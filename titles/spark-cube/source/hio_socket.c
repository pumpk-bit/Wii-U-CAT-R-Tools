/*
 * hio_socket.c - the coreinit HIO API over ordinary PPC sockets.
 *
 * On a CAT-DEV, HIO channels run through /dev/dk, SDIO and the MION to the
 * PC's HostBridge. A CAT-R has no MION, and coreinit's HIOInit stops at the
 * headless check, so gx2spark's "SparkCapture" channel never opens. This file
 * implements the same API under the names HSKInit, HSKOpenEx, ... . The SDK's
 * coreinit import library defines all HIO functions in one member, so the
 * names can't simply be overridden. Instead scripts/Build.ps1 extracts
 * gx2spark's commsManager.o (the only member that calls HIO) and renames its
 * HIOInit, HIOOpenEx, HIORead, HIOWrite and HIOClose references in place in
 * the string table (same length: HIO -> HSK), and the Makefile links that copy
 * ahead of gx2spark.a. The stock archive and every other title are untouched.
 *
 * Wire: the console listens on TCP HIO_SOCKET_PORT. The PC (catr hio)
 * connects and sends one line, the channel name ("SparkCapture\n"). The
 * console answers "+\n" and from then on the socket is that channel's raw
 * byte stream, exactly what HIORead/HIOWrite carry on real hardware. "-\n"
 * means no channel of that name is open; "?\n" lists the open channels
 * ("+Name\n" each, then ".\n") and closes. A new connection for a channel
 * replaces the old one.
 *
 * Differences from coreinit:
 *  - Connection callbacks (HIO_STATUS_NEW_CONNECTION, NO_CONNECTIONS) are
 *    called directly on the listener/reader thread, not through the
 *    HIOAsyncStruct's message queue.
 *  - HIOReadAsyncEx/HIOWriteAsyncEx do the transfer synchronously, then
 *    call the callback.
 *  - HIORead with no PC connected waits up to a second and then returns
 *    HIO_STATUS_NO_CLIENT_TXN_BUF_AVAILABLE (-5); gx2spark's read thread
 *    sleeps 100 ms and retries on that status.
 */
#include <cafe.h>
#include <cafe/hio.h>
#include <cafe/network.h>
#include <nn/ac.h>
#include <string.h>

#define HIO_SOCKET_PORT 6100

HIOStatus HSKInit(void);
HIOStatus HSKShutdown(void);
HIOStatus HSKQueryHeadlessMode(void);
HIOHandle HSKOpenEx(const char *hioName, HIOAsyncStruct *asyncStruct,
                         HIOChannelOptions options, HIOChannelFlags flags);
HIOStatus HSKClose(HIOHandle handle);
HIOStatus HSKRead(HIOHandle handle, u32 sizeInBytes, void *pData);
HIOStatus HSKWrite(HIOHandle handle, u32 sizeInBytes, const void *pData);
HIOStatus HSKReadAsyncEx(HIOHandle handle, u32 sizeInBytes, void *pData,
                              HIOAsyncStruct *asyncStruct);
HIOStatus HSKWriteAsyncEx(HIOHandle handle, u32 sizeInBytes, const void *pData,
                               HIOAsyncStruct *asyncStruct);
#define HIO_MAX_CHANNELS 4
#define HIO_NAME_MAX 64
#define LISTENER_STACK (16 * 1024)

typedef struct {
    BOOL used;
    char name[HIO_NAME_MAX];
    HIOAsyncStruct async;      /* connection callback, copied as coreinit does */
    int sock;                  /* -1 while no PC is connected */
    OSEvent connected;         /* manual: signalled while sock >= 0 */
    OSMutex write_lock;        /* one HIOWrite at a time */
} HioChannel;

static BOOL s_initialized = FALSE;
static OSMutex s_lock;
static HioChannel s_channels[HIO_MAX_CHANNELS];
static OSThread s_listener;
static u64 s_listener_stack[LISTENER_STACK / sizeof(u64)];  /* 8-byte aligned */
static int s_listen_sock = -1;

static void notify(HioChannel *ch, HIOStatus status)
{
    if (ch->async.callback)
        ch->async.callback(status, ch->async.userData);
}

static BOOL send_all(int sock, const void *data, u32 size)
{
    const u8 *p = (const u8 *)data;
    while (size > 0) {
        int n = send(sock, p, (int)size, 0);
        if (n <= 0)
            return FALSE;
        p += n;
        size -= (u32)n;
    }
    return TRUE;
}

/* Drops the connection if it is still the one that failed. */
static void drop(HioChannel *ch, int sock)
{
    BOOL dropped = FALSE;
    OSLockMutex(&s_lock);
    if (ch->used && ch->sock == sock && sock >= 0) {
        ch->sock = -1;
        OSResetEvent(&ch->connected);
        dropped = TRUE;
    }
    OSUnlockMutex(&s_lock);
    if (dropped) {
        socketclose(sock);
        OSReport("HIO-socket: %s disconnected\n", ch->name);
        notify(ch, HIO_STATUS_NO_CONNECTIONS);
    }
}

static int read_line(int sock, char *buf, int size)
{
    int len = 0;
    while (len < size - 1) {
        char c;
        if (recv(sock, &c, 1, 0) != 1)
            return -1;
        if (c == '\n')
            break;
        if (c != '\r')
            buf[len++] = c;
    }
    buf[len] = '\0';
    return len;
}

static void serve_client(int sock)
{
    char name[HIO_NAME_MAX];
    HioChannel *ch = NULL;
    int old = -1;

    if (read_line(sock, name, sizeof name) < 0) {
        socketclose(sock);
        return;
    }
    if (strcmp(name, "?") == 0) {
        char line[HIO_NAME_MAX + 4];
        OSLockMutex(&s_lock);
        for (int i = 0; i < HIO_MAX_CHANNELS; i++) {
            if (!s_channels[i].used)
                continue;
            line[0] = s_channels[i].sock >= 0 ? '*' : '+';
            strcpy(line + 1, s_channels[i].name);
            strcat(line, "\n");
            send_all(sock, line, strlen(line));
        }
        OSUnlockMutex(&s_lock);
        send_all(sock, ".\n", 2);
        socketclose(sock);
        return;
    }

    OSLockMutex(&s_lock);
    for (int i = 0; i < HIO_MAX_CHANNELS; i++) {
        if (s_channels[i].used && strcmp(s_channels[i].name, name) == 0) {
            ch = &s_channels[i];
            break;
        }
    }
    OSUnlockMutex(&s_lock);

    if (!ch) {
        send_all(sock, "-\n", 2);
        socketclose(sock);
        return;
    }
    if (!send_all(sock, "+\n", 2)) {
        socketclose(sock);
        return;
    }

    OSLockMutex(&s_lock);
    old = ch->sock;
    ch->sock = sock;
    OSSignalEvent(&ch->connected);
    OSUnlockMutex(&s_lock);

    if (old >= 0)
        socketclose(old);   /* the old reader sees an error and returns */
    OSReport("HIO-socket: %s connected%s\n", ch->name, old >= 0 ? " (replaced)" : "");
    notify(ch, HIO_STATUS_NEW_CONNECTION);
}

static int listener_main(int argc, void *argv)
{
    struct sockaddr_in addr;
    int one = 1;
    (void)argc;
    (void)argv;

    s_listen_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s_listen_sock < 0) {
        OSReport("HIO-socket: socket() failed, err=%d\n", socketlasterr());
        return 0;
    }
    setsockopt(s_listen_sock, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons(HIO_SOCKET_PORT);
    addr.sin_addr.s_addr = INADDR_ANY;
    if (bind(s_listen_sock, (struct sockaddr *)&addr, sizeof addr) < 0 ||
        listen(s_listen_sock, 2) < 0) {
        OSReport("HIO-socket: bind/listen on %d failed, err=%d\n",
                 HIO_SOCKET_PORT, socketlasterr());
        socketclose(s_listen_sock);
        s_listen_sock = -1;
        return 0;
    }
    OSReport("HIO-socket: listening on TCP %d\n", HIO_SOCKET_PORT);

    while (s_initialized) {
        int len = sizeof addr;
        int sock = accept(s_listen_sock, (struct sockaddr *)&addr, &len);
        if (sock < 0) {
            if (!s_initialized)
                break;
            OSSleepMilliseconds(200);
            continue;
        }
        serve_client(sock);
    }
    return 0;
}

HIOStatus HSKInit(void)
{
    ACResult ac;

    if (s_initialized)
        return HIO_STATUS_OK;

    OSInitMutex(&s_lock);
    memset(s_channels, 0, sizeof s_channels);
    for (int i = 0; i < HIO_MAX_CHANNELS; i++)
        s_channels[i].sock = -1;

    socket_lib_init();
    ac = ACInitialize();
    if (ACIsSuccess(ac))
        ac = ACConnect();
    if (!ACIsSuccess(ac))
        OSReport("HIO-socket: ACConnect failed (0x%08X); trying sockets anyway\n", (u32)ac.value);

    s_initialized = TRUE;
    if (!OSCreateThread(&s_listener, listener_main, 0, NULL,
                        (u8 *)s_listener_stack + LISTENER_STACK, LISTENER_STACK,
                        16, OS_THREAD_ATTR_DETACH)) {
        OSReport("HIO-socket: listener thread failed\n");
        s_initialized = FALSE;
        return HIO_STATUS_OUT_OF_MEMORY;
    }
    OSSetThreadName(&s_listener, "HIO-socket listener");
    OSResumeThread(&s_listener);
    OSReport("HIO-socket: HIOInit (replaces coreinit HIO for this title)\n");
    return HIO_STATUS_OK;
}

HIOStatus HSKShutdown(void)
{
    if (!s_initialized)
        return HIO_STATUS_NOT_INITIALIZED;
    for (int i = 0; i < HIO_MAX_CHANNELS; i++)
        if (s_channels[i].used)
            HSKClose(i);
    s_initialized = FALSE;
    if (s_listen_sock >= 0) {
        socketclose(s_listen_sock);
        s_listen_sock = -1;
    }
    return HIO_STATUS_OK;
}

HIOStatus HSKQueryHeadlessMode(void)
{
    return 0;   /* not headless: this transport works without a MION */
}

HIOHandle HSKOpenEx(const char *hioName, HIOAsyncStruct *asyncStruct,
                    HIOChannelOptions options, HIOChannelFlags flags)
{
    HIOHandle handle = HIO_HANDLE_INVALID;
    (void)options;
    (void)flags;

    if (!s_initialized)
        return HIO_STATUS_NOT_INITIALIZED;
    if (!hioName || !hioName[0] || strlen(hioName) >= HIO_NAME_MAX ||
        strchr(hioName, '\n'))
        return HIO_STATUS_INVALID_CHANNEL_NAME;

    OSLockMutex(&s_lock);
    for (int i = 0; i < HIO_MAX_CHANNELS; i++) {
        if (s_channels[i].used && strcmp(s_channels[i].name, hioName) == 0) {
            OSUnlockMutex(&s_lock);
            return HIO_STATUS_ALREADY_OPEN;
        }
    }
    for (int i = 0; i < HIO_MAX_CHANNELS; i++) {
        HioChannel *ch = &s_channels[i];
        if (ch->used)
            continue;
        memset(ch, 0, sizeof *ch);
        strcpy(ch->name, hioName);
        if (asyncStruct)
            ch->async = *asyncStruct;
        ch->sock = -1;
        OSInitEvent(&ch->connected, FALSE, OS_EVENT_MANUAL);
        OSInitMutex(&ch->write_lock);
        ch->used = TRUE;
        handle = i;
        break;
    }
    OSUnlockMutex(&s_lock);

    if (handle == HIO_HANDLE_INVALID)
        return HIO_STATUS_NO_CHANNELS;
    OSReport("HIO-socket: opened channel %s (handle %d)\n", hioName, handle);
    return handle;
}

static HioChannel *channel(HIOHandle handle)
{
    if (handle < 0 || handle >= HIO_MAX_CHANNELS || !s_channels[handle].used)
        return NULL;
    return &s_channels[handle];
}

HIOStatus HSKClose(HIOHandle handle)
{
    HioChannel *ch;
    int sock;

    if (!s_initialized)
        return HIO_STATUS_NOT_INITIALIZED;
    ch = channel(handle);
    if (!ch)
        return HIO_STATUS_INVALID_HANDLE;
    OSLockMutex(&s_lock);
    sock = ch->sock;
    ch->sock = -1;
    ch->used = FALSE;
    OSSignalEvent(&ch->connected);   /* wake a waiting reader; it sees !used */
    OSUnlockMutex(&s_lock);
    if (sock >= 0)
        socketclose(sock);
    return HIO_STATUS_OK;
}

HIOStatus HSKRead(HIOHandle handle, u32 sizeInBytes, void *pData)
{
    HioChannel *ch;
    int sock;
    u32 got = 0;

    if (!s_initialized)
        return HIO_STATUS_NOT_INITIALIZED;
    ch = channel(handle);
    if (!ch)
        return HIO_STATUS_INVALID_HANDLE;
    if (sizeInBytes == 0)
        return 0;
    if (!pData)
        return HIO_STATUS_INVALID_PARAM;

    if (!OSWaitEventWithTimeout(&ch->connected, OSMillisecondsToNanoseconds(1000)))
        return HIO_STATUS_NO_CLIENT_TXN_BUF_AVAILABLE;
    OSLockMutex(&s_lock);
    sock = ch->used ? ch->sock : -1;
    OSUnlockMutex(&s_lock);
    if (sock < 0)
        return ch->used ? HIO_STATUS_NO_CLIENT_TXN_BUF_AVAILABLE : HIO_STATUS_INVALID_HANDLE;

    while (got < sizeInBytes) {
        int n = recv(sock, (u8 *)pData + got, (int)(sizeInBytes - got), 0);
        if (n <= 0) {
            drop(ch, sock);
            /* Nothing consumed yet: let the caller retry on the next
             * connection. Mid-message: the message is lost either way. */
            return HIO_STATUS_NO_CLIENT_TXN_BUF_AVAILABLE;
        }
        got += (u32)n;
    }
    return (HIOStatus)got;
}

HIOStatus HSKWrite(HIOHandle handle, u32 sizeInBytes, const void *pData)
{
    HioChannel *ch;
    int sock;
    BOOL ok;

    if (!s_initialized)
        return HIO_STATUS_NOT_INITIALIZED;
    ch = channel(handle);
    if (!ch)
        return HIO_STATUS_INVALID_HANDLE;
    if (sizeInBytes == 0)
        return 0;
    if (!pData)
        return HIO_STATUS_INVALID_PARAM;

    OSLockMutex(&s_lock);
    sock = ch->sock;
    OSUnlockMutex(&s_lock);
    if (sock < 0)
        return HIO_STATUS_NO_CONNECTIONS;

    OSLockMutex(&ch->write_lock);
    ok = send_all(sock, pData, sizeInBytes);
    OSUnlockMutex(&ch->write_lock);
    if (!ok) {
        drop(ch, sock);
        return HIO_STATUS_NO_CONNECTIONS;
    }
    return (HIOStatus)sizeInBytes;
}

HIOStatus HSKReadAsyncEx(HIOHandle handle, u32 sizeInBytes, void *pData,
                         HIOAsyncStruct *asyncStruct)
{
    HIOStatus status = HSKRead(handle, sizeInBytes, pData);
    if (asyncStruct && asyncStruct->callback)
        asyncStruct->callback(status, asyncStruct->userData);
    return status < 0 ? status : HIO_STATUS_OK;
}

HIOStatus HSKWriteAsyncEx(HIOHandle handle, u32 sizeInBytes, const void *pData,
                          HIOAsyncStruct *asyncStruct)
{
    HIOStatus status = HSKWrite(handle, sizeInBytes, pData);
    if (asyncStruct && asyncStruct->callback)
        asyncStruct->callback(status, asyncStruct->userData);
    return status < 0 ? status : HIO_STATUS_OK;
}
