#include <stdint.h>
#include <wafel/ios_dynamic.h>
#include <wafel/trampoline.h>
#include <wafel/utils.h>

/*
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
    original("PCFSTCP[V11]: cookie=%u selector_word=%08x (TCP enable + init retry)\n",
             cookie, *(volatile uint32_t *)SELECTOR_CALL);
}

static bool observe_init(trampoline_state *regs)
{
    FS_LOG("PCFSTCP[V11]: init entry state=%08x flags=%08x lr=%08x\n",
           regs->r[0], regs->r[1], regs->lr);
    return false;
}

static bool observe_config(trampoline_state *regs)
{
    /* trampoline_state.stack is the original SP at the intercepted site. */
    FS_LOG("PCFSTCP[V11]: config enable=%08x last_result=%08x\n",
           regs->stack[0x70 / 4], regs->r[0]);
    regs->stack[0x70 / 4] = 1;
    FS_LOG("PCFSTCP[V11]: config enable overridden to 1 in RAM\n");
    return false;
}

static int observe_selector(void *state, int flags, uint32_t r2, uint32_t r3,
                            int (*original)(void *, int), const void *lr)
{
    (void)r2; (void)r3;
    FS_LOG("PCFSTCP[V11]: selector enter state=%p flags=%08x lr=%p\n", state, flags, lr);
    (void)original;
    if (!state) {
        FS_LOG("PCFSTCP[V11]: null selector state; refusing TCP\n");
        return 0;
    }
    volatile uint8_t *bytes = (volatile uint8_t *)state;
    *(volatile uint32_t *)(bytes + 0x58c) = 7500;
    *(volatile uint32_t *)(bytes + 0xdb4) = 0x0000ff00u;
    *(volatile uint32_t *)(bytes + 0xdb8) = 0x0000f7e0u;
    FS_LOG("PCFSTCP[V11]: selected native TCP transport=3 port=7500\n");
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

    FS_LOG("PCFSTCP[V11]: TCP_SM_Init result=%d attempts=%u retries=%u ncl_fd=%d\n",
           err, attempts, attempts - 1, *(volatile int32_t *)NCL_FD);
    return err;
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

    debug_printf("PCFSTCP[V11]: log=%08x selector=%08x\n", log_word, selector_word);
    if (log_word != COOKIE_LOG_STOCK || selector_word != SELECTOR_STOCK ||
        stock_word(INIT_ENTRY) != INIT_STOCK ||
        stock_word(CONFIG_RESULT) != CONFIG_STOCK ||
        stock_word(TCP_INIT_CALL) != TCP_INIT_CALL_STOCK ||
        stock_word(TCP_INIT_FN) != TCP_INIT_FN_STOCK ||
        stock_word(NCL_OPEN_FN) != NCL_OPEN_FN_STOCK ||
        stock_word(NCL_OPEN_FD_LIT) != NCL_FD ||
        stock_word(USLEEP_FN) != USLEEP_FN_STOCK) {
        debug_printf("PCFSTCP[V11]: signature mismatch; no patch applied\n");
        return;
    }
    trampoline_blreplace(COOKIE_LOG_CALL, cookie_log);
    trampoline_hook_before_v2(INIT_ENTRY, observe_init);
    trampoline_hook_before_v2(CONFIG_RESULT, observe_config);
    trampoline_blreplace(SELECTOR_CALL, observe_selector);
    trampoline_blreplace(TCP_INIT_CALL, retry_tcp_init);
}

void mcp_main(void)
{
}
