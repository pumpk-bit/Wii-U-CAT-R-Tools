#include <stdint.h>
#include <string.h>
#include <wafel/ios_dynamic.h>
#include <wafel/ios/svc.h>
#include <wafel/patch.h>
#include <wafel/trampoline.h>
#include "dk_sock.h"

/*
 * Answers /dev/dk when the SDIO slot has no MION, and carries the character
 * read/write ioctls to a PC that connects to TCP 7332.
 *
 * The listen thread uses the WUP server's /dev/socket ioctl sequence with its
 * own device handle, so the MCP server on port 1337 is left alone. Logging
 * goes through the FS logger at 0x107f0c84 (USB/FTDI), not debug_printf.
 *
 * Handled here, only after stock dispatch failed because the host slot is
 * empty (return 0xfff9fff5, which dkMain has not replied yet):
 *   0x10 / 0x11  block-stream write / read
 *   0x20 / 0x21  the character read / write coreinit's HIO worker uses
 *   0x41         the other PCHAR packet-out command
 * Command 0x30 (open a named channel) is not answered here. A success
 * reply without the stock opener leaves the shared-memory ring empty, and
 * coreinit's debugger send then fails with 0xfff3fffc. The stock opener
 * does not use the SDIO card. The no-card branch is patched so command
 * 0x30 still reaches it, and every other command keeps the no-card
 * failure. IOS open and close stay on the stock path. With no PC
 * connected, the tunneled commands stay on that path too. A line is
 * logged once per command: the worker polls, and a line per call fills
 * the USB log.
 */
#define HEADLESS_FLAG 0x10894e60u
#define HEADLESS_ERR 0xfff9fff5u

#define WRITER_MOV 0x10712af0u
#define WRITER_MOV_STOCK 0xe3a02001u
#define WRITER_MOV_CLEAR 0xe3a02000u
#define WRITER_STR 0x10712af4u
#define WRITER_STR_STOCK 0xe5832000u
#define WRITER_LIT 0x10712bb0u

#define READER 0x107125f4u
#define READER_LDR_LIT 0xe59f3004u
#define READER_LDR_VAL 0xe5930000u
#define READER_BX 0x107125fcu
#define READER_BX_STOCK 0xe12fff1eu
#define READER_LIT 0x10712600u
#define READER_MOV1 0xe3a00001u /* mov r0,#1: HIOInit sees headless and returns -10, as stock */

#define DK_MAIN 0x10712bd4u
#define DK_MAIN_STOCK 0xe92d45f0u
#define FS_LOG_FN 0x107f0c84u
#define FS_LOG_STOCK 0xe92d000fu

#define DISPATCH_BL_A 0x10712da4u
#define DISPATCH_BL_A_STOCK 0xebffff05u
#define DISPATCH_BL_B 0x10712db8u
#define DISPATCH_BL_B_STOCK 0xebffff00u

/* beq to the no-card return. Replaced with a gate that lets command 0x30
 * fall into the switch. The switch instruction and the no-card return are
 * the two absolute words at the end of the gate. */
#define NULL_DEV_BEQ 0x10712a30u
#define NULL_DEV_BEQ_STOCK 0x0a00002cu

/* dk's client table: 0x60 entries of 0x38 bytes, indexed by the IOS fd
 * (FUN_107178cc). +0 in use, +0x18 interface (0 /boot .. 10 /hfioBlock,
 * 11..74 /userhostioN), +0x1c type. The table address is the literal
 * dkDispatchRequest passes to that lookup. */
#define CLIENT_LIT 0x10712ba0u
#define CLIENT_TABLE 0x113fd2b0u
#define CLIENT_MAX 0x60u
#define CLIENT_SIZE 0x38u
#define IFACE_UNKNOWN 0xffffu

/* SMD probe (read-only). dk's shared-memory channels carry the debugger
 * stub's traffic.
 *   DRAIN_BL   dkMain's call of the 0x100 drain FUN_10713f44(table)
 *   MSG_BL_A/B the drain's two calls of FUN_10713dd4(entry, smd_msg)
 * The table holds 2 entries of 0x58 bytes from +4: +0 in use, +8 / +0xc
 * IOSU SMD channel indexes, +0x10 dk client. SMD channels: 8 x 0x30
 * bytes, +0 the control block (IOSU state +0x80, PPC state +0x340). */
#define DRAIN_BL 0x10712d10u
#define DRAIN_BL_STOCK 0xeb00048bu
#define MSG_BL_A 0x10713facu
#define MSG_BL_A_STOCK 0xebffff88u
#define MSG_BL_B 0x10714028u
#define MSG_BL_B_STOCK 0xebffff69u
#define SMD_TABLE_LIT 0x10712e74u
#define SMD_TABLE 0x1147290cu
#define SMD_CH_LIT 0x107bc444u
#define SMD_CH 0x11c39ce0u
#define SMD_ENTRIES 2u
#define SMD_ENTRY_SIZE 0x58u
#define SMD_CH_MAX 8u
#define SMD_READY 0x2222u
#define HOST_SLOT 0x115a122cu

/* dk creates the SMD table and its 10 ms drain timer only on message 0xd,
 * and only when the /dev/bsp hardware version is not 0x28/0x29.
 * This build starts it itself on the first /dev/dk request. */
#define SMD_START_FN 0x10714164u
#define SMD_START_STOCK 0xe92d4070u
#define BSP_HWVER_FN 0x107edc54u
#define BSP_HWVER_STOCK 0xe92d40f0u
#define SYSMODE_FN 0x107f6cd4u
#define SYSMODE_STOCK 0xe7f07af0u
typedef void (*smd_start_fn)(uint32_t);
typedef uint32_t (*bsp_hwver_fn)(uint8_t *);
typedef int (*sysmode_fn)(void);

/* SMD tunnel for the debugger: dk holds an SMD read until it has data for
 * it. FUN_10713d00 / FUN_10713d20 post a finished
 * read / write back to the stub; they flush the request and its vectors. */
#define SMD_REPLY_READ_FN 0x10713d00u
#define SMD_REPLY_READ_STOCK 0xe5903000u
#define SMD_REPLY_WRITE_FN 0x10713d20u
#define SMD_REPLY_WRITE_STOCK 0xe5903000u
#define SMD_PENDING 0xfff9ffe1u
#define SMD_IFACE 3u
/* The stub posts about 32 reads (request blocks 0x500 apart). */
#define SMD_HOLD_MAX 64u
#define SMD_NO_DATA 0xfff9fff6u
#define SMD_DATA_MAX 0x200u
#define SMD_FD 0xfffeu
typedef int (*smd_reply_fn)(uint32_t, uint32_t);

typedef void (*dcache_fn)(const void *, uint32_t);
#define INVALIDATE_DCACHE ((dcache_fn)0x107f6b8cu)

#define REQ_MAGIC 0x444B3031u
#define RESP_MAGIC 0x444B3032u
#define MAX_VEC 4u
#define MAX_VEC_LEN 0x10000u
#define MAX_FRAME 0x12000u

typedef int (*fs_log_fn)(const char *, ...);
#define FS_LOG ((fs_log_fn)FS_LOG_FN)

int enable_debug_printf;

static int patch_applied;
static int channel_gate;
static int client_table_ok;
static int open_enter;
static int open_done;

/* Runs instead of the no-card beq. ip is the device pointer, r4 the
 * message. Command 0x30 is the only one whose handler does not use the
 * device, so it continues into the switch. Everything else takes the
 * original no-card return. The literal pool moves with these words. */
static const uint32_t null_dev_gate[] = {
    0xe35c0000, 0x1a00000d, 0xe5d40024, 0xe3500000, 0x1a000008,
    0xe5d40025, 0xe3500000, 0x1a000005, 0xe5d40026, 0xe3500000,
    0x1a000002, 0xe5d40027, 0xe3500030, 0x0a000001, 0xe59f0008,
    0xe12fff10, 0xe59f0004, 0xe12fff10, 0x10712ae8, 0x10712a34,
};

static uint8_t req_buf[MAX_FRAME];
static uint8_t resp_buf[MAX_FRAME];

#define SEEN_MAX 16u
static uint32_t seen_cmd[SEEN_MAX];
static uint8_t seen_kind[SEEN_MAX];
static uint32_t seen_n;

/* One USB line per command and reason. 0x21 is 33, so a 32-bit mask cannot
 * keep it apart from every other command above 31. */
static void log_once(uint32_t kind, uint32_t cmd, const char *fmt)
{
    uint32_t i;

    for (i = 0; i < seen_n; i++) {
        if (seen_cmd[i] == cmd && seen_kind[i] == (uint8_t)kind)
            return;
    }
    if (seen_n >= SEEN_MAX)
        return;
    seen_cmd[seen_n] = cmd;
    seen_kind[seen_n] = (uint8_t)kind;
    seen_n++;
    FS_LOG(fmt, cmd);
}

/* Interface of the client behind an fd, or IFACE_UNKNOWN. Read only. */
static uint32_t client_iface(uint32_t fd)
{
    const volatile uint32_t *entry;

    if (!client_table_ok || fd >= CLIENT_MAX)
        return IFACE_UNKNOWN;
    entry = (const volatile uint32_t *)(CLIENT_TABLE + fd * CLIENT_SIZE);
    if (!entry[0] || entry[0x18 / 4] >= IFACE_UNKNOWN)
        return IFACE_UNKNOWN;
    return entry[0x18 / 4];
}

#define FD_SEEN_MAX 16u
static uint32_t fd_seen[FD_SEEN_MAX];
static uint32_t fd_seen_n;

/* One USB line per fd/interface pair, so the log shows which channel
 * each fd is. */
static void log_fd_once(uint32_t word)
{
    uint32_t i;

    for (i = 0; i < fd_seen_n; i++) {
        if (fd_seen[i] == word)
            return;
    }
    if (fd_seen_n >= FD_SEEN_MAX)
        return;
    fd_seen[fd_seen_n++] = word;
    FS_LOG("DKTCP: fd=%x iface=%x\n", word & 0xffffu, word >> 16);
}

static uint32_t stock_word(uintptr_t vaddr)
{
    return *(volatile uint32_t *)ios_elf_vaddr_to_paddr(vaddr);
}

static void put_be32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static uint32_t get_be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

/* Returns 1 when the IOS reply has been sent. */
static int service_dk(uint32_t msg)
{
    uint32_t cmd = *(uint32_t *)(msg + 0x24);
    uint32_t nin = *(uint32_t *)(msg + 0x28);
    uint32_t nout = *(uint32_t *)(msg + 0x2c);
    IOSVec *vec = *(IOSVec **)(msg + 0x30);
    uint32_t i, off, resp_len, result, nout_got, extra;
    const uint8_t *p;
    uint8_t read_data[80];
    uint32_t read_len = 0;

    if (cmd != 0x10 && cmd != 0x11 && cmd != 0x20 && cmd != 0x21 &&
        cmd != 0x41) {
        log_once(1, cmd, "DKTCP: cmd=%x left stock\n");
        return 0;
    }
    if (!vec || nin > MAX_VEC || nout > MAX_VEC || nin + nout > MAX_VEC)
        return 0;

    off = 20;
    for (i = 0; i < nin; i++) {
        if (vec[i].len > MAX_VEC_LEN || off + 4 + vec[i].len > MAX_FRAME)
            return 0;
        if (vec[i].len && !vec[i].ptr)
            return 0;
    }
    for (i = 0; i < nout; i++) {
        if (vec[nin + i].len > MAX_VEC_LEN || off + 4 > MAX_FRAME)
            return 0;
    }

    /* A character write's bytes sit in the bounce, which IOS presents as
     * an output. The 12-byte input is only the shim header. Send that
     * bounce as well so the PC sees the packet. The 80-byte output is the
     * status buffer and is not part of the packet. */
    extra = 0;
    if (cmd == 0x21 || cmd == 0x41) {
        for (i = 0; i < nout; i++) {
            if (vec[nin + i].len && vec[nin + i].len != 80 && vec[nin + i].ptr)
                extra++;
        }
    }

    put_be32(req_buf + 0, REQ_MAGIC);
    put_be32(req_buf + 4, cmd);
    put_be32(req_buf + 8, nin + extra);
    put_be32(req_buf + 12, nout);
    /* Interface in the high half, client fd in the low half, so the PC can
     * keep the debugger channel (/dbgCtrl = 3) apart from the others. */
    {
        uint32_t fd = *(uint32_t *)(msg + 8);
        uint32_t word = (client_iface(fd) << 16) | (fd & 0xffffu);

        log_fd_once(word);
        put_be32(req_buf + 16, word);
    }
    off = 20;
    for (i = 0; i < nin; i++) {
        put_be32(req_buf + off, vec[i].len);
        off += 4;
        if (vec[i].len) {
            memcpy(req_buf + off, vec[i].ptr, vec[i].len);
            off += vec[i].len;
        }
    }
    if (extra) {
        for (i = 0; i < nout; i++) {
            if (!vec[nin + i].len || vec[nin + i].len == 80 || !vec[nin + i].ptr)
                continue;
            if (off + 4 + vec[nin + i].len > MAX_FRAME)
                return 0;
            put_be32(req_buf + off, vec[nin + i].len);
            off += 4;
            memcpy(req_buf + off, vec[nin + i].ptr, vec[nin + i].len);
            off += vec[nin + i].len;
        }
    }
    for (i = 0; i < nout; i++) {
        put_be32(req_buf + off, vec[nin + i].len);
        off += 4;
    }

    if (dk_net_exchange(req_buf, off, resp_buf, MAX_FRAME, &resp_len) < 0) {
        log_once(2, cmd, "DKTCP: cmd=%x no client\n");
        return 0;
    }
    if (resp_len < 12 || get_be32(resp_buf) != RESP_MAGIC) {
        FS_LOG("DKTCP: cmd=%x bad reply\n", cmd);
        return 0;
    }
    result = get_be32(resp_buf + 8);
    p = resp_buf + 12;
    nout_got = 0;
    while (nout_got < nout && (uint32_t)(p - resp_buf) + 4 <= resp_len) {
        uint32_t len = get_be32(p);
        uint32_t cap = vec[nin + nout_got].len;
        p += 4;
        if ((uint32_t)(p - resp_buf) + len > resp_len || len > cap)
            return 0;
        if (cmd == 0x20 && read_len == 0 && len && len <= sizeof(read_data)) {
            memcpy(read_data, p, len);
            read_len = len;
        }
        /* 0x20's output starts with the command tag 0x20. The completion
         * refuses to copy the bounce unless that word is still there, so
         * the reply is not written into the output. 0x21's 80-byte output
         * holds the same kind of tag; the write succeeds on the IOS result
         * alone. */
        if (cmd != 0x20 && cmd != 0x21 && cmd != 0x41 &&
            cap && vec[nin + nout_got].ptr) {
            if (len)
                memcpy(vec[nin + nout_got].ptr, p, len);
            if (len < cap)
                memset((uint8_t *)vec[nin + nout_got].ptr + len, 0, cap - len);
        }
        p += len;
        nout_got++;
    }
    if (result != 0 || nout_got != nout)
        return 0;

    /* DK_CharRead copies out of every non-header input. The copy length is
     * the big-endian word at byte 4 of the 12-byte input. The PC's first
     * output is those bytes (channel id, then status, little-endian). */
    if (cmd == 0x20 && read_len) {
        for (i = 0; i < nin; i++) {
            if (!vec[i].ptr || !vec[i].len)
                continue;
            if (vec[i].len == 12) {
                put_be32(vec[i].ptr, 0);
                put_be32((uint8_t *)vec[i].ptr + 4, read_len);
            } else if (vec[i].len >= read_len) {
                memcpy(vec[i].ptr, read_data, read_len);
            }
        }
    }

    iosResourceReply((ipcmessage *)msg, 0);
    log_once(3, cmd, "DKTCP: cmd=%x replied 0\n");
    return 1;
}

/* ---- SMD probe ---------------------------------------------------------
 * Logs only. Every hooked call still runs the stock function with the
 * same arguments, and nothing on either side is written. */

static int smd_probe;
static int smd_start_ok;
static int smd_start_done;
static uint32_t smd_state_last[SMD_ENTRIES][4];
static uint32_t smd_state_lines;
static uint32_t smd_msg_lines;
static uint32_t smd_count[SMD_ENTRIES];

static uint32_t smd_rd(uint32_t addr)
{
    INVALIDATE_DCACHE((const void *)addr, 4);
    return *(volatile uint32_t *)addr;
}

/* IOSU state and PPC state of one SMD channel, or 0xffffffff. */
static void smd_states(uint32_t ch, uint32_t *iop, uint32_t *ppc)
{
    uint32_t ctrl;

    *iop = *ppc = 0xffffffffu;
    /* Same guard as the stock state reader FUN_107bc380: open state > 1. */
    if (ch >= SMD_CH_MAX || *(volatile uint32_t *)(SMD_CH + ch * 0x30 + 0x2c) <= 1)
        return;
    ctrl = *(volatile uint32_t *)(SMD_CH + ch * 0x30);
    if (!ctrl)
        return;
    *iop = smd_rd(ctrl + 0x80);
    *ppc = smd_rd(ctrl + 0x340);
}

/* Before each 10 ms drain: log the state words of every open entry
 * whenever they change (at most 24 lines). */
static void smd_log_states(void)
{
    uint32_t i, s[4];
    const volatile uint32_t *e;

    if (*(volatile uint32_t *)SMD_TABLE == 0)
        return;
    for (i = 0; i < SMD_ENTRIES; i++) {
        e = (const volatile uint32_t *)(SMD_TABLE + 4 + i * SMD_ENTRY_SIZE);
        if (!e[0])
            continue;
        smd_states(e[2], &s[0], &s[1]);
        smd_states(e[3], &s[2], &s[3]);
        if (!memcmp(s, smd_state_last[i], sizeof(s)) || smd_state_lines >= 24)
            continue;
        memcpy(smd_state_last[i], s, sizeof(s));
        smd_state_lines++;
        FS_LOG("DKSMD: entry %x ch %x iop %x ppc %x | ch %x iop %x ppc %x | iface %x\n",
               i, e[2], s[0], s[1], e[3], s[2], s[3],
               e[4] ? *(volatile uint32_t *)(e[4] + 0x18) : IFACE_UNKNOWN);
    }
}

/* ---- SMD tunnel --------------------------------------------------------
 * With no host device, stock dk answers each debugger read at once and the
 * stub reposts it in a tight loop. Here a read on interface 3 is held, as
 * a MION-backed dk would, and served from the PC on the 10 ms drain tick.
 * Writes go to the PC and complete at once. Requests and vectors hold
 * physical addresses, which IOSU reads and writes directly (stock does). */

static int smd_tunnel;
static uint32_t smd_hold_entry[SMD_HOLD_MAX];
static uint32_t smd_hold_req[SMD_HOLD_MAX];
static uint32_t smd_hold_key[SMD_HOLD_MAX][3];
static uint32_t smd_hold_head, smd_hold_n;
static uint32_t smd_reads_done, smd_writes_done, smd_tun_lines, smd_dup_reads, smd_overflow;
static void smd_tunnel_tick(void);
static uint8_t smd_req_buf[24 + SMD_DATA_MAX];
static uint8_t smd_resp_buf[16 + SMD_DATA_MAX];

static void smd_tun_log2(const char *fmt, uint32_t a, uint32_t b)
{
    if (smd_tun_lines < 32) {
        smd_tun_lines++;
        FS_LOG(fmt, a, b);
    }
}

/* Entry still open with the same channels and client, both sides ready. */
static int smd_entry_live(uint32_t entry, const uint32_t *key)
{
    const volatile uint32_t *e = (const volatile uint32_t *)entry;
    uint32_t a, b, c, d;

    if (!e[0] || e[2] != key[0] || e[3] != key[1] || e[4] != key[2])
        return 0;
    smd_states(e[2], &a, &b);
    smd_states(e[3], &c, &d);
    return a == SMD_READY && b == SMD_READY && c == SMD_READY && d == SMD_READY;
}

/* DK01 frame from the plugin to the PC: cmd 0x140 (read, one output of
 * cap bytes) or 0x141 (write, one input). Returns the reply payload length,
 * or -1 for no PC / bad reply. */
static int smd_exchange(uint32_t cmd, const void *data, uint32_t len, uint32_t cap,
                        const uint8_t **payload)
{
    uint32_t off = 20, resp_len, plen;

    put_be32(smd_req_buf + 0, REQ_MAGIC);
    put_be32(smd_req_buf + 4, cmd);
    put_be32(smd_req_buf + 8, data ? 1 : 0);
    put_be32(smd_req_buf + 12, data ? 0 : 1);
    put_be32(smd_req_buf + 16, (SMD_IFACE << 16) | SMD_FD);
    if (data) {
        put_be32(smd_req_buf + off, len);
        off += 4;
        memcpy(smd_req_buf + off, data, len);
        off += len;
    } else {
        put_be32(smd_req_buf + off, cap);
        off += 4;
    }
    if (dk_net_exchange(smd_req_buf, off, smd_resp_buf, sizeof(smd_resp_buf), &resp_len) < 0)
        return -1;
    if (resp_len < 12 || get_be32(smd_resp_buf) != RESP_MAGIC || get_be32(smd_resp_buf + 8))
        return -1;
    if (data)
        return 0;
    if (resp_len < 16)
        return -1;
    plen = get_be32(smd_resp_buf + 12);
    if (plen > cap || 16 + plen > resp_len)
        return -1;
    *payload = smd_resp_buf + 16;
    return (int)plen;
}

/* Request vectors: v0 status (12 bytes), v1 data, v2 descriptor. */
static int smd_req_ok(uint32_t req)
{
    const volatile uint32_t *r = (const volatile uint32_t *)req;

    return r[1] == 3 && r[2] && r[3] >= 8 && r[4] && r[5] && r[6] && r[7] >= 0x14;
}

/* Returns 1 when the message was taken (held or completed). */
static int smd_tunnel_msg(uint32_t entry, uint32_t smsg)
{
    volatile uint32_t *r, *v0, *v2;
    uint32_t req, client, cmd, idx, len;

    if (!smd_tunnel || *(volatile uint32_t *)HOST_SLOT)
        return 0;
    if (*(volatile uint32_t *)smsg != 2)
        return 0;
    req = *(volatile uint32_t *)(smsg + 8);
    client = *(volatile uint32_t *)(entry + 0x10);
    if (!req || !client || *(volatile uint32_t *)(client + 0x18) != SMD_IFACE)
        return 0;
    if (!smd_req_ok(req))
        return 0;
    r = (volatile uint32_t *)req;
    cmd = r[0];
    v0 = (volatile uint32_t *)r[2];
    v2 = (volatile uint32_t *)r[6];

    if (cmd == 0x40) {
        /* The stub reposts a buffer that is already held. A second entry
         * for it would fill the queue and send the overflow down the stock
         * no-host path (the spin again), and a later delivery would land
         * in a buffer the stub has moved on from. Keep one. */
        for (idx = 0; idx < smd_hold_n; idx++) {
            if (smd_hold_req[(smd_hold_head + idx) % SMD_HOLD_MAX] == req) {
                /* The stub may have rewritten the descriptor; keep it as
                 * the held state needs it. */
                v0[0] = SMD_PENDING;
                v2[3] = r[4];
                smd_dup_reads++;
                return 1;
            }
        }
        if (smd_hold_n >= SMD_HOLD_MAX) {
            /* Stock's no-host reply leaves v0 as it was, and a reused buffer
             * then still carries an old byte count: the stub re-reads a
             * stale packet. Say "no data" first. */
            if (!smd_overflow++)
                smd_tun_log2("DKSMD: hold queue full, req %x to stock (%x)\n", req, 0);
            v0[0] = SMD_NO_DATA;
            v0[1] = 0;
            return 0;
        }
        /* As FUN_107175b0: status pending, descriptor +0xc = data. */
        v0[0] = SMD_PENDING;
        v2[3] = r[4];
        idx = (smd_hold_head + smd_hold_n) % SMD_HOLD_MAX;
        smd_hold_entry[idx] = entry;
        smd_hold_req[idx] = req;
        smd_hold_key[idx][0] = *(volatile uint32_t *)(entry + 8);
        smd_hold_key[idx][1] = *(volatile uint32_t *)(entry + 0xc);
        smd_hold_key[idx][2] = client;
        smd_hold_n++;
        if (smd_hold_n == 1 && smd_reads_done == 0)
            smd_tun_log2("DKSMD: read held, req %x cap %x\n", req, v2[4]);
        /* GDB's bytes may already be waiting (an ack for the stub). */
        smd_tunnel_tick();
        return 1;
    }
    if (cmd == 0x41) {
        /* The data starts at v1; the stub leaves 12 bytes in front of it
         * for dk's PCHAR header. Length: the descriptor's +0x10. */
        len = v2[4];
        if (!len || len > r[5])
            len = r[5];
        if (len > SMD_DATA_MAX)
            len = SMD_DATA_MAX;
        if (smd_exchange(0x141, (const void *)r[4], len, 0, 0) < 0) {
            smd_tun_log2("DKSMD: write len %x not sent (%x)\n", len, 0);
            return 0;
        }
        v0[0] = 0;
        v0[1] = len;
        ((smd_reply_fn)SMD_REPLY_WRITE_FN)(entry, req);
        smd_writes_done++;
        if (smd_writes_done <= 4 || !(smd_writes_done & (smd_writes_done - 1)))
            smd_tun_log2("DKSMD: write #%x len %x sent\n", smd_writes_done, len);
        /* GDB answers a packet with '+' at once; the stub resends if the ack
         * is late, and GDB then takes the resend as its next reply. */
        smd_tunnel_tick();
        return 1;
    }
    return 0;
}

/* On each drain tick: drop held reads whose channel went away, then serve
 * the oldest one from the PC. An empty answer keeps it held. */
static int smd_tunnel_serve_one(void);

static void smd_tunnel_tick(void)
{
    uint32_t i;

    /* Up to 8 reads per call while the PC has bytes; stops at the first
     * empty answer. */
    for (i = 0; i < 8; i++) {
        if (smd_tunnel_serve_one() <= 0)
            break;
    }
}

static int smd_tunnel_serve_one(void)
{
    volatile uint32_t *r, *v0, *v2;
    const uint8_t *payload = 0;
    uint32_t idx, entry, req, cap;
    int n;

    while (smd_hold_n) {
        idx = smd_hold_head;
        if (smd_entry_live(smd_hold_entry[idx], smd_hold_key[idx]))
            break;
        smd_tun_log2("DKSMD: dropped held read %x (channel gone), %x left\n",
                     smd_hold_req[idx], smd_hold_n - 1);
        smd_hold_head = (smd_hold_head + 1) % SMD_HOLD_MAX;
        smd_hold_n--;
    }
    if (!smd_hold_n)
        return 0;
    idx = smd_hold_head;
    entry = smd_hold_entry[idx];
    req = smd_hold_req[idx];
    r = (volatile uint32_t *)req;
    v0 = (volatile uint32_t *)r[2];
    v2 = (volatile uint32_t *)r[6];
    cap = v2[4];
    if (!cap || cap > r[5])
        cap = r[5];
    if (cap > SMD_DATA_MAX)
        cap = SMD_DATA_MAX;
    n = smd_exchange(0x140, 0, 0, cap, &payload);
    if (n <= 0)
        return 0;
    /* The data vector's own (physical) address, never the descriptor word:
     * a re-posted request can carry the stub's virtual address there. */
    memcpy((void *)r[4], payload, (uint32_t)n);
    v2[3] = r[4];
    v0[0] = (uint32_t)n;
    v0[1] = (uint32_t)n;
    smd_hold_head = (smd_hold_head + 1) % SMD_HOLD_MAX;
    smd_hold_n--;
    ((smd_reply_fn)SMD_REPLY_READ_FN)(entry, req);
    smd_reads_done++;
    if (smd_reads_done <= 4 || !(smd_reads_done & (smd_reads_done - 1)))
        smd_tun_log2("DKSMD: read #%x served %x bytes\n", smd_reads_done, (uint32_t)n);
    return n;
}

__attribute__((target("arm")))
static int smd_drain(uint32_t table, uint32_t r1, uint32_t r2, uint32_t r3,
                     int (*original)(uint32_t, uint32_t, uint32_t, uint32_t),
                     const void *unused)
{
    int rc;
    (void)unused;
    smd_log_states();
    rc = original(table, r1, r2, r3);
    smd_tunnel_tick();
    return rc;
}

/* One SMD message: word 0 type, word 2 the request. Request: +0 command
 * (0x40 read from host, 0x41 write to host), +4 vector count, then
 * {addr, len} pairs (v0 status, v1 data, v2 descriptor). */
static void smd_log_msg(uint32_t entry, uint32_t smsg)
{
    uint32_t idx = (entry - (SMD_TABLE + 4)) / SMD_ENTRY_SIZE;
    uint32_t type = *(volatile uint32_t *)smsg;
    uint32_t req = *(volatile uint32_t *)(smsg + 8);
    uint32_t cmd = 0, nvec = 0, v[6] = {0, 0, 0, 0, 0, 0}, d[8];
    uint32_t i, n, client, iface = IFACE_UNKNOWN, host;

    if (idx < SMD_ENTRIES) {
        n = ++smd_count[idx];
        /* Counts 1, 2, 4, ... show a resend loop without a line per call. */
        if (n > 8 && (n & (n - 1)))
            return;
    }
    if (smd_msg_lines >= 40)
        return;
    smd_msg_lines++;

    client = *(volatile uint32_t *)(entry + 0x10);
    if (client)
        iface = *(volatile uint32_t *)(client + 0x18);
    host = *(volatile uint32_t *)HOST_SLOT;
    /* No cache invalidate on the request or the PPC buffers: the stock
     * handler reads the request as it is, and an invalidate could drop a
     * line IOSU has not written back. */
    if (type == 2 && req) {
        cmd = *(volatile uint32_t *)req;
        nvec = *(volatile uint32_t *)(req + 4);
        for (i = 0; i < 6; i++)
            v[i] = *(volatile uint32_t *)(req + 8 + i * 4);
    }
    FS_LOG("DKSMD: msg #%x entry %x iface %x type %x cmd %x nvec %x host %x\n",
           idx < SMD_ENTRIES ? smd_count[idx] : 0, idx, iface, type, cmd, nvec, host);
    if (type != 2 || !req)
        return;
    FS_LOG("DKSMD:   v0 %x/%x v1 %x/%x v2 %x/%x\n", v[0], v[1], v[2], v[3], v[4], v[5]);
    /* First 32 bytes of the data and of the descriptor. */
    for (i = 0; i < 2; i++) {
        uint32_t addr = v[2 + i * 2], len = v[3 + i * 2];
        if (!addr || !len)
            continue;
        if (len > 32)
            len = 32;
        memset(d, 0, sizeof(d));
        memcpy(d, (const void *)addr, len);
        FS_LOG("DKSMD:   %s %x %x %x %x %x %x %x %x\n", i ? "v2" : "v1",
               d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7]);
    }
}

__attribute__((target("arm")))
static int smd_msg(uint32_t entry, uint32_t smsg, uint32_t r2, uint32_t r3,
                   int (*original)(uint32_t, uint32_t, uint32_t, uint32_t),
                   const void *unused)
{
    int rc;
    (void)unused;
    if (entry && smsg)
        smd_log_msg(entry, smsg);
    if (entry && smsg && smd_tunnel_msg(entry, smsg))
        return 0;
    rc = original(entry, smsg, r2, r3);
    if (smd_msg_lines < 40 && rc != 0) {
        smd_msg_lines++;
        FS_LOG("DKSMD:   stock handler returned %x\n", rc);
    }
    return rc;
}

__attribute__((target("arm")))
static int dk_dispatch(uint32_t ctx, uint32_t msg, uint32_t r2, uint32_t r3,
                       int (*original)(uint32_t, uint32_t, uint32_t, uint32_t),
                       const void *unused)
{
    int rc;
    (void)unused;
    dk_net_log_fs();
    /* First /dev/dk request: dkMain's queue exists and no 0x30 open has
     * happened yet (the start clears the table's two entries). Runs in
     * dk's own thread, as the stock call on message 0xd would. */
    if (!smd_start_done) {
        smd_start_done = 1;
        if (smd_probe && smd_start_ok) {
            uint8_t hw[4] = {0, 0, 0, 0};
            int mode = ((sysmode_fn)SYSMODE_FN)();
            uint32_t hrc = ((bsp_hwver_fn)BSP_HWVER_FN)(hw);

            FS_LOG("DKSMD: gate mode %x bsp rc %x hwver %02x%02x%02x%02x table %x\n",
                   mode, hrc, hw[0], hw[1], hw[2], hw[3],
                   *(volatile uint32_t *)SMD_TABLE);
            if (*(volatile uint32_t *)SMD_TABLE == 0) {
                ((smd_start_fn)SMD_START_FN)(SMD_TABLE);
                FS_LOG("DKSMD: drain started, table %x timer %x\n",
                       *(volatile uint32_t *)SMD_TABLE,
                       *(volatile uint32_t *)(SMD_TABLE + 0xb8));
            }
        }
    }
    /* Log the channel open only. Do not read or free the shared-memory
     * ring from this thread; that took the console off the network. */
    if (msg && !open_enter &&
        !*(uint8_t *)(msg + 0x24) && !*(uint8_t *)(msg + 0x25) &&
        !*(uint8_t *)(msg + 0x26) && *(uint8_t *)(msg + 0x27) == 0x30) {
        open_enter = 1;
        FS_LOG("DKTCP: channel open enter\n");
    }
    rc = original(ctx, msg, r2, r3);
    if (open_enter && !open_done) {
        open_done = 1;
        FS_LOG("DKTCP: channel open rc %x\n", rc);
    }
    if ((uint32_t)rc != HEADLESS_ERR || !msg)
        return rc;
    if (service_dk(msg))
        return 0;
    return rc;
}

static bool dk_boot(trampoline_state *regs)
{
    (void)regs;
    if (patch_applied)
        FS_LOG("DKNOHL: headless flag stays 0; FSA param 4 returns 1 (HIO stays off)\n");
    else
        FS_LOG("DKNOHL: signature mismatch; headless flag left stock\n");
    if (channel_gate)
        FS_LOG("DKTCP: channel open stays on the stock path\n");
    if (!client_table_ok)
        FS_LOG("DKTCP: client table literal mismatch; interfaces unknown\n");
    FS_LOG(smd_probe ? "DKSMD: probe on\n" : "DKSMD: signature mismatch; probe off\n");
    if (smd_probe && !smd_start_ok)
        FS_LOG("DKSMD: start signature mismatch; drain left stock\n");
    FS_LOG(smd_tunnel ? "DKSMD: tunnel on (iface 3)\n"
                      : "DKSMD: reply signature mismatch; tunnel off\n");
    dk_net_log_fs();
    return false;
}

__attribute__((target("arm")))
void kern_main(void)
{
    uint32_t mov = stock_word(WRITER_MOV);
    uint32_t str = stock_word(WRITER_STR);
    uint32_t wlit = stock_word(WRITER_LIT);
    uint32_t r0 = stock_word(READER);
    uint32_t r1 = stock_word(READER + 4);
    uint32_t rbx = stock_word(READER_BX);
    uint32_t rlit = stock_word(READER_LIT);
    uint32_t dkm = stock_word(DK_MAIN);
    uint32_t flog = stock_word(FS_LOG_FN);
    uint32_t bla = stock_word(DISPATCH_BL_A);
    uint32_t blb = stock_word(DISPATCH_BL_B);
    int reader_stock = r0 == READER_LDR_LIT && r1 == READER_LDR_VAL &&
                       rbx == READER_BX_STOCK && rlit == HEADLESS_FLAG;
    int reader_done = r0 == READER_MOV1 && r1 == READER_BX_STOCK;
    int writer_stock = mov == WRITER_MOV_STOCK;
    int writer_done = mov == WRITER_MOV_CLEAR;

    if (dkm != DK_MAIN_STOCK || flog != FS_LOG_STOCK)
        return;

    client_table_ok = stock_word(CLIENT_LIT) == CLIENT_TABLE;

    /* SMD probe: all three call sites and both table literals must be
     * stock, or none of it is installed. */
    if (stock_word(DRAIN_BL) == DRAIN_BL_STOCK &&
        stock_word(MSG_BL_A) == MSG_BL_A_STOCK &&
        stock_word(MSG_BL_B) == MSG_BL_B_STOCK &&
        stock_word(SMD_TABLE_LIT) == SMD_TABLE &&
        stock_word(SMD_CH_LIT) == SMD_CH && client_table_ok) {
        trampoline_blreplace(DRAIN_BL, smd_drain);
        trampoline_blreplace(MSG_BL_A, smd_msg);
        trampoline_blreplace(MSG_BL_B, smd_msg);
        smd_probe = 1;
        smd_start_ok = stock_word(SMD_START_FN) == SMD_START_STOCK &&
                       stock_word(BSP_HWVER_FN) == BSP_HWVER_STOCK &&
                       stock_word(SYSMODE_FN) == SYSMODE_STOCK;
        /* Both reply functions: ldr r3,[r0]; mov r2,r0; ... ldr r0,[r2,#12|#8]. */
        smd_tunnel = stock_word(SMD_REPLY_READ_FN) == SMD_REPLY_READ_STOCK &&
                     stock_word(SMD_REPLY_READ_FN + 0x14) == 0xe592000cu &&
                     stock_word(SMD_REPLY_WRITE_FN) == SMD_REPLY_WRITE_STOCK &&
                     stock_word(SMD_REPLY_WRITE_FN + 0x14) == 0xe5920008u;
    }

    patch_applied = (writer_stock || writer_done) && str == WRITER_STR_STOCK &&
                    wlit == HEADLESS_FLAG && (reader_stock || reader_done);
    if (patch_applied && writer_stock)
        U32_PATCH_K(WRITER_MOV, WRITER_MOV_CLEAR);
    if (patch_applied && reader_stock) {
        U32_PATCH_K(READER, READER_MOV1);
        U32_PATCH_K(READER + 4, READER_BX_STOCK);
    }

    if (bla == DISPATCH_BL_A_STOCK && blb == DISPATCH_BL_B_STOCK) {
        trampoline_blreplace(DISPATCH_BL_A, dk_dispatch);
        trampoline_blreplace(DISPATCH_BL_B, dk_dispatch);
    }
    if (stock_word(NULL_DEV_BEQ) == NULL_DEV_BEQ_STOCK) {
        uint32_t ins;

        /* install writes a BL. This site is not a call, so the same
         * offset has to be a plain branch or dkMain's return is lost. */
        trampoline_install(NULL_DEV_BEQ, (void *)null_dev_gate,
                           (void *)(null_dev_gate +
                                    sizeof(null_dev_gate) / sizeof(null_dev_gate[0])));
        ins = stock_word(NULL_DEV_BEQ);
        if ((ins >> 24) == 0xeb) {
            U32_PATCH_K(NULL_DEV_BEQ, (ins & 0x00ffffffu) | 0xea000000u);
            channel_gate = 1;
        }
    }

    trampoline_hook_before_v2(DK_MAIN, dk_boot);
}

void mcp_main(void)
{
    dk_net_start();
}
