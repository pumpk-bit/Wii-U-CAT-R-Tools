#include <stdint.h>
#include <wafel/ios_dynamic.h>
#include <wafel/trampoline.h>
#include <wafel/utils.h>

/*
 * V12 = V11 plus bigger socket buffers on the PCFS listening socket.
 *
 * Stock PCFS creates its socket with the default buffers, so the console
 * advertises an 8192-byte TCP receive window and a PC sending file data has
 * to stop every 8 KB to wait for an ACK. V12 replaces the listen() call in
 * the PCFS accept routine:
 *   10730780  TCP_SM_Socket(port): socket(2,1,6) 107EBE38, bind 107EC330
 *   10730820  listen/accept: 10730898 bl listen(fd, 1) 107EC238  <- V12 wraps
 *             107308B8 accept 107EC438, then close(listen fd)
 *   107EBB9C  setsockopt(fd, level, opt, val, len): socket ioctl 9; level
 *             must be -1 (SOL_SOCKET), 0 or 6. Stock code only sets
 *             TCP_NODELAY (6, 0x2004) on the connection (1072FB68).
 * Before listen it sets SO_WINSCALE, then SO_RCVBUF and SO_SNDBUF, trying
 * smaller sizes when the network stack refuses one. The accepted connection
 * inherits them, and the SYN-ACK already carries the window scale.
 *
 * V11 = V10 (config gate override + native TCP selector) plus a bounded retry
 * of TCP_SM_Init for the boot-time race with the NET module.
 *
 * Race (IOS-FS, image BB4A157D...C5D0):
 *   1073039C  bl transport_selector      -> V10 returns 3 (TCP)
 *   107304C4  native TCP branch
 *   107304EC  bl TCP_SM_Init (107309A0)  -> V11 wraps this call
 *   107309A0  TCP_SM_Init: bl net_wait (1073092C), on 0 return -1
 *   1073092C  net_wait: bl ncl_open (107EC6D8); nonzero -> return 0 at once
 *   107EC6D8  ncl_open: IOS_Open("/dev/net/ifmgr/ncl"); caches fd at 108345A4
 *             (initial value -1) only when the open succeeds.
 * Before NET registers /dev/net/ifmgr/ncl the open fails silently and
 * TCP_SM_Init returns -1 in a few ms ("__DKT_OpenTCP: TCP_SM_Init() failed").
 * Once the open works, net_wait itself polls the link for up to ~24 s.
 *
 * The retry only repeats while the cached ncl fd is still -1, i.e. only for
 * the "device not registered yet" failure. Any other failure (link wait
 * timeout, SOShim socket open) is passed through unchanged, exactly as V10.
 */
#define COOKIE_LOG_CALL 0x1072b168u
#define COOKIE_LOG_STOCK 0xeb0316c5u
#define SELECTOR_CALL 0x1073039cu
#define SELECTOR_STOCK 0xebffff10u
#define INIT_ENTRY 0x1072c304u
#define INIT_STOCK 0xe92d47f0u
#define CONFIG_RESULT 0x1072c46cu
#define CONFIG_STOCK 0xe59d3070u

/* New in V11 */
#define TCP_INIT_CALL 0x107304ecu   /* bl 0x107309a0 */
#define TCP_INIT_CALL_STOCK 0xeb00012bu
#define TCP_INIT_FN 0x107309a0u     /* push {lr} */
#define TCP_INIT_FN_STOCK 0xe52de004u
#define NCL_OPEN_FN 0x107ec6d8u     /* push {r4, r5, lr} */
#define NCL_OPEN_FN_STOCK 0xe92d4030u
#define NCL_OPEN_FD_LIT 0x107ec720u /* literal pool word = &ncl_fd */
#define NCL_FD 0x108345a4u
#define USLEEP_FN 0x107f64f8u       /* FS usleep(us): queue + timer + wait */
#define USLEEP_FN_STOCK 0xe92d4030u

/* New in V12 */
#define LISTEN_CALL 0x10730898u     /* bl 0x107ec238 (listen) */
#define LISTEN_CALL_STOCK 0xeb02ee66u
#define LISTEN_FN 0x107ec238u
#define SETSOCKOPT_FN 0x107ebb9cu
#define SOSHIM_FN_STOCK 0xe92d4ff0u /* push {r4-fp, lr}, both functions */
#define SETSOCKOPT_WRAPPER 0x10730750u /* b 0x107ebb9c */
#define SETSOCKOPT_WRAPPER_STOCK 0xea02ed11u

#define SOL_SOCKET_LEVEL (-1)
#define SO_SNDBUF_OPT 0x1001
#define SO_RCVBUF_OPT 0x1002
#define SO_WINSCALE_OPT 0x0400

typedef int (*fs_setsockopt_fn)(int, int, int, const void *, int);
#define FS_SETSOCKOPT ((fs_setsockopt_fn)SETSOCKOPT_FN)

#define TCP_INIT_RETRY_US 250000u   /* 250 ms between attempts */
#define TCP_INIT_MAX_ATTEMPTS 25u   /* first try + 24 retries = ~6 s */

typedef int (*fs_log_fn)(const char *, ...);
#define FS_LOG ((fs_log_fn)0x107f0c84u)
typedef int (*fs_usleep_fn)(uint32_t);
#define FS_USLEEP ((fs_usleep_fn)USLEEP_FN)

int enable_debug_printf;

static void cookie_log(const char *format, uint32_t cookie,
                       uint32_t r2, uint32_t r3,
                       void (*original)(const char *, ...),
                       const void *saved_lr)
{
    (void)format;
    (void)r2;
    (void)r3;
    (void)saved_lr;
    /* Runs inside FS: use its existing logger and virtual code mapping. */
    original("PCFSTCP[V12]: cookie=%u selector_word=%08x (TCP enable + init retry + buffers)\n",
             cookie, *(volatile uint32_t *)SELECTOR_CALL);
}

static bool observe_init(trampoline_state *regs)
{
    FS_LOG("PCFSTCP[V12]: init entry state=%08x flags=%08x lr=%08x\n",
           regs->r[0], regs->r[1], regs->lr);
    return false;
}

static bool observe_config(trampoline_state *regs)
{
    /* trampoline_state.stack is the original SP at the intercepted site. */
    FS_LOG("PCFSTCP[V12]: config enable=%08x last_result=%08x\n",
           regs->stack[0x70 / 4], regs->r[0]);
    regs->stack[0x70 / 4] = 1;
    FS_LOG("PCFSTCP[V12]: config enable overridden to 1 in RAM\n");
    return false;
}

static int observe_selector(void *state, int flags, uint32_t r2, uint32_t r3,
                            int (*original)(void *, int), const void *lr)
{
    (void)r2; (void)r3;
    FS_LOG("PCFSTCP[V12]: selector enter state=%p flags=%08x lr=%p\n", state, flags, lr);
    (void)original;
    if (!state) {
        FS_LOG("PCFSTCP[V12]: null selector state; refusing TCP\n");
        return 0;
    }
    volatile uint8_t *bytes = (volatile uint8_t *)state;
    *(volatile uint32_t *)(bytes + 0x58c) = 7500;
    *(volatile uint32_t *)(bytes + 0xdb4) = 0x0000ff00u;
    *(volatile uint32_t *)(bytes + 0xdb8) = 0x0000f7e0u;
    FS_LOG("PCFSTCP[V12]: selected native TCP transport=3 port=7500\n");
    return 3;
}

/* Replaces the BL at 0x107304EC. TCP_SM_Init takes no arguments and returns
 * 0 on success; r0-r3 are passed through untouched anyway. */
static int retry_tcp_init(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3,
                          int (*original)(uint32_t, uint32_t, uint32_t, uint32_t),
                          const void *lr)
{
    (void)lr;
    uint32_t attempts = 1;
    int err = original(r0, r1, r2, r3);

    while (err != 0 && attempts < TCP_INIT_MAX_ATTEMPTS &&
           *(volatile int32_t *)NCL_FD < 0) {
        FS_USLEEP(TCP_INIT_RETRY_US);
        attempts++;
        err = original(r0, r1, r2, r3);
    }

    FS_LOG("PCFSTCP[V12]: TCP_SM_Init result=%d attempts=%u retries=%u ncl_fd=%d\n",
           err, attempts, attempts - 1, *(volatile int32_t *)NCL_FD);
    return err;
}

static const int buffer_sizes[] = { 0x20000, 0x10000, 0x8000, 0x4000 };

/* Sets one buffer option to the largest size the stack accepts; returns it,
 * or the last error when none is accepted. */
static int set_buffer(int fd, int opt)
{
    int err = 0;
    for (unsigned i = 0; i < sizeof(buffer_sizes) / sizeof(buffer_sizes[0]); i++) {
        int size = buffer_sizes[i];
        err = FS_SETSOCKOPT(fd, SOL_SOCKET_LEVEL, opt, &size, sizeof(size));
        if (err >= 0)
            return size;
    }
    return err;
}

/* Replaces the BL at 0x10730898: listen(fd, backlog) on the PCFS socket. */
static int bigger_buffers_listen(int fd, int backlog, uint32_t r2, uint32_t r3,
                                 int (*original)(int, int, uint32_t, uint32_t),
                                 const void *lr)
{
    (void)lr;
    int on = 1;
    int scale = FS_SETSOCKOPT(fd, SOL_SOCKET_LEVEL, SO_WINSCALE_OPT, &on, sizeof(on));
    int rcv = set_buffer(fd, SO_RCVBUF_OPT);
    int snd = set_buffer(fd, SO_SNDBUF_OPT);
    FS_LOG("PCFSTCP[V12]: socket %d winscale=%d rcvbuf=%d sndbuf=%d\n",
           fd, scale, rcv, snd);
    return original(fd, backlog, r2, r3);
}

static uint32_t stock_word(uintptr_t vaddr)
{
    return *(volatile uint32_t *)ios_elf_vaddr_to_paddr(vaddr);
}

__attribute__((target("arm")))
void kern_main(void)
{
    uint32_t log_word = stock_word(COOKIE_LOG_CALL);
    uint32_t selector_word = stock_word(SELECTOR_CALL);

    debug_printf("PCFSTCP[V12]: log=%08x selector=%08x\n", log_word, selector_word);
    if (log_word != COOKIE_LOG_STOCK || selector_word != SELECTOR_STOCK ||
        stock_word(INIT_ENTRY) != INIT_STOCK ||
        stock_word(CONFIG_RESULT) != CONFIG_STOCK ||
        stock_word(TCP_INIT_CALL) != TCP_INIT_CALL_STOCK ||
        stock_word(TCP_INIT_FN) != TCP_INIT_FN_STOCK ||
        stock_word(NCL_OPEN_FN) != NCL_OPEN_FN_STOCK ||
        stock_word(NCL_OPEN_FD_LIT) != NCL_FD ||
        stock_word(USLEEP_FN) != USLEEP_FN_STOCK ||
        stock_word(LISTEN_CALL) != LISTEN_CALL_STOCK ||
        stock_word(LISTEN_FN) != SOSHIM_FN_STOCK ||
        stock_word(SETSOCKOPT_FN) != SOSHIM_FN_STOCK ||
        stock_word(SETSOCKOPT_WRAPPER) != SETSOCKOPT_WRAPPER_STOCK) {
        debug_printf("PCFSTCP[V12]: signature mismatch; no patch applied\n");
        return;
    }
    trampoline_blreplace(COOKIE_LOG_CALL, cookie_log);
    trampoline_hook_before_v2(INIT_ENTRY, observe_init);
    trampoline_hook_before_v2(CONFIG_RESULT, observe_config);
    trampoline_blreplace(SELECTOR_CALL, observe_selector);
    trampoline_blreplace(TCP_INIT_CALL, retry_tcp_init);
    trampoline_blreplace(LISTEN_CALL, bigger_buffers_listen);
}

void mcp_main(void)
{
}
