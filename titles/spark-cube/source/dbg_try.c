/*
 * dbg_try.c - ask coreinit's own GDB stub to wait for a debugger.
 *
 * The packager forces cos.xml cmdFlags to 0, so this sets app-flag bit 0x80
 * itself. The stub's transport init then has to run on all three cores: the
 * other cores wait in OSWaitRendezvous until the main core reaches them.
 * That main core opens /dev/dk/dbgCtrl. OSDebugStr waits for a debugger.
 */
#include <cafe/os.h>
#include <string.h>

#define DBG_STACK (16 * 1024)

static OSThread s_thread[3];
static u64 s_stack[3][DBG_STACK / sizeof(u64)];
static unsigned int s_init;

static int core_entry(int argc, void *argv);

/* coreinit .data. __OSGetAppFlags reads this. Bit 0x80 selects /dbgCtrl.
 * makecfmaster rewrites cos.xml cmdFlags to 0, so the title sets the bit. */
static volatile unsigned int *const s_app_flags = (volatile unsigned int *)0x10013C2C;

/* Writable alias of this title's text. OS 1000400A refuses it: OSMapMemory
 * fails, one line is logged, and the rest of dbgtry runs without it.
 *
 * Title text is mapped read-only, so the stub's plain stw of a breakpoint
 * trap (coreinit 0x02067cf8, agent command 9) faults inside the stub and
 * hangs it. Map the same physical pages a second time, read-write, and let
 * the PC bridge (dk_gdb.py --text-alias) point the stub's Z0/z0/M at that
 * alias. The caches are physically tagged, so the stub's dcbf/icbi on the
 * alias address also covers the real one. */
#define TEXT_PAGE (128u * 1024u)
/* Exported by coreinit but not in the SDK headers (link fails if not). */
extern u32 OSEffectiveToPhysical(u32 va);
extern void ICInvalidateRange(const void *addr, u32 nBytes);

/* Never called except by the alias probe; its first word is rewritten
 * through the alias to prove the mapping is writable and executes. */
static int dbg_alias_probe(void) { return 0x1234; }
static int (*volatile s_probe)(void) = dbg_alias_probe;

unsigned int g_dbg_text_alias[4]; /* text base, size, alias base, 0x414c4953 "ALIS" */

static void text_alias(void)
{
    unsigned int me = (unsigned int)s_probe;
    unsigned int text = 0, size = 0;
    int rpls = OSDynLoad_GetNumberOfRPLs();
    for (int i = 0; i < rpls && i < 64; i++) {
        OSDynLoad_NotifyHdr hdr;
        memset(&hdr, 0, sizeof(hdr));
        if (OSDynLoad_GetRPLInfo((unsigned int)i, 1, &hdr) != 1)
            continue;
        if (me >= hdr.mDebug_TextAddr && me < hdr.mDebug_TextAddr + hdr.mDebug_TextSize) {
            text = hdr.mDebug_TextAddr;
            size = hdr.mDebug_TextSize;
            break;
        }
    }
    va_t mva = 0;
    pa_t dpa = 0, apa = 0;
    size_t msz = 0, dsz = 0, asz = 0;
    OSGetMapVirtAddrRange(&mva, &msz);
    OSGetDataPhysAddrRange(&dpa, &dsz);
    OSGetAvailPhysAddrRange(&apa, &asz);
    OSReport("DBG-try: alias: map va %08x+%x data pa %08x+%x avail pa %08x+%x\n",
             mva, msz, dpa, dsz, apa, asz);
    if (!text) {
        OSReport("DBG-try: alias: own text not found\n");
        return;
    }
    unsigned int base = text & ~(TEXT_PAGE - 1);
    unsigned int end = (text + size + TEXT_PAGE - 1) & ~(TEXT_PAGE - 1);
    unsigned int len = end - base;
    unsigned int pa = OSEffectiveToPhysical(base);
    OSReport("DBG-try: alias: text %08x+%x pages %08x+%x pa %08x\n", text, size, base, len, pa);
    for (unsigned int off = 0; off < len; off += TEXT_PAGE) {
        unsigned int p = OSEffectiveToPhysical(base + off);
        if (p != pa + off) {
            OSReport("DBG-try: alias: text not contiguous at +%x (pa %08x)\n", off, p);
            return;
        }
    }
    unsigned int va = OSAllocVirtAddr(0, len, TEXT_PAGE);
    if (!va) {
        OSReport("DBG-try: alias: OSAllocVirtAddr failed\n");
        return;
    }
    if (!OSMapMemory(va, pa, len, OS_MAP_RW)) {
        OSReport("DBG-try: alias: OSMapMemory(%08x, %08x, %x, RW) failed\n", va, pa, len);
        return;
    }
    volatile unsigned int *orig = (volatile unsigned int *)me;
    volatile unsigned int *alias = (volatile unsigned int *)(va + (me - base));
    OSReport("DBG-try: alias: mapped %08x; probe %08x word %08x via alias %08x\n",
             va, me, *orig, *alias);
    *alias = 0x38605678u;                      /* li r3,0x5678 */
    alias[1] = 0x4e800020u;                    /* blr */
    DCFlushRange((void *)alias, 8);
    ICInvalidateRange((void *)orig, 8);
    int got = s_probe();
    OSReport("DBG-try: alias: probe returned %x (5678 = writable alias works)\n", got);
    if (got != 0x5678)
        return;
    g_dbg_text_alias[0] = base;
    g_dbg_text_alias[1] = len;
    g_dbg_text_alias[2] = va;
    g_dbg_text_alias[3] = 0x414c4953u;
    OSReport("DBG-try: text alias %08x+%x -> %08x (dk_gdb.py --text-alias %08x:%x:%08x)\n",
             base, len, va, base, len, va);
}

static int dbg_main(int argc, void *argv)
{
    (void)argc;
    (void)argv;
    text_alias();
    OSReport("DBG-try: app flags before=%08x\n", *s_app_flags);
    *s_app_flags |= 0x80;
    OSReport("DBG-try: app flags after=%08x\n", *s_app_flags);
    /* Transport init is not exported. coreinit text is relocated, so take
     * its load address from the loader and require the stwu this function
     * starts with. A wrong address is not called. */
    unsigned int text = 0;
    unsigned int text_size = 0;
    int rpls = OSDynLoad_GetNumberOfRPLs();
    OSReport("DBG-try: rpl count=%d\n", rpls);
    for (int i = 0; i < rpls && i < 64; i++) {
        OSDynLoad_NotifyHdr hdr;
        memset(&hdr, 0, sizeof(hdr));
        if (OSDynLoad_GetRPLInfo((unsigned int)i, 1, &hdr) != 1 || !hdr.mpName)
            continue;
        if (!strstr(hdr.mpName, "coreinit"))
            continue;
        text = hdr.mDebug_TextAddr;
        text_size = hdr.mDebug_TextSize;
        OSReport("DBG-try: coreinit text=%08x size=%08x\n", text, text_size);
        break;
    }
    unsigned int init_addr = text + 0x3b1d4;
    if (!text || init_addr < text || init_addr + 4 > text + text_size) {
        OSReport("DBG-try: transport init not in coreinit text\n");
        return 0;
    }
    unsigned int word = *(volatile unsigned int *)init_addr;
    OSReport("DBG-try: transport init at %08x word=%08x\n", init_addr, word);
    if (word != 0x9421ffd0) {
        OSReport("DBG-try: transport init signature mismatch\n");
        return 0;
    }
    s_init = init_addr;
    for (int core = 1; core <= 2; core++) {
        if (!OSCreateThread(&s_thread[core], core_entry, core, NULL,
                            (u8 *)s_stack[core] + sizeof(s_stack[core]),
                            sizeof(s_stack[core]), 16,
                            (u16)(1u << core))) {
            OSReport("DBG-try: OSCreateThread failed for core %d\n", core);
            return 0;
        }
        OSSetThreadName(&s_thread[core], "SparkCube dbgtry");
        OSResumeThread(&s_thread[core]);
    }
    return core_entry(0, NULL);
}

/* User heartbeat (coreinit 0x0201546c, 1000400A): each core has a 500 ms
 * periodic alarm whose handler adds 1 to the per-core word 0xFFFFFFD8, and
 * the kernel times the title out when that stops moving. A debugger halt
 * stops it. coreinit's own "Disabling heartbeat for title" path starts no
 * alarm and stores -1 there instead, so do the same: cancel this core's
 * alarm, then store -1. Must run on the core it is for. */
#define HB_ALARM_BASE 0x10086CE8u
#define HB_ALARM_SIZE 0x58u
#define HB_ALARM_TAG 0x614C724Du /* "aLrM" */
#define HB_COUNTER ((volatile unsigned int *)0xFFFFFFD8u)

static void heartbeat_off(int core)
{
    OSAlarm *alarm = (OSAlarm *)(HB_ALARM_BASE + (unsigned int)core * HB_ALARM_SIZE);
    unsigned int tag = *(volatile unsigned int *)alarm;

    OSReport("DBG-try: core %d heartbeat alarm %08x tag %08x count %08x\n",
             core, (unsigned int)alarm, tag, *HB_COUNTER);
    if (tag != HB_ALARM_TAG) {
        OSReport("DBG-try: core %d heartbeat alarm tag mismatch, left on\n", core);
        return;
    }
    OSCancelAlarm(alarm);
    *HB_COUNTER = 0xFFFFFFFFu;
    OSReport("DBG-try: core %d heartbeat off\n", core);
}

static int core_entry(int argc, void *argv)
{
    (void)argv;
    heartbeat_off(argc);
    OSReport("DBG-try: core %d calling init\n", argc);
    ((void (*)(void))s_init)();
    OSReport("DBG-try: core %d init returned initialized=%d\n",
             argc, OSIsDebuggerInitialized());
    if (argc == 0 && OSIsDebuggerInitialized()) {
        OSReport("DBG-try: OSDebugStr begin\n");
        OSDebugStr("SparkCube dbgtry");
        OSReport("DBG-try: OSDebugStr returned\n");
    }
    return 0;
}

void dbg_try_start(void)
{
    if (!OSCreateThread(&s_thread[0], dbg_main, 0, NULL,
                        (u8 *)s_stack[0] + sizeof(s_stack[0]), sizeof(s_stack[0]),
                        16, OS_THREAD_ATTR_AFFINITY_CORE0)) {
        OSReport("DBG-try: OSCreateThread failed\n");
        return;
    }
    OSSetThreadName(&s_thread[0], "SparkCube dbgtry");
    OSResumeThread(&s_thread[0]);
    OSReport("DBG-try: thread started\n");
}
