#include "dk_sock.h"

#include <string.h>
#include <wafel/ios/memory.h>
#include <wafel/ios/svc.h>
#include <wafel/ios/thread.h>

#define AF_INET 2
#define SOCK_STREAM 1
#define IPPROTO_TCP 6

struct dk_in_addr {
    uint32_t s_addr;
};

struct sockaddr_in {
    uint16_t sin_family;
    uint16_t sin_port;
    struct dk_in_addr sin_addr;
    uint8_t sin_zero[8];
};

/*
 * Private copy of the WUP server's socket wrappers. The shared wafel socket()
 * keeps one /dev/socket fd for the whole core, and the MCP WUP server already
 * owns that fd. Opening a second handle here leaves port 1337 alone.
 */
#define DK_PORT 7332
#define SOCK_HEAP 0xCAFF

static int sock_dev = -1;
static volatile int client_fd = -1;

/* Shared with the FS hook. The plugin mapping is writable from both
 * processes. phase: 0 not started, 1 waiting for /dev/socket, 2 listening,
 * 3 a PC is connected. */
#define MB_IDLE 0u
#define MB_REQ  1u
#define MB_DONE 2u
#define MB_MAX  0x12000u

static volatile int mcp_thr = -100;
static volatile int phase;
static volatile uint32_t mb_state;
static volatile uint32_t mb_req_len;
static volatile uint32_t mb_resp_len;
static volatile int mb_result;
static uint8_t mb_req[MB_MAX];
static uint8_t mb_resp[MB_MAX];
static int logged_thr = -999;
static int logged_phase = -1;

static void mem_barrier(void)
{
    __asm__ volatile("" ::: "memory");
}

typedef int (*fs_log_fn)(const char *, ...);
#define FS_LOG ((fs_log_fn)0x107f0c84u)

static void *alloc_iobuf(uint32_t size)
{
    void *ptr = iosAlloc(SOCK_HEAP, size);
    if (ptr)
        memset(ptr, 0, size);
    return ptr;
}

static int dk_socket(int domain, int type, int protocol)
{
    uint32_t *inbuf = alloc_iobuf(0xC);
    int ret;
    if (!inbuf)
        return -100;
    inbuf[0] = domain;
    inbuf[1] = type;
    inbuf[2] = protocol;
    ret = iosIoctl(sock_dev, 0x11, inbuf, 0xC, NULL, 0);
    iosFree(SOCK_HEAP, inbuf);
    return ret;
}

static int dk_close(int sockfd)
{
    uint32_t *inbuf = alloc_iobuf(4);
    int ret;
    if (!inbuf)
        return -100;
    inbuf[0] = sockfd;
    ret = iosIoctl(sock_dev, 0x3, inbuf, 4, NULL, 0);
    iosFree(SOCK_HEAP, inbuf);
    return ret;
}

static int dk_accept(int sockfd)
{
    uint8_t *iobuf = alloc_iobuf(0x18);
    uint32_t *inbuf;
    int ret;
    if (!iobuf)
        return -100;
    inbuf = (uint32_t *)iobuf;
    inbuf[0] = sockfd;
    inbuf[5] = 0x10;
    ret = iosIoctl(sock_dev, 0x1, inbuf, 0x18, inbuf, 0x18);
    iosFree(SOCK_HEAP, iobuf);
    return ret;
}

static int dk_bind(int sockfd, const struct sockaddr_in *addr)
{
    uint8_t *iobuf = alloc_iobuf(0x18);
    uint32_t *inbuf;
    int ret;
    if (!iobuf)
        return -100;
    inbuf = (uint32_t *)iobuf;
    inbuf[0] = sockfd;
    memcpy(&inbuf[1], addr, 0x10);
    inbuf[5] = 0x10;
    ret = iosIoctl(sock_dev, 0x2, inbuf, 0x18, NULL, 0);
    iosFree(SOCK_HEAP, iobuf);
    return ret;
}

static int dk_listen(int sockfd, int backlog)
{
    uint32_t *inbuf = alloc_iobuf(8);
    int ret;
    if (!inbuf)
        return -100;
    inbuf[0] = sockfd;
    inbuf[1] = backlog;
    ret = iosIoctl(sock_dev, 0xA, inbuf, 8, NULL, 0);
    iosFree(SOCK_HEAP, inbuf);
    return ret;
}

static int dk_recv(int sockfd, void *buf, uint32_t len)
{
    void *data_buf;
    uint8_t *iobuf;
    iovec_s *iovec;
    uint32_t *inbuf;
    int ret;
    if (!len)
        return -101;
    data_buf = iosAllocAligned(SOCK_HEAP, len, 0x40);
    if (!data_buf)
        return -100;
    iobuf = alloc_iobuf(0x38);
    if (!iobuf) {
        iosFree(SOCK_HEAP, data_buf);
        return -100;
    }
    iovec = (iovec_s *)iobuf;
    inbuf = (uint32_t *)&iobuf[0x30];
    inbuf[0] = sockfd;
    inbuf[1] = 0;
    iovec[0].ptr = inbuf;
    iovec[0].len = 8;
    iovec[1].ptr = data_buf;
    iovec[1].len = len;
    ret = iosIoctlv(sock_dev, 0xC, 1, 3, iovec);
    if (ret > 0 && buf)
        memcpy(buf, data_buf, (uint32_t)ret < len ? (uint32_t)ret : len);
    iosFree(SOCK_HEAP, data_buf);
    iosFree(SOCK_HEAP, iobuf);
    return ret;
}

static int dk_send(int sockfd, const void *buf, uint32_t len)
{
    void *data_buf;
    uint8_t *iobuf;
    iovec_s *iovec;
    uint32_t *inbuf;
    int ret;
    if (!buf || !len)
        return -101;
    data_buf = iosAllocAligned(SOCK_HEAP, len, 0x40);
    if (!data_buf)
        return -100;
    iobuf = alloc_iobuf(0x38);
    if (!iobuf) {
        iosFree(SOCK_HEAP, data_buf);
        return -100;
    }
    memcpy(data_buf, buf, len);
    iovec = (iovec_s *)iobuf;
    inbuf = (uint32_t *)&iobuf[0x30];
    inbuf[0] = sockfd;
    inbuf[1] = 0;
    iovec[0].ptr = inbuf;
    iovec[0].len = 8;
    iovec[1].ptr = data_buf;
    iovec[1].len = len;
    ret = iosIoctlv(sock_dev, 0xE, 4, 0, iovec);
    iosFree(SOCK_HEAP, data_buf);
    iosFree(SOCK_HEAP, iobuf);
    return ret;
}

static int send_all(int fd, const uint8_t *buf, uint32_t len)
{
    uint32_t off = 0;
    while (off < len) {
        int n = dk_send(fd, buf + off, len - off);
        if (n <= 0)
            return -1;
        off += (uint32_t)n;
    }
    return 0;
}

static int recv_all(int fd, uint8_t *buf, uint32_t len)
{
    uint32_t off = 0;
    while (off < len) {
        int n = dk_recv(fd, buf + off, len - off);
        if (n <= 0)
            return -1;
        off += (uint32_t)n;
    }
    return 0;
}

static uint32_t read_be32(const uint8_t *p);

static void finish_request(int result, uint32_t resp_len)
{
    mb_result = result;
    mb_resp_len = resp_len;
    mem_barrier();
    mb_state = MB_DONE;
}

static void service_request(int fd)
{
    uint32_t req_len = mb_req_len;
    uint32_t total;
    mem_barrier();
    if (req_len == 0 || req_len > MB_MAX ||
        send_all(fd, mb_req, req_len) < 0 ||
        recv_all(fd, mb_resp, 8) < 0) {
        client_fd = -1;
        finish_request(-1, 0);
        return;
    }
    total = read_be32(mb_resp + 4);
    if (total < 8 || total > MB_MAX ||
        (total > 8 && recv_all(fd, mb_resp + 8, total - 8) < 0)) {
        client_fd = -1;
        finish_request(-1, 0);
        return;
    }
    finish_request(0, total);
}

static uint32_t listen_thread(void *arg)
{
    uint32_t backoff_us = 500000;
    (void)arg;
    phase = 1;
    for (;;) {
        int dev = iosOpen("/dev/socket", 0);
        if (dev >= 0) {
            sock_dev = dev;
            break;
        }
        usleep(500000);
    }

    for (;;) {
        struct sockaddr_in addr;
        int srv;
        memset(&addr, 0, sizeof(addr));
        addr.sin_family = AF_INET;
        addr.sin_port = DK_PORT;
        addr.sin_addr.s_addr = 0;
        srv = dk_socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (srv < 0) {
            usleep(backoff_us);
            if (backoff_us < 5000000)
                backoff_us *= 2;
            continue;
        }
        if (dk_bind(srv, &addr) < 0 || dk_listen(srv, 1) < 0) {
            dk_close(srv);
            usleep(backoff_us);
            if (backoff_us < 5000000)
                backoff_us *= 2;
            continue;
        }
        phase = 2;
        for (;;) {
            int client = dk_accept(srv);
            if (client < 0) {
                /* The listening socket is dead (NET kills every socket
                 * before a restart). Retrying accept on it every 200 ms
                 * filled IOS-NET's 64 transactions and stalled the
                 * restart. Drop it, back off, and start over. */
                dk_close(srv);
                srv = -1;
                usleep(backoff_us);
                if (backoff_us < 5000000)
                    backoff_us *= 2;
                break;
            }
            backoff_us = 500000;
            client_fd = client;
            phase = 3;
            while (client_fd == client) {
                if (mb_state == MB_REQ)
                    service_request(client);
                else
                    usleep(1000);
            }
            dk_close(client);
            client_fd = -1;
            phase = 2;
            if (mb_state == MB_REQ)
                finish_request(-1, 0);
        }
    }
    return 0;
}

/* iosCreateThread from the FS hook returns -4 for every heap. The WUP server
 * creates its thread from mcp_main, on heap 1, and that succeeds. */
int dk_net_start(void)
{
    u8 *stack = (u8 *)malloc_global(0x2000);
    int thr;
    if (!stack) {
        mcp_thr = -1;
        return -1;
    }
    thr = iosCreateThread(listen_thread, NULL, (u32 *)(stack + 0x2000), 0x2000, 0x78, 1);
    mcp_thr = thr;
    if (thr < 0)
        return thr;
    iosStartThread(thr);
    return 0;
}

void dk_net_log_fs(void)
{
    int thr = mcp_thr;
    int ph = phase;
    if (thr == logged_thr && ph == logged_phase)
        return;
    logged_thr = thr;
    logged_phase = ph;
    FS_LOG("DKTCP: mcp thr %d phase %d\n", thr, ph);
}

static uint32_t read_be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

int dk_net_exchange(const void *req, uint32_t req_len,
                    void *resp, uint32_t resp_cap, uint32_t *resp_len)
{
    uint32_t i;
    if (phase != 3 || resp_cap < 8 || req_len == 0 || req_len > MB_MAX)
        return -1;
    if (mb_state == MB_DONE)
        mb_state = MB_IDLE;
    if (mb_state != MB_IDLE)
        return -1;
    memcpy(mb_req, req, req_len);
    mb_req_len = req_len;
    mem_barrier();
    mb_state = MB_REQ;
    for (i = 0; i < 3000 && mb_state == MB_REQ; i++)
        usleep(1000);
    if (mb_state != MB_DONE) {
        mb_state = MB_IDLE;
        return -1;
    }
    mem_barrier();
    if (mb_result != 0 || mb_resp_len > resp_cap) {
        mb_state = MB_IDLE;
        return -1;
    }
    memcpy(resp, mb_resp, mb_resp_len);
    *resp_len = mb_resp_len;
    mem_barrier();
    mb_state = MB_IDLE;
    return 0;
}
