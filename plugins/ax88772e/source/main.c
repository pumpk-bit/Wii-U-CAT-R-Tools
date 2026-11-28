#include <stdint.h>
#include <wafel/ios_dynamic.h>
#include <wafel/patch.h>
#include <wafel/trampoline.h>
#include <wafel/utils.h>
#include "uhs.h"

/*
 * AX88772E on the Wii U. The stock USBETH driver speaks the AX88772
 * vendor protocol. An AX88772E (and AX88772D) enumerates as 0x0b95:0x1790
 * with bcdDevice 0x0300 and speaks the AX88179 register protocol. This
 * plugin keeps the 772A/B/C fix and, for the 179-family ids, translates
 * the stock register traffic and the RX/TX headers.
 *
 * Cafe lines are tried three ways. USBETH's logger (0x123ccb50) takes a
 * client index in r0, not a pointer; index 0 is valid and must be passed
 * through. The same module's printf (0x123e2c78) is the sink behind
 * "USBETH Trace:". FS_LOG (0x107f0c84) is what the FS plugins call; it is
 * only mapped in the FS process, so the call is attempted and a fault
 * there means this process cannot see it. debug_printf is GPIO serial.
 *
 * Image: IOSU segments 29/30 (text 0x12300000, rodata 0x12440000).
 * Every site is checked before it is hooked.
 */

#define USBETH_LOG_FN 0x123ccb50u
#define USBETH_LOG_STOCK 0xe92d000cu /* push {r2, r3} */
#define USBETH_PRINT_FN 0x123e2c78u
#define USBETH_PRINT_STOCK 0xe92d000fu /* push {r0-r3} */
#define USBETH_TRACE_BL 0x123b5580u /* "Created interface" log, r0 = client index */
#define FS_LOG_FN 0x107f0c84u
#define FS_LOG_STOCK 0xe92d000fu /* push {r0-r3} */

#define REGISTER_BL 0x123b8f7cu
#define CHIPCODE_BL 0x123ba1ecu
#define RXCTL_MOV 0x123ba608u
#define RXCTL_MOV_STOCK 0xe1a03001u /* mov r3, r1 */

#define READ_CTRL_BL 0x123b9b10u
#define READ_SUBS 0x123b9b14u
#define READ_SUBS_STOCK 0xe2507000u /* subs r7, r0, #0 */
#define WRITE_CTRL_BL 0x123b9910u
#define WRITE_SUBS 0x123b9914u
#define WRITE_SUBS_STOCK 0xe2506000u /* subs r6, r0, #0 */
#define CTRL_FN 0x123bdbd4u

/* RX resubmit pacing in 0x123b80ac: after each RX completion USBETH keeps a
 * value at dev+0x1c0 (adjusted in 0x123b8174 by 50 - fill%, fill% = how full
 * the 16 KB bulk-in buffer was, clamped to -1000..4000, -1000 when > 60%).
 * At >= 1000 (and dev+0x1cc > 99) it arms a timer for that long and queues
 * the next bulk-in only when it fires; below that it queues it at once
 * (0x123b75ac). Light traffic such as PCFS replies (4 KB per transfer, the
 * console's 8 KB TCP window) drives it to 4000, so every received burst
 * waited ~4-5 ms before the next transfer was queued (pcfs5: 0.4 MB/s).
 * The plugin turns the blt into b: always queue at once. */
#define RX_PACE_BLT 0x123b80c4u
#define RX_PACE_BLT_STOCK 0xba000015u /* blt 0x123b8120 */
#define RX_PACE_B 0xea000015u         /* b   0x123b8120 */
#define RX_CASE 0x123b95e0u
#define RX_CASE_STOCK 0xe597e004u /* ldr lr, [r7, #4] */
#define RX_BUF_OFF 0x444u

#define TX_HDR_DONE 0x123b97c4u
#define TX_HDR_DONE_STOCK 0xe59e1038u /* ldr r1, [lr, #56] */
#define TX_LEN_OFF 0x40u

#define INT_CASE 0x123b95a0u
#define INT_CASE_STOCK 0xe5973010u /* ldr r3, [r7, #16] */

/* Controller link change: (ctx, ctrl, 3 = down / 4 = up), only acted on when
 * the controller is open (state 3); queues an event for the interface FSM. */
#define LINK_EVENT_FN 0x123b71c8u
#define LINK_EVENT_STOCK 0xe92d4030u /* push {r4, r5, lr} */
typedef void (*link_event_fn)(uint32_t, uint32_t, uint32_t);
#define LINK_EVENT ((link_event_fn)LINK_EVENT_FN)

#define VIDPID_PTR 0x12456dd0u

#define AX_ACCESS_MAC 0x01u
#define AX_ACCESS_PHY 0x02u
#define AX_PHY_ID 0x03u
#define AX_RX_CTL 0x0bu
#define AX_NODE_ID 0x10u
#define AX_MEDIUM 0x22u
#define AX_MONITOR 0x24u
#define AX_PHYPWR 0x26u
#define AX_BULKIN 0x2eu
#define AX_CLK 0x33u
#define AX_RXCOE 0x34u
#define AX_TXCOE 0x35u
#define AX_PAUSE_LO 0x54u
#define AX_PAUSE_HI 0x55u
#define AX_PHYSR 0x11u

#define AX_RXCTL_VAL 0x03aau /* drop crc, ip align, start, accept phys/mcast/bcast */
#define AX_MEDIUM_100 0x0332u
#define AX_MEDIUM_1000 0x013bu
#define CTRL_TIMEOUT 0x00989680u

#define AX_CMD_SET_SW_MII 0x06u
#define AX_CMD_MII_READ 0x07u
#define AX_CMD_MII_WRITE 0x08u
#define AX_CMD_MII_STAT 0x09u
#define AX_CMD_SET_HW_MII 0x0au
#define AX_CMD_EEPROM 0x0bu
#define AX_CMD_READ_NODE 0x13u
#define AX_CMD_WRITE_NODE 0x14u
#define AX_CMD_WRITE_MEDIUM 0x1bu

/* AX88179A family (chip version 6: AX88179A/B, AX88772D/E). Values from the
 * Linux "ax88179_178a: Add support for AX88179A-based chips" series (v3). */
#define AX_CHIP_STATUS 0x05u
#define AX_REQ_RELOAD 0x06u     /* reload EEPROM/eFuse, no data */
#define AX_REQ_FW_MODE 0x08u    /* value 1: link speed byte in the interrupt */
#define AX_REQ_BL 0x2au         /* bootloader space, firmware version at 0xfd-0xff */
#define AX_REQ_PHY_POWER 0x31u  /* 1 byte, 0x02 = PHY on */
#define AX_REQ_AUTODETACH 0xc0u
#define A_ETH_TX_GAP 0x0du
#define A_BFM_DATA 0x0eu
#define A_VLAN_CTL 0x2bu
#define A_BM_INT_MASK 0x41u
#define A_BM_RX_DMA 0x43u
#define A_BM_TX_DMA 0x46u
#define A_RX_STATUS_CDC 0x6du
#define A_ARC_CTRL 0x9eu
#define A_SWP_CTRL 0xb1u
#define A_CDC_DELAY_TX 0xb5u
#define A_MAC_PATH 0xb7u
#define A_NEW_PAUSE 0xb8u
#define A_BULK_OUT_CTRL 0xb9u
#define A_RX_DATA_CDC_CNT 0xc0u
#define A_TX_HDR_CKSUM 0xccu
#define A_CDC_ECM_CTRL 0xb0u
#define AX_RX_FREE_BUF 0x57u   /* RX free buffer count, 2 bytes */
#define TX_PATH_READS 1      /* chip reads from the send path (diagnostic) */
#define A_EP5_EHR 0xf9u
#define A_RXCTL_VAL 0x01aau     /* drop crc, start, ap, amall, ab (no ip align) */
#define A_MEDIUM_BASE 0x0130u   /* receive + both pause directions */
#define A_RX_OK 0x00000800u
#define A_RX_DROP 0x80000000u
#define A_TX_DROP_PAD 0x10000000u

#define MII_PHYSID1 0x02u
#define ASIX_PHYSID1 0x003bu

typedef int (*usbeth_log_fn)(void *, int, const char *, ...);
typedef int (*print_fn)(const char *, ...);
typedef int (*ctrl_fn)(void *, uint32_t, uint32_t, uint32_t,
                       uint32_t, uint32_t, void *, uint32_t, uint32_t);

#define USBETH_LOG ((usbeth_log_fn)USBETH_LOG_FN)
#define USBETH_PRINT ((print_fn)USBETH_PRINT_FN)
#define FS_LOG ((print_fn)FS_LOG_FN)
#define USB_CTRL ((ctrl_fn)CTRL_FN)

static uint16_t known_ids[][2] = {
    { 0x0b95, 0x7720 },
    { 0x0b95, 0x772a },
    { 0x0b95, 0x772b },
    { 0x0b95, 0x772c },
    { 0x05ac, 0x1402 },
    { 0x0b95, 0x1790 }, /* AX88179 and AX88772D/E (bcdDevice tells them apart) */
    { 0x0b95, 0x178a },
    { 0x0b95, 0x772d },
    { 0x0b95, 0x772e },
};
#define KNOWN_ID_COUNT (sizeof(known_ids) / sizeof(known_ids[0]))

static void (*stock_probe)(void *context, UhsInterfaceProfile *profile);
static uint32_t log_id;
static int log_ok;
static int log_have;
static int log_from_stock;
static int print_ok;
static int fs_ok;
static int ax179;
static int translator_on;
static int hw_ready;
static int fast_phy;
static uint16_t bcd_device;
static uint8_t mac[6];
static int have_mac;
static int sw_mii;
static int mii_logs;
static int phyid_logged;
static int a179;          /* chip version 6: AX88179A family path */
/* Last 179A status interrupt: link, 100 Mbit, full duplex (default 100 full). */
static int status_link;
static int status_100 = 1;
static int status_full = 1;
static int link_logs;
static int override;
static int override_rc;
static int tx_logs;
static int rx_logs;
static int medium_logs;
static uint8_t rx_bounce[16 * 1024]; /* stock bulk-in transfers are 16 KB */
/* USB DMA buffer: own cache lines, or cache maintenance on it clobbers the
 * variables next to it (29.log: the relink flag kept reverting to 0). */
static uint8_t scratch[64] __attribute__((aligned(64)));
static void *last_ctx;
static uint32_t last_handle;
static uint32_t free_at_link = 0xffff;
static int relinked; /* link setting redone since the last link-up */
static int relink_logs;
static int freebuf_logs;
static int miiw_logs;
static int restart_logs;
static int stop_logs;
static int int_logs;      /* defined with the interrupt hook below */
static int int_link = -1;

static uint32_t word_at(uint32_t addr)
{
    return *(volatile uint32_t *)ios_elf_vaddr_to_paddr(addr);
}

static uint32_t bl_target(uint32_t addr, uint32_t ins)
{
    int32_t off = (int32_t)(ins << 8) >> 6;

    return addr + 8u + (uint32_t)off;
}

struct log_line {
    const char *fmt;
    uint32_t a, b, c;
};

static struct log_line pending[16];
static int npending;

static void emit(const char *fmt, uint32_t a, uint32_t b, uint32_t c)
{
    if (log_have && log_ok)
        USBETH_LOG((void *)log_id, 3, fmt, a, b, c);
    if (print_ok)
        USBETH_PRINT(fmt, a, b, c);
    /* One attempt. A fault here means USBETH cannot execute the FS logger. */
    if (fs_ok) {
        fs_ok = 0;
        FS_LOG(fmt, a, b, c);
    }
}

static void cafe(const char *fmt, uint32_t a, uint32_t b, uint32_t c)
{
    if (log_have || print_ok || fs_ok)
        emit(fmt, a, b, c);
    if (!log_from_stock && npending < (int)(sizeof(pending) / sizeof(pending[0]))) {
        pending[npending].fmt = fmt;
        pending[npending].a = a;
        pending[npending].b = b;
        pending[npending].c = c;
        npending++;
    }
}

static void flush_logs(void)
{
    int i;
    int n = npending;

    npending = 0;
    for (i = 0; i < n; i++)
        emit(pending[i].fmt, pending[i].a, pending[i].b, pending[i].c);
}

/* r0 of a real USBETH log call is the client index. Index 0 is in use. */
static void note_log_id(uint32_t id, int from_stock)
{
    if (id > 31)
        return;
    if (log_from_stock && !from_stock)
        return;
    log_id = id;
    log_have = 1;
    if (from_stock)
        log_from_stock = 1;
    flush_logs();
}

static void capture_log_id(trampoline_state *regs)
{
    note_log_id((uint32_t)regs->r[0], 1);
}

static int is_179(uint16_t vid, uint16_t pid)
{
    return vid == 0x0b95 &&
           (pid == 0x1790 || pid == 0x178a || pid == 0x772d || pid == 0x772e);
}

static void put_le16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static uint16_t get_le16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

static uint32_t get_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void put_le32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static int ctrl(void *ctx, uint32_t handle, uint32_t req, uint32_t type,
                uint32_t val, uint32_t idx, void *data, uint32_t len)
{
    return USB_CTRL(ctx, handle, req, type, val, idx, data, len, CTRL_TIMEOUT);
}

static int mac_write(void *ctx, uint32_t handle, uint8_t *buf,
                     uint32_t reg, uint32_t len)
{
    return ctrl(ctx, handle, AX_ACCESS_MAC, 0x40, reg, len, buf, len);
}

static int mac_read(void *ctx, uint32_t handle, uint8_t *buf,
                    uint32_t reg, uint32_t len)
{
    return ctrl(ctx, handle, AX_ACCESS_MAC, 0xc0, reg, len, buf, len);
}

static int phy_read(void *ctx, uint32_t handle, uint8_t *buf, uint32_t reg)
{
    return ctrl(ctx, handle, AX_ACCESS_PHY, 0xc0, AX_PHY_ID, reg, buf, 2);
}

static int phy_write(void *ctx, uint32_t handle, uint8_t *buf, uint32_t reg)
{
    return ctrl(ctx, handle, AX_ACCESS_PHY, 0x40, AX_PHY_ID, reg, buf, 2);
}

static void set_medium(void *ctx, uint32_t handle, uint8_t *buf)
{
    uint16_t sr = 0;
    uint16_t mode = 0x0130; /* receive + both pause directions */
    int rc = phy_read(ctx, handle, buf, AX_PHYSR);

    if (rc >= 0)
        sr = get_le16(buf);
    if (sr & 0x0400) {
        if (sr & 0x8000)
            mode |= 0x0009; /* giga + 125 MHz */
        else if (sr & 0x4000)
            mode |= 0x0200; /* 100 */
        if (sr & 0x2000)
            mode |= 0x0002;
    } else if (fast_phy) {
        mode |= 0x0202; /* 100 full, until the link comes up */
    } else {
        mode |= 0x000b; /* giga full */
    }
    put_le16(buf, mode);
    rc = mac_write(ctx, handle, buf, AX_MEDIUM, 2);
    if (medium_logs < 4) {
        medium_logs++;
        cafe("AX772E: medium %x physr %x rc %x\n", mode, sr, (uint32_t)rc);
    }
}

/* Vendor request outside the MAC register space. */
static int vreq(void *ctx, uint32_t handle, uint32_t req, uint32_t type,
                uint32_t val, uint8_t *buf, uint32_t len)
{
    return ctrl(ctx, handle, req, type, val, 0, buf, len);
}

static int mac_write8(void *ctx, uint32_t handle, uint8_t *buf,
                      uint32_t reg, uint8_t v)
{
    buf[0] = v;
    return mac_write(ctx, handle, buf, reg, 1);
}

static int mac_write16(void *ctx, uint32_t handle, uint8_t *buf,
                       uint32_t reg, uint16_t v)
{
    put_le16(buf, v);
    return mac_write(ctx, handle, buf, reg, 2);
}

/* ax88179a_bind + ax88179_reset (chip version >= 6) without VLAN/WoL. */
static int a179_init(void *ctx, uint32_t handle, uint8_t *buf)
{
    int rc;
    int i;

    if (vreq(ctx, handle, AX_REQ_BL, 0xc0, 0xfd, buf, 1) >= 0 &&
        vreq(ctx, handle, AX_REQ_BL, 0xc0, 0xfe, buf + 1, 1) >= 0 &&
        vreq(ctx, handle, AX_REQ_BL, 0xc0, 0xff, buf + 2, 1) >= 0)
        cafe("AX772E: 179a fw %x.%x.%x\n", buf[0], buf[1], buf[2]);

    rc = vreq(ctx, handle, AX_REQ_FW_MODE, 0x40, 1, buf, 0);
    cafe("AX772E: 179a fw mode rc %x\n", (uint32_t)rc, 0, 0);
    rc = vreq(ctx, handle, AX_REQ_RELOAD, 0x40, 0, buf, 0);
    cafe("AX772E: 179a reload rc %x\n", (uint32_t)rc, 0, 0);
    udelay(100000);

    /* ASIX ax88179a_bind (default build, no ENABLE_LPM): USB device
     * controller register 0x3404, clear bits 8-10/13-14 mask, set 0x6100. */
    if (vreq(ctx, handle, 0x81, 0xc0, 0x3404, buf, 4) >= 0) {
        uint32_t dc = get_le32(buf);

        put_le32(buf, (dc & 0xffff98ffu) | 0x00006100u);
        rc = vreq(ctx, handle, 0x81, 0x40, 0x3404, buf, 4);
        cafe("AX772E: 179a usb dc was %x rc %x\n", dc, (uint32_t)rc, 0);
    }

    buf[0] = 0x02;
    rc = vreq(ctx, handle, AX_REQ_PHY_POWER, 0x40, 0, buf, 1);
    udelay(250000);
    buf[0] = 0;
    i = vreq(ctx, handle, AX_REQ_PHY_POWER, 0xc0, 0, buf, 1);
    cafe("AX772E: 179a phy power rc %x readback %x rc %x\n",
         (uint32_t)rc, buf[0], (uint32_t)i);
    if (rc < 0)
        return rc;

    vreq(ctx, handle, AX_REQ_AUTODETACH, 0x40, 0x0500, buf, 0);
    mac_write8(ctx, handle, buf, A_BULK_OUT_CTRL, 0x02);
    mac_write16(ctx, handle, buf, AX_RX_CTL, 0);
    mac_write8(ctx, handle, buf, AX_PAUSE_LO, 0x04);
    mac_write8(ctx, handle, buf, AX_PAUSE_HI, 0x10);
    mac_write8(ctx, handle, buf, A_VLAN_CTL, 0);
    mac_write8(ctx, handle, buf, A_BM_INT_MASK, 0xff);
    mac_write8(ctx, handle, buf, A_BM_RX_DMA, 0);
    mac_write8(ctx, handle, buf, A_BM_TX_DMA, 0);
    mac_write8(ctx, handle, buf, A_ARC_CTRL, 0);
    mac_write8(ctx, handle, buf, A_SWP_CTRL, 0);
    /* ASIX ax88179a_hw_init: TX header checksum on (the descriptor carries
     * a 7-bit sum of its length, see tx_before). */
    mac_write8(ctx, handle, buf, A_TX_HDR_CKSUM, 0x01);

    rc = mac_read(ctx, handle, buf, AX_NODE_ID, 6);
    if (rc < 0)
        return rc;
    for (i = 0; i < 6; i++)
        mac[i] = buf[i];
    have_mac = 1;
    cafe("AX772E: mac %02x%02x%02x\n", mac[0], mac[1], mac[2]);
    cafe("AX772E: mac %02x%02x%02x\n", mac[3], mac[4], mac[5]);

    mac_write8(ctx, handle, buf, AX_RXCOE, 0x67);
    mac_write8(ctx, handle, buf, AX_TXCOE, 0x67);
    rc = mac_write16(ctx, handle, buf, AX_RX_CTL, A_RXCTL_VAL);
    if (rc < 0)
        return rc;
    mac_write8(ctx, handle, buf, A_MAC_PATH, 0);
    if (mac_read(ctx, handle, buf, AX_MONITOR, 1) >= 0)
        mac_write8(ctx, handle, buf, AX_MONITOR, (uint8_t)((buf[0] & 0xe0u) | 0x04u));
    else
        mac_write8(ctx, handle, buf, AX_MONITOR, 0x04);
    if (mac_read(ctx, handle, buf, AX_MEDIUM, 2) >= 0) {
        uint16_t med = get_le16(buf);

        mac_write16(ctx, handle, buf, AX_MEDIUM, (uint16_t)(med & ~1u));
        mac_write16(ctx, handle, buf, AX_MEDIUM, (uint16_t)(med | 1u));
    }
    mac_write8(ctx, handle, buf, A_BFM_DATA, 0);
    /* These chips start in CDC mode. ASIX clears the CDC/ECM control
     * register (0xb0) last in ax88179a_hw_init; without it the vendor
     * bulk-in endpoint never returned data (27.log). */
    {
        uint32_t old = 0xff;

        if (mac_read(ctx, handle, buf, A_CDC_ECM_CTRL, 1) >= 0)
            old = buf[0];
        rc = mac_write8(ctx, handle, buf, A_CDC_ECM_CTRL, 0);
        cafe("AX772E: 179a cdc ecm ctrl was %x rc %x\n", old, (uint32_t)rc, 0);
    }
    mac_write16(ctx, handle, buf, AX_MEDIUM, A_MEDIUM_BASE | 0x0002);

    /* BMCR: power-down off, autoneg on and restarted. */
    if (phy_read(ctx, handle, buf, 0) >= 0) {
        uint16_t bmcr = get_le16(buf);
        put_le16(buf, (uint16_t)((bmcr & ~0x0800u) | 0x1200u));
        phy_write(ctx, handle, buf, 0);
        cafe("AX772E: 179a bmcr was %x\n", bmcr, 0, 0);
    }
    return 0;
}

/* ax88179a_mac_link_up for 100 Mbit (also used for 10). The stock driver
 * hands us its AX88772 medium value: bit 1 full duplex, bit 9 100 Mbit. */
static void a179_link_up(void *ctx, uint32_t handle, uint8_t *buf, uint32_t stock)
{
    /* Order and values from a Windows USB capture of this adapter (ASIX
     * 2.22.1.0, REV_0300, 100M full): one-byte writes 6d c0 6f c3 0d b2 b3
     * b4 0f b5 6e, then 2e = 05 c0 04 06 0f, 0b, 0b, b7 = 03, 22 last.
     * Per-frame bulk-in mode (32.log) changed nothing, so this is back to
     * the value Windows and ASIX's Linux driver use. A shorter aggregation
     * timer (0x0040 instead of 0x04c0) made PCFS loading slower, not faster
     * (121143.log: Unity init 209 s instead of 117 s). */
    static const uint8_t bulk_full[5] = { 5, 0xc0, 0x04, 0x06, 0x0f };
    static const uint8_t bulk_half[5] = { 7, 0xc0, 0x04, 0x06, 0x0f };
    int full = (stock & 0x0002u) != 0;
    uint16_t mode = A_MEDIUM_BASE | (full ? 0x0002u : 0);
    int rc;
    int i;

    /* Teardown and link-up both pass through here on USBETH's own thread:
     * a free count below the link-up value means frames sit in the chip. */
    if (freebuf_logs < 12 && mac_read(ctx, handle, buf, AX_RX_FREE_BUF, 2) >= 0) {
        freebuf_logs++;
        cafe("AX772E: 179a rx free %x before link setting\n", get_le16(buf), 0, 0);
    }
    mac_write8(ctx, handle, buf, A_RX_STATUS_CDC, 0x78);
    mac_write8(ctx, handle, buf, A_RX_DATA_CDC_CNT, 0x40);
    mac_write8(ctx, handle, buf, 0x6f, 0x00);
    mac_write8(ctx, handle, buf, 0xc3, 0x00);
    mac_write8(ctx, handle, buf, A_ETH_TX_GAP, 0x05);
    mac_write8(ctx, handle, buf, 0xb2, 0x90);
    mac_write8(ctx, handle, buf, 0xb3, 0xe2);
    mac_write8(ctx, handle, buf, 0xb4, 0x7d);
    mac_write8(ctx, handle, buf, 0x0f, 0x01);
    mac_write8(ctx, handle, buf, A_CDC_DELAY_TX, 0xa5);
    mac_write8(ctx, handle, buf, 0x6e, 0x70);
    for (i = 0; i < 5; i++)
        buf[i] = full ? bulk_full[i] : bulk_half[i];
    mac_write(ctx, handle, buf, AX_BULKIN, 5);
    mac_write16(ctx, handle, buf, AX_RX_CTL, 0x0018);
    mac_write16(ctx, handle, buf, AX_RX_CTL, A_RXCTL_VAL);
    rc = mac_write8(ctx, handle, buf, A_MAC_PATH, 0x03);
    mac_write16(ctx, handle, buf, AX_MEDIUM, mode);
    if (link_logs < 40) {
        uint32_t rxctl = 0xffff, med = 0xffff, path = 0xff;

        link_logs++;
        cafe("AX772E: 179a link up stock %x medium %x rc %x\n", stock, mode,
             (uint32_t)rc);
        if (mac_read(ctx, handle, buf, AX_RX_CTL, 2) >= 0)
            rxctl = get_le16(buf);
        if (mac_read(ctx, handle, buf, AX_MEDIUM, 2) >= 0)
            med = get_le16(buf);
        if (mac_read(ctx, handle, buf, A_MAC_PATH, 1) >= 0)
            path = buf[0];
        cafe("AX772E: 179a readback rxctl %x medium %x path %x\n", rxctl, med, path);
    }
}

static int hw_init(void *ctx, uint32_t handle, uint8_t *buf)
{
    int rc;
    static const uint8_t bulkin_100[5] = { 7, 0xae, 7, 0x18, 0xff };
    static const uint8_t bulkin_1000[5] = { 7, 0x20, 3, 0x16, 0xff };

    /* Callers hand us the 2-byte EEPROM slot. Keep the USB data local. */
    buf = scratch;

    if (hw_ready)
        return 0;
    hw_ready = 1;
    cafe("AX772E: init bcd %x\n", bcd_device, 0, 0);

    a179 = 0;
    buf[0] = 0;
    rc = mac_read(ctx, handle, buf, AX_CHIP_STATUS, 1);
    cafe("AX772E: chip status %x rc %x\n", buf[0], (uint32_t)rc, 0);
    if (rc >= 0 && (buf[0] >> 4) == 6) {
        a179 = 1;
        rc = a179_init(ctx, handle, buf);
        if (rc < 0)
            goto out;
        cafe("AX772E: init done (179a)\n", 0, 0, 0);
        return 0;
    }

    put_le16(buf, 0);
    rc = mac_write(ctx, handle, buf, AX_PHYPWR, 2);
    if (rc < 0)
        goto out;
    put_le16(buf, 0x0020);
    rc = mac_write(ctx, handle, buf, AX_PHYPWR, 2);
    if (rc < 0)
        goto out;
    udelay(200000);

    buf[0] = 0x03;
    rc = mac_write(ctx, handle, buf, AX_CLK, 1);
    if (rc < 0)
        goto out;
    udelay(100000);

    rc = mac_read(ctx, handle, buf, AX_NODE_ID, 6);
    if (rc < 0)
        goto out;
    for (rc = 0; rc < 6; rc++)
        mac[rc] = buf[rc];
    have_mac = 1;
    cafe("AX772E: mac %02x%02x%02x\n", mac[0], mac[1], mac[2]);
    cafe("AX772E: mac %02x%02x%02x\n", mac[3], mac[4], mac[5]);

    for (rc = 0; rc < 5; rc++)
        buf[rc] = fast_phy ? bulkin_100[rc] : bulkin_1000[rc];
    rc = mac_write(ctx, handle, buf, AX_BULKIN, 5);
    if (rc < 0)
        goto out;

    buf[0] = 0x34;
    mac_write(ctx, handle, buf, AX_PAUSE_HI, 1);
    buf[0] = 0x52;
    mac_write(ctx, handle, buf, AX_PAUSE_LO, 1);

    buf[0] = 0x67;
    mac_write(ctx, handle, buf, AX_RXCOE, 1);
    buf[0] = 0x67;
    mac_write(ctx, handle, buf, AX_TXCOE, 1);

    put_le16(buf, AX_RXCTL_VAL);
    rc = mac_write(ctx, handle, buf, AX_RX_CTL, 2);
    if (rc < 0)
        goto out;

    buf[0] = 0x64;
    mac_write(ctx, handle, buf, AX_MONITOR, 1);
    set_medium(ctx, handle, buf);

    /* BMCR: enable and restart autoneg. */
    if (phy_read(ctx, handle, buf, 0) >= 0) {
        put_le16(buf, (uint16_t)(get_le16(buf) | 0x1200));
        phy_write(ctx, handle, buf, 0);
    }
    cafe("AX772E: init done\n", 0, 0, 0);
    return 0;
out:
    cafe("AX772E: init failed %x\n", (uint32_t)rc, 0, 0);
    return rc;
}

static int on_read(void *ctx, uint32_t handle, uint32_t cmd, uint32_t val,
                   uint32_t idx, uint8_t *data, uint32_t len)
{
    int rc;

    last_ctx = ctx;
    last_handle = handle;
    rc = hw_init(ctx, handle, data);
    uint32_t i;

    if (rc < 0)
        return rc;
    if (cmd == AX_CMD_EEPROM && len >= 2 && data) {
        data[0] = 0;
        data[1] = 0;
        if (have_mac && val >= 4 && val <= 6) {
            i = (val - 4) * 2;
            data[0] = mac[i];
            data[1] = mac[i + 1];
        }
        return 0;
    }
    /* Cmd 0x06 waits until this bit is 1, cmd 0x0a until it is 0.
     * A stuck 0 is status 22:-1014 and the driver releases the device. */
    if (cmd == AX_CMD_MII_STAT && len >= 1 && data) {
        data[0] = sw_mii ? 1 : 0;
        if (len >= 2)
            data[1] = 0;
        return 0;
    }
    /* 179A family: the stock link query (0x123b9cf8: BMCR, ANLPAR, BMSR) runs
     * when the interface enters LINK-UP, inline from the link interrupt on
     * USBETH's callback thread. A real control transfer there never
     * completes (24.log hung right after the third link-up event). Answer
     * those three registers from the last status interrupt instead. */
    if (cmd == AX_CMD_MII_READ && len >= 2 && data && a179 &&
        (idx == 0 || idx == 1 || idx == 5)) {
        uint16_t v;

        /* ASIX runs ax88179a_link_reset after the PHY reports link. The
         * stock driver only writes the medium during admin setup, before
         * the cable link exists. Redo the link setting here: this is the
         * stock link query on the FSM thread, right after LINK-UP. */
        if (status_link && !relinked) {
            uint32_t rxctl = 0xffff, med = 0xffff, path = 0xff, usb = 0xff;

            relinked = 1;
            if (mac_read(ctx, handle, scratch, AX_RX_CTL, 2) >= 0)
                rxctl = get_le16(scratch);
            if (mac_read(ctx, handle, scratch, AX_MEDIUM, 2) >= 0)
                med = get_le16(scratch);
            if (mac_read(ctx, handle, scratch, A_MAC_PATH, 1) >= 0)
                path = scratch[0];
            if (mac_read(ctx, handle, scratch, 0x02, 1) >= 0)
                usb = scratch[0];
            if (mac_read(ctx, handle, scratch, AX_RX_FREE_BUF, 2) >= 0)
                free_at_link = get_le16(scratch);
            if (relink_logs < 8) {
                relink_logs++;
                cafe("AX772E: at link up rxctl %x medium %x path %x\n", rxctl, med, path);
                cafe("AX772E: at link up usb link status %x rx free %x\n", usb,
                     free_at_link, 0);
            }
            a179_link_up(ctx, handle, scratch, status_full ? 0x0002u : 0);
        }

        if (idx == 0)
            v = (uint16_t)(0x1000u | (status_100 ? 0x2000u : 0) |
                           (status_full ? 0x0100u : 0));
        else if (idx == 1)
            v = (uint16_t)(0x782du | (status_link ? 0x0004u : 0));
        else
            v = (uint16_t)(0x4401u | (status_100 ? (status_full ? 0x0100u : 0x0080u)
                                                  : (status_full ? 0x0040u : 0x0020u)));
        put_le16(data, v);
        if (mii_logs < 24) {
            mii_logs++;
            cafe("AX772E: mii reg %x reported %x (from status)\n", idx, v, 0);
        }
        return 0;
    }
    if (cmd == AX_CMD_MII_READ && len >= 2 && data) {
        rc = phy_read(ctx, handle, data, idx);
        /* Stock USBETH (0x123ba38c) reads PHYSID1 up to 50 times and only
         * goes on when it is the ASIX value 0x003b; otherwise it fails with
         * 22:-1014. The 772E's PHY reports its own id, so report ASIX's. */
        if (idx == MII_PHYSID1 && rc >= 0 && get_le16(data) != ASIX_PHYSID1) {
            if (!phyid_logged) {
                phyid_logged = 1;
                cafe("AX772E: phy id %x reported as %x\n", get_le16(data),
                     ASIX_PHYSID1, 0);
            }
            put_le16(data, ASIX_PHYSID1);
        }
        if (mii_logs < 24) {
            mii_logs++;
            cafe("AX772E: mii reg %x data %x rc %x\n", idx,
                 get_le16(data), (uint32_t)rc);
        }
        return rc;
    }
    if (cmd == AX_CMD_READ_NODE && len >= 6 && data && have_mac) {
        for (i = 0; i < 6; i++)
            data[i] = mac[i];
        return 0;
    }
    if (data) {
        for (i = 0; i < len && i < 16; i++)
            data[i] = 0;
    }
    return 0;
}

static int on_write(void *ctx, uint32_t handle, uint32_t cmd, uint32_t val,
                    uint32_t idx, uint8_t *data, uint32_t len)
{
    int rc = hw_init(ctx, handle, data ? data : mac);
    uint32_t i;

    if (rc < 0)
        return rc;
    if (cmd == AX_CMD_WRITE_NODE && data && len >= 6) {
        for (i = 0; i < 6; i++)
            mac[i] = data[i];
        have_mac = 1;
        return mac_write(ctx, handle, data, AX_NODE_ID, 6);
    }
    if (cmd == AX_CMD_SET_SW_MII) {
        sw_mii = 1;
        return 0;
    }
    if (cmd == AX_CMD_SET_HW_MII) {
        sw_mii = 0;
        return 0;
    }
    if (cmd == AX_CMD_MII_WRITE && data && len >= 2) {
        if (a179 && miiw_logs < 16) {
            miiw_logs++;
            cafe("AX772E: mii write reg %x value %x\n", idx, get_le16(data), 0);
        }
        return phy_write(ctx, handle, data, idx);
    }
    /* The stock medium write carries its value in wValue and has no data. */
    if (cmd == AX_CMD_WRITE_MEDIUM && a179 && (val & 0x0300u) == 0x0200u) {
        /* Admin down (0x206: 100M, receive off; at every restart, after
         * USBETH disabled the endpoints): stop the receiver. The link-up
         * sequence used to run here too and turned receive back on
         * (readback rxctl 1aa medium 132), so the chip kept taking frames
         * from the PC with nobody reading them while the console
         * restarted. ASIX's stop clears the medium receive bit and RX_CTL.
         * Boot also writes 0x206 between its admin ups; the next 0x336 runs
         * the link-up sequence again. The init (0) and link-up (2) writes
         * have no speed bit and keep the link-up sequence. */
        int drc;

        mac_write16(ctx, handle, scratch, AX_RX_CTL, 0);
        drc = mac_write16(ctx, handle, scratch, AX_MEDIUM,
                          A_MEDIUM_BASE & ~0x0100u);
        status_link = 0;
        relinked = 0;
        if (stop_logs < 8) {
            stop_logs++;
            cafe("AX772E: admin down, receive stopped stock %x rc %x\n", val,
                 (uint32_t)drc, 0);
        }
    }
    else if (cmd == AX_CMD_WRITE_MEDIUM && a179) {
        a179_link_up(ctx, handle, scratch, val);
        /* Admin up (medium with receive enabled, 0x336) waits 5 s for a
         * link-up interrupt. The 179A family only interrupts on a change, so
         * when the cable link survived the last admin down (35-3.log, 2nd
         * and 3rd test) none comes and the FSM gives up with 103-1607.
         * Restart autonegotiation to get a fresh down/up pair. */
        if (val & 0x0100u) {
            int prc;

            put_le16(scratch, 0x1200u); /* autoneg enable + restart */
            prc = phy_write(ctx, handle, scratch, 0);
            status_link = 0;
            relinked = 0;
            int_logs = 0;
            if (restart_logs < 8) {
                restart_logs++;
                cafe("AX772E: admin up, autoneg restarted rc %x\n", (uint32_t)prc, 0, 0);
            }
        }
    }
    else if (cmd == AX_CMD_WRITE_MEDIUM && data)
        set_medium(ctx, handle, data);
    return 0;
}

/* usb_ctrl returns before touching the wire when its context is NULL.
 * The following instruction's hook replaces r0 with our result. */
static void neuter(trampoline_state *regs)
{
    regs->r[0] = 0;
}

/* Interface FSM event entry 0x123b5040(ctx, iface, event, arg); the state
 * is the word at iface + 8. Its trace lines are gated by a flag in USBETH's
 * context, so the two call sites are wrapped instead (read-only). */
static const uint32_t fsm_event_sites[] = { 0x123b52d0u, 0x123b5568u };
#define FSM_EVENT_FN 0x123b5040u
static int fsm_event_logs;

typedef int (*fsm_event_fn)(uint32_t, uint32_t, uint32_t, uint32_t);

/* Queued events (admin up/down, link changes) reach 0x123b5040 through the
 * handler at 0x123b52a8, which ends in a tail branch (b 0x123b5040 at
 * 0x123b52b8). hook_before_v2 installs a plain branch for a non-BL site, so
 * LR stays intact, and then continues to the branch target. */
#define FSM_QUEUED_B 0x123b52b8u
static int fsm_queued_logs;

static bool fsm_queued_hook(trampoline_state *regs)
{
    uint32_t iface = (uint32_t)regs->r[1];

    if (ax179 && fsm_queued_logs < 120) {
        fsm_queued_logs++;
        cafe("AX772E: fsm queued event %x arg %x state %x\n",
             (uint32_t)regs->r[2], (uint32_t)regs->r[3],
             iface ? *(volatile uint32_t *)(iface + 8) : 0xffu);
    }
    return true;
}

static int fsm_event_wrap(uint32_t ctx, uint32_t iface, uint32_t event,
                          uint32_t arg, uint32_t original, uint32_t saved_lr)
{
    uint32_t before = iface ? *(volatile uint32_t *)(iface + 8) : 0xffu;
    int rc = ((fsm_event_fn)original)(ctx, iface, event, arg);
    uint32_t after = iface ? *(volatile uint32_t *)(iface + 8) : 0xffu;

    (void)saved_lr;
    if (ax179 && fsm_event_logs < 120) {
        fsm_event_logs++;
        cafe("AX772E: fsm event %x arg %x\n", event, arg, 0);
        cafe("AX772E: fsm state %x -> %x rc %x\n", before, after, (uint32_t)rc);
    }
    return rc;
}

static void read_before(trampoline_state *regs)
{
    override = 0;
    if (!ax179)
        return;
    override_rc = on_read((void *)regs->r[0], (uint32_t)regs->r[1],
                          (uint32_t)regs->r[2], regs->stack[0], regs->stack[1],
                          (uint8_t *)regs->stack[2], regs->stack[3]);
    override = 1;
    neuter(regs);
}

static void write_before(trampoline_state *regs)
{
    override = 0;
    if (!ax179)
        return;
    override_rc = on_write((void *)regs->r[0], (uint32_t)regs->r[1],
                           (uint32_t)regs->r[2], regs->stack[0], regs->stack[1],
                           (uint8_t *)regs->stack[2], regs->stack[3]);
    override = 1;
    neuter(regs);
}

static void read_after(trampoline_state *regs)
{
    if (override)
        regs->r[0] = override_rc;
}

static void write_after(trampoline_state *regs)
{
    if (override)
        regs->r[0] = override_rc;
}

/* Repack one AX88179 bulk buffer into the 4-byte headers the stock parser
 * already understands: le16 length, le16 ~length, then the frame. */
static int rx_repack(uint8_t *buf, uint32_t len)
{
    uint32_t hdr;
    uint32_t pkt_cnt;
    uint32_t hdr_off;
    uint32_t data_off = 0;
    uint32_t out = 0;
    uint32_t n;

    if (len < 8 || len > sizeof(rx_bounce))
        return -1;
    hdr = get_le32(buf + len - 4);
    pkt_cnt = hdr & 0xffffu;
    hdr_off = hdr >> 16;
    if (pkt_cnt == 0 || pkt_cnt > 40 || hdr_off + pkt_cnt * 4 > len - 4)
        return -1;

    for (n = 0; n < pkt_cnt; n++) {
        uint32_t meta = get_le32(buf + hdr_off + n * 4);
        uint32_t pkt_len = (meta >> 16) & 0x1fffu;
        uint32_t step;
        uint32_t frame;
        uint32_t i;

        if (pkt_len == 0)
            continue;
        step = (pkt_len + 7u) & ~7u;
        if (data_off + step > hdr_off)
            return -1;
        if ((meta & 0xa0000000u) || pkt_len < 16) {
            data_off += step;
            continue;
        }
        frame = pkt_len - 2;
        if (out + 4 + frame + 1 > len)
            return -1;
        put_le16(rx_bounce + out, (uint16_t)frame);
        put_le16(rx_bounce + out + 2, (uint16_t)~frame);
        for (i = 0; i < frame; i++)
            rx_bounce[out + 4 + i] = buf[data_off + 2 + i];
        out += 4 + ((frame + 1u) & ~1u);
        data_off += step;
    }
    if (out == 0)
        return -1;
    for (n = 0; n < out; n++)
        buf[n] = rx_bounce[n];
    return (int)out;
}

/* AX88179A family: 8-byte LE trailer (pkt_cnt bits 0-12, descriptor offset
 * bits 13-31), one 8-byte descriptor per packet (length bits 16-30, bit 31
 * drop, bit 11 rx ok), packets padded to 8 bytes, no IP-align pad. */
static int rx_repack_a(uint8_t *buf, uint32_t len)
{
    uint32_t lo;
    uint32_t pkt_cnt;
    uint32_t hdr_off;
    uint32_t data_off = 0;
    uint32_t out = 0;
    uint32_t n;

    if (len < 16 || len > sizeof(rx_bounce))
        return -1;
    lo = get_le32(buf + len - 8);
    pkt_cnt = lo & 0x1fffu;
    hdr_off = lo >> 13;
    if (pkt_cnt == 0 || pkt_cnt > 64 || hdr_off + pkt_cnt * 8 > len - 8)
        return -1;

    for (n = 0; n < pkt_cnt; n++) {
        uint32_t desc = get_le32(buf + hdr_off + n * 8);
        uint32_t frame = (desc & 0x7fff0000u) >> 16;
        uint32_t step = (frame + 7u) & ~7u;
        uint32_t i;

        if (frame == 0)
            continue;
        if (data_off + step > hdr_off)
            return -1;
        if ((desc & A_RX_DROP) || !(desc & A_RX_OK) || frame < 14) {
            data_off += step;
            continue;
        }
        if (out + 4 + frame + 1 > len)
            return -1;
        put_le16(rx_bounce + out, (uint16_t)frame);
        put_le16(rx_bounce + out + 2, (uint16_t)~frame);
        for (i = 0; i < frame; i++)
            rx_bounce[out + 4 + i] = buf[data_off + i];
        out += 4 + ((frame + 1u) & ~1u);
        data_off += step;
    }
    if (out == 0)
        return -1;
    for (n = 0; n < out; n++)
        buf[n] = rx_bounce[n];
    return (int)out;
}

/* Bulk transfer submit 0x123bc0d4(r0-r3, buf, len, ...): r2 is the endpoint.
 * Called by RX submit (0x123ba724, 16 KB bulk-in) and TX (0x123b9810). */
#define XFER_FN 0x123bc0d4u
#define RX_SUBMIT_BL 0x123ba724u
#define TX_SUBMIT_BL 0x123b9810u
/* Completion case 2 (TX done): cmp r8, #0 with r8 = status/length. */
#define TX_DONE_CASE 0x123b9568u
#define TX_DONE_STOCK 0xe3580000u
typedef int (*xfer_fn)(uint32_t, uint32_t, uint32_t, uint32_t,
                       uint32_t, uint32_t, uint32_t, uint32_t);
static int rxd_logs;
static int rxok_logs;
static int rxs_logs;
static int txs_logs;
static int txd_logs;

static int rx_submit_wrap(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3,
                          uint32_t original, uint32_t saved_lr,
                          uint32_t s0, uint32_t s1, uint32_t s2, uint32_t s3)
{
    int rc = ((xfer_fn)original)(r0, r1, r2, r3, s0, s1, s2, s3);

    (void)saved_lr;
    if (ax179 && (rxs_logs < 6 || (rc < 0 && rxs_logs < 20))) {
        rxs_logs++;
        cafe("AX772E: rx submit ep %x len %x rc %x\n", r2, s1, (uint32_t)rc);
    }
    return rc;
}

static int tx_submit_wrap(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3,
                          uint32_t original, uint32_t saved_lr,
                          uint32_t s0, uint32_t s1, uint32_t s2, uint32_t s3)
{
    int rc = ((xfer_fn)original)(r0, r1, r2, r3, s0, s1, s2, s3);

    (void)saved_lr;
    if (ax179 && (txs_logs < 6 || (rc < 0 && txs_logs < 20))) {
        txs_logs++;
        cafe("AX772E: tx submit ep %x len %x rc %x\n", r2, s1, (uint32_t)rc);
        /* From the 2nd send on (DHCP retries, seconds after link-up): has
         * the chip buffered received frames? Compare with "rx free" at
         * link up. A lower count means frames wait in the chip but never
         * go out on bulk-in. */
        if (TX_PATH_READS && a179 && txs_logs >= 2 && txs_logs <= 5 && last_ctx) {
            uint32_t freebuf = 0xffff, path = 0xff, rxctl = 0xffff;

            if (mac_read(last_ctx, last_handle, scratch, AX_RX_FREE_BUF, 2) >= 0)
                freebuf = get_le16(scratch);
            if (mac_read(last_ctx, last_handle, scratch, A_MAC_PATH, 1) >= 0)
                path = scratch[0];
            if (mac_read(last_ctx, last_handle, scratch, AX_RX_CTL, 2) >= 0)
                rxctl = get_le16(scratch);
            cafe("AX772E: later rx free %x (link up %x) path %x\n", freebuf,
                 free_at_link, path);
            cafe("AX772E: later rxctl %x\n", rxctl, 0, 0);
        }
    }
    return rc;
}

static bool tx_done_hook(trampoline_state *regs)
{
    if (ax179 && txd_logs < 8) {
        txd_logs++;
        cafe("AX772E: tx done status %x\n", (uint32_t)regs->r[8], 0, 0);
    }
    return false; /* run the original cmp */
}

/* Hooked with hook_before_v2. The site's own instruction (ldr lr, [r7, #4])
 * loads LR, and the v1 trampoline returns with bx lr afterwards, so the
 * first real RX completion jumped into data (29.log/30.log froze right
 * after RX started working). v2 returns through a fixed address. */
static bool rx_before(trampoline_state *regs)
{
    uint8_t *obj;
    uint8_t *buf;
    uint32_t len;
    int neu;

    if (!ax179)
        return false;
    obj = (uint8_t *)regs->r[6];
    len = (uint32_t)regs->r[8];
    if (rxd_logs < 16) {
        rxd_logs++;
        cafe("AX772E: rx done len %x obj %x\n", len, (uint32_t)obj, 0);
    }
    if (!obj || (int)len < 8)
        return false;
    buf = *(uint8_t **)(obj + RX_BUF_OFF);
    if (!buf)
        return false;
    if (rx_logs < 2) {
        rx_logs++;
        cafe("AX772E: rx len %x tail %x\n", len, get_le32(buf + len - 4), 0);
    }
    neu = a179 ? rx_repack_a(buf, len) : rx_repack(buf, len);
    if (neu > 0) {
        regs->r[8] = neu;
        if (rxok_logs < 12) {
            rxok_logs++;
            /* repacked: le16 len, le16 ~len, then the frame */
            cafe("AX772E: rx ok in %x out %x first len %x\n", len, (uint32_t)neu,
                 get_le16(buf));
            /* destination MAC (first 3 and last 3 bytes) and ethertype */
            cafe("AX772E: rx dst %x %x type %x\n",
                 ((uint32_t)buf[4] << 16) | ((uint32_t)buf[5] << 8) | buf[6],
                 ((uint32_t)buf[7] << 16) | ((uint32_t)buf[8] << 8) | buf[9],
                 ((uint32_t)buf[16] << 8) | buf[17]);
        }
    }
    else if (rx_logs < 6) {
        rx_logs++;
        cafe("AX772E: rx repack failed len %x\n", len, 0, 0);
    }
    return false;
}

/* Interrupt completion (case 3 of the completion switch, 0x123b95a0). The
 * stock code only reads the link bit (byte 2, bit 0) when the transfer
 * length equals the interrupt endpoint's wMaxPacketSize (dev + 0x19e).
 * r7 = dev, r8 = actual length, [sp + 52] = data. */
static int link_event_ok; /* set in kern_main; word_at is only safe there */

static void int_before(trampoline_state *regs)
{
    uint8_t *dev;
    uint8_t *data;
    uint32_t len;
    uint32_t want;
    int link;

    if (!ax179)
        return;
    dev = (uint8_t *)regs->r[7];
    len = (uint32_t)regs->r[8];
    data = (uint8_t *)regs->stack[13];
    if (!dev || !data || (int)len < 3)
        return;
    want = *(uint16_t *)(dev + 0x19e);
    link = data[2] & 1;
    if (int_logs < 6 || link != int_link) {
        int_logs++;
        /* dev + 12 is the controller state; 0x123b71c8 drops link
         * changes unless it is 3 (open). */
        cafe("AX772E: int len %x want %x state %x\n", len, want,
             *(uint32_t *)(dev + 12));
        cafe("AX772E: int data %x %x link %x\n", get_le32(data),
             len >= 8 ? get_le32(data + 4) : 0, (uint32_t)link);
    }
    int_link = link;
    if (a179) {
        /* intdata1: link bit 16, speed bits 8-10 (1 = 10, 2 = 100), duplex bit 12 */
        uint32_t d = get_le32(data);

        status_link = link;
        if (!link)
            relinked = 0;
        if (link) {
            status_100 = ((d >> 8) & 7u) != 1u;
            status_full = ((d >> 12) & 1u) != 0;
        }
    }
    if (a179 && len != want && want >= 3)
        regs->r[8] = (int)want;

    /* The interface FSM debounces link-up: in LINK-DEBOUNCE (state 2) each
     * link-up event bumps a counter (0x123b4fe4) and only the third moves it
     * to LINK-UP. AX88772 chips repeat their status in every interrupt
     * packet; the 179A family only reports changes, so one event arrives and
     * the debounce timer drops the link. Report link-up twice more through
     * the driver's own link-change function, from the same completion
     * context; the stock code adds the third right after this hook. */
    if (a179 && link && link_event_ok && (uint32_t)regs->r[10]) {
        uint32_t ctx = (uint32_t)regs->r[10];

        LINK_EVENT(ctx, (uint32_t)dev, 4);
        LINK_EVENT(ctx, (uint32_t)dev, 4);
        if (int_logs < 12) {
            int_logs++;
            cafe("AX772E: link up repeated for debounce\n", 0, 0, 0);
        }
    }
}

/* USBETH's logger (0x123ccb50) drops levels below 3. The interface FSM and
 * controller lines are forwarded by replacing their call sites, never by
 * hooking the logger's entry: hook_before plants a BL, which overwrites LR,
 * and at a function entry LR is still the return address (that hung boot).
 * trampoline_blreplace pushes the original target and LR, so the caller's
 * stack arguments start at the 7th parameter. */
static const uint32_t fsm_log_sites[] = {
    0x123b4184u, /* IfFsm(%x): Status %d:%d when processing state %s, event %s. */
    0x123b5130u, /* IfFsm(%x): Error %d:%d when processing state %s entry. */
    0x123b516cu, /* IfFsm(%x): state %s, event %s. */
    0x123b51e4u, /* IfFsm(%x): Error %d:%d when processing state %s, event %s. */
    0x123b5210u, /* IfFsm(%x): state %s -> %s. */
    0x123b5280u, /* IfFsm(%x): Error %d:%d when processing state %s exit. */
    0x123b71fcu, /* CTRL driver link state change to %d ignored, invalid state %d. */
    0x123b74a4u, /* Ctrl(%x): %s, %s. */
};
#define FSM_SITE_COUNT (sizeof(fsm_log_sites) / sizeof(fsm_log_sites[0]))
static int fsm_logs;
static uint32_t fsm_sites_wrapped;

typedef int (*log10_fn)(uint32_t, uint32_t, uint32_t, uint32_t, uint32_t,
                        uint32_t, uint32_t, uint32_t, uint32_t, uint32_t);

static int fsm_log_wrap(uint32_t client, uint32_t level, uint32_t fmt,
                        uint32_t a3, uint32_t original, uint32_t saved_lr,
                        uint32_t s0, uint32_t s1, uint32_t s2, uint32_t s3,
                        uint32_t s4, uint32_t s5)
{
    (void)saved_lr;
    /* These lines never reached the Cafe log with their own client index
     * (and USBETH's printf sink does not reach it at all), so send them
     * with the client index the plugin's own lines use, at level 3. */
    if (ax179 && log_have && fsm_logs < 200) {
        if (fsm_logs == 0)
            cafe("AX772E: fsm log site reached, client %x level %x\n",
                 client, level, 0);
        fsm_logs++;
        client = log_id;
        if (level < 3)
            level = 3;
    }
    return ((log10_fn)original)(client, level, fmt, a3, s0, s1, s2, s3, s4, s5);
}

/* IOS syscall 0x52 (FlushDCache), encoded the way stroopwafel's svc.S does it.
 * Its own copy here, so the call runs in USBETH's process without reaching
 * into wafel_core's code. */
__asm__(
    ".section .text\n"
    ".arm\n"
    ".align 2\n"
    ".type ax_flush_dcache, %function\n"
    "ax_flush_dcache:\n"
    "    .word 0xE7F052F0\n"
    "    bx lr\n");
int ax_flush_dcache(void *address, uint32_t size);

/* Hooked with hook_before_v2: at this site LR holds the packet object, and
 * the v1 hook's BL would overwrite it (the stock loop then wrote through code
 * addresses and IOSU froze on the first transmit). Returns false so the
 * original instruction still runs. */
static bool tx_before(trampoline_state *regs)
{
    uint32_t packet;
    uint32_t ip;
    uint8_t *ptr;
    uint32_t len;
    uint32_t i;

    if (!ax179)
        return false;
    packet = regs->lr;
    ip = (uint32_t)regs->r[12];
    if (!packet)
        return false;
    ptr = *(uint8_t **)(packet + ip);
    len = *(uint32_t *)(packet + ip + 4) & 0xffffu;
    if (!ptr || len < 14 || len > 1518)
        return false;
    for (i = len; i > 0; i--)
        ptr[7 + i] = ptr[3 + i];
    if (a179) {
        /* 8-byte LE descriptor: length in bits 0-20. A transfer that ends on
         * a 512-byte boundary gets 8 pad bytes the chip is told to drop. */
        uint32_t extra = 4;
        /* bits 21-27: (len + len >> 8 + (len >> 16 & 0x1f)) & 0x7f */
        uint32_t sum = ((len + (len >> 8) + ((len >> 16) & 0x1fu)) & 0x7fu) << 21;

        if (((len + 8u) & 511u) == 0) {
            put_le32(ptr, len | sum | A_TX_DROP_PAD);
            for (i = 0; i < 8; i++)
                ptr[8 + len + i] = 0;
            extra += 8;
        } else {
            put_le32(ptr, len | sum);
        }
        put_le32(ptr + 4, 0);
        *(uint32_t *)(packet + TX_LEN_OFF) += extra;
        /* The stock code cleans the data cache only over its own 4 + len
         * bytes before the USB DMA reads the buffer. The frame now ends 4
         * bytes later (plus any pad), and those bytes could still sit in the
         * cache: about 1 in 5 full-size frames reached the PC with their last
         * 4 bytes taken from stale memory (the chip's checksum offload made
         * the TCP checksum match them), which corrupted PCFS writes. */
        ax_flush_dcache(ptr, 8u + len + 8u);
    } else {
        put_le32(ptr, len);
        if (((len + 8u) & 511u) == 0)
            put_le32(ptr + 4, 0x80008000u);
        else
            put_le32(ptr + 4, 0);
        *(uint32_t *)(packet + TX_LEN_OFF) += 4;
    }
    if (tx_logs < 2) {
        tx_logs++;
        cafe("AX772E: tx frame %x total %x\n", len,
             *(uint32_t *)(packet + TX_LEN_OFF), 0);
    }
    return false;
}

static void probe_wrapper(void *context, UhsInterfaceProfile *profile)
{
    uint16_t vid = profile->dev_desc.idVendor;
    uint16_t pid = profile->dev_desc.idProduct;
    uint32_t i;

    note_log_id(*(uint32_t *)((uint8_t *)context + 4), 0);
    for (i = 0; i < KNOWN_ID_COUNT; i++) {
        if (vid != known_ids[i][0] || pid != known_ids[i][1])
            continue;
        ax179 = is_179(vid, pid);
        bcd_device = profile->dev_desc.bcdDevice;
        fast_phy = ax179 && (bcd_device == 0x0300 || pid == 0x772d || pid == 0x772e);
        hw_ready = 0;
        have_mac = 0;
        sw_mii = 0;
        mii_logs = 0;
        phyid_logged = 0;
        int_logs = 0;
        int_link = -1;
        link_logs = 0;
        cafe("AX772E: probe vidpid %x bcd %x class %x\n",
             ((uint32_t)vid << 16) | pid, bcd_device,
             profile->if_desc.bInterfaceClass);
        cafe("AX772E: fsm log sites wrapped %x of %x\n", fsm_sites_wrapped,
             (uint32_t)FSM_SITE_COUNT, 0);
        cafe("AX772E: config %x subclass %x proto %x\n",
             profile->cfg_desc.bConfigurationValue,
             profile->if_desc.bInterfaceSubClass,
             profile->if_desc.bInterfaceProtocol);
        if (ax179 && !translator_on) {
            cafe("AX772E: translator not installed, id left alone\n", 0, 0, 0);
            return;
        }
        if (ax179 && profile->if_desc.bInterfaceClass != 0xff) {
            cafe("AX772E: not the vendor interface, left for re-enum\n", 0, 0, 0);
            return;
        }
        {
            uint16_t *vidpid = (uint16_t *)VIDPID_PTR;
            vidpid[0] = vid;
            vidpid[1] = pid;
        }
        stock_probe(context, profile);
        return;
    }
}

static int register_hook(int *handles, UhsInterfaceFilter *filter, void *context,
                         void *callback,
                         int (*original)(int *, UhsInterfaceFilter *, void *, void *))
{
    filter->match_params = MATCH_ANY;
    stock_probe = callback;
    return original(handles, filter, context, probe_wrapper);
}

static uint8_t chipcode;

static int (*read_command)(int, int, uint8_t, uint16_t, uint16_t, uint16_t, uint8_t *) =
    (void *)0x123b9ab4;

static void read_chipcode(trampoline_state *regs)
{
    int rc;

    if (ax179)
        return;
    rc = read_command(regs->r[0], regs->r[1], 0x09, 0, 0, 1, &chipcode);
    chipcode &= 0x70;
    cafe("AX772E: 772 chipcode %x rc %x\n", chipcode, (uint32_t)rc, 0);
}

static void rx_control(trampoline_state *regs)
{
    if (ax179)
        return;
    if (chipcode >= 0x20)
        regs->r[1] &= ~0x600;
}

__attribute__((target("arm")))
void kern_main(void)
{
    uint32_t logw = word_at(USBETH_LOG_FN);
    uint32_t read_bl = word_at(READ_CTRL_BL);
    uint32_t write_bl = word_at(WRITE_CTRL_BL);
    int translator;

    log_ok = logw == USBETH_LOG_STOCK;
    print_ok = word_at(USBETH_PRINT_FN) == USBETH_PRINT_STOCK;
    fs_ok = word_at(FS_LOG_FN) == FS_LOG_STOCK;
    translator = log_ok &&
                 word_at(READ_SUBS) == READ_SUBS_STOCK &&
                 word_at(WRITE_SUBS) == WRITE_SUBS_STOCK &&
                 (read_bl >> 24) == 0xeb &&
                 bl_target(READ_CTRL_BL, read_bl) == CTRL_FN &&
                 (write_bl >> 24) == 0xeb &&
                 bl_target(WRITE_CTRL_BL, write_bl) == CTRL_FN &&
                 word_at(RX_CASE) == RX_CASE_STOCK &&
                 word_at(TX_HDR_DONE) == TX_HDR_DONE_STOCK;

    if ((word_at(REGISTER_BL) >> 24) == 0xeb)
        trampoline_blreplace(REGISTER_BL, register_hook);
    if ((word_at(CHIPCODE_BL) >> 24) == 0xeb)
        trampoline_hook_before(CHIPCODE_BL, read_chipcode);
    if (word_at(RXCTL_MOV) == RXCTL_MOV_STOCK)
        trampoline_hook_before(RXCTL_MOV, rx_control);

    if (log_ok && (word_at(USBETH_TRACE_BL) >> 24) == 0xeb &&
        bl_target(USBETH_TRACE_BL, word_at(USBETH_TRACE_BL)) == USBETH_LOG_FN)
        trampoline_hook_before(USBETH_TRACE_BL, capture_log_id);

    if (!translator)
        return;
    translator_on = 1;
    trampoline_hook_before(READ_CTRL_BL, read_before);
    trampoline_hook_before(READ_SUBS, read_after);
    trampoline_hook_before(WRITE_CTRL_BL, write_before);
    trampoline_hook_before(WRITE_SUBS, write_after);
    trampoline_hook_before_v2(RX_CASE, rx_before);
    trampoline_hook_before_v2(TX_HDR_DONE, tx_before);
    link_event_ok = word_at(LINK_EVENT_FN) == LINK_EVENT_STOCK;
    if (word_at(INT_CASE) == INT_CASE_STOCK)
        trampoline_hook_before(INT_CASE, int_before);
    if ((word_at(RX_SUBMIT_BL) >> 24) == 0xeb &&
        bl_target(RX_SUBMIT_BL, word_at(RX_SUBMIT_BL)) == XFER_FN)
        trampoline_blreplace(RX_SUBMIT_BL, rx_submit_wrap);
    if ((word_at(TX_SUBMIT_BL) >> 24) == 0xeb &&
        bl_target(TX_SUBMIT_BL, word_at(TX_SUBMIT_BL)) == XFER_FN)
        trampoline_blreplace(TX_SUBMIT_BL, tx_submit_wrap);
    if (word_at(TX_DONE_CASE) == TX_DONE_STOCK)
        trampoline_hook_before_v2(TX_DONE_CASE, tx_done_hook);
    if (word_at(RX_PACE_BLT) == RX_PACE_BLT_STOCK)
        U32_PATCH_K(RX_PACE_BLT, RX_PACE_B);
    {
        uint32_t i;
        uint32_t n = 0;

        for (i = 0; i < FSM_SITE_COUNT; i++) {
            uint32_t site = fsm_log_sites[i];
            uint32_t ins = word_at(site);

            if ((ins >> 24) == 0xeb && bl_target(site, ins) == USBETH_LOG_FN) {
                trampoline_blreplace(site, fsm_log_wrap);
                n++;
            }
        }
        for (i = 0; i < sizeof(fsm_event_sites) / sizeof(fsm_event_sites[0]); i++) {
            uint32_t site = fsm_event_sites[i];
            uint32_t ins = word_at(site);

            if ((ins >> 24) == 0xeb && bl_target(site, ins) == FSM_EVENT_FN) {
                trampoline_blreplace(site, fsm_event_wrap);
                n += 0x100;
            }
        }
        {
            uint32_t ins = word_at(FSM_QUEUED_B);

            if ((ins >> 24) == 0xea && bl_target(FSM_QUEUED_B, ins) == FSM_EVENT_FN) {
                trampoline_hook_before_v2(FSM_QUEUED_B, fsm_queued_hook);
                n += 0x1000;
            }
        }
        fsm_sites_wrapped = n; /* logged at probe; no logging from kern_main */
    }
}

void mcp_main(void)
{
}
