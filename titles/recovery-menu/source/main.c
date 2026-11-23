#include <cafe/demo.h>
#include <cafe/fs.h>
#include <cafe/os.h>
#include <cafe/vpad.h>
#include <nn/ac.h>

#include <stdio.h>
#include <string.h>

#include "menulog.h"
#include "titles.h"
#include "ui.h"

/* catr's `repair recovery-menu` waits for "CAT Recovery Menu v<x> starting". */
#define MENU_VERSION "v1.1"
#define MENU_TITLE_VERSION 11
#define MENU_SDK_VERSION "2.12.13"
#define MENU_OS_ID "000500101000400A"
#define TITLE_ID_SELF 0x000500101F700700ULL
#define TITLE_ID_SYSTEM_CONFIG_TOOL 0x000500101F700500ULL
#define PCFS_PROOF_PATH "/vol/content/pcfs-proof.txt"
#define DEMO_FONT_SHADER_PATH "/vol/content/assets/shaders/lib_demo/cafe/demoFont.gsh"

/*
 * Every file access of this file is asynchronous with a deadline. A PCFS read
 * whose host link has dropped never completes; the synchronous calls used in
 * v0.2 froze the whole title (and the console with it) at that point.
 */
#define FS_TIMEOUT_MS 3000
#define STATUS_REFRESH_MS 5000

typedef enum
{
    TAB_STATUS,
    TAB_INSTALL,
    TAB_LAUNCH,
    TAB_LOGS,
    TAB_SCT,
    TAB_COUNT
} Tab;

static const char *const TAB_LABELS[TAB_COUNT] = {
    "Status", "Install", "Launch", "Logs", "Config Tool"
};

typedef struct
{
    volatile BOOL done;
    volatile FSStatus result;
} AsyncOp;

typedef enum
{
    PROBE_IDLE,
    PROBE_OPEN,
    PROBE_READ,
    PROBE_CLOSE
} ProbeState;

typedef enum
{
    MARKER_UNKNOWN,
    MARKER_OK,
    MARKER_ERROR,
    MARKER_TIMEOUT
} MarkerState;

static FSClient g_fs_client;
static FSCmdBlock g_fs_cmd;
static AsyncOp g_op;
static FSAsyncParams g_async;
static BOOL g_fs_ready = FALSE;
/* A timed-out command still owns g_fs_cmd; never touch FS again once set. */
static BOOL g_fs_stuck = FALSE;

static ProbeState g_probe = PROBE_IDLE;
static OSTime g_probe_started;
static FSFileHandle g_probe_file = FS_INVALID_HANDLE_VALUE;
static FSStatus g_probe_read = 0;
static MarkerState g_marker = MARKER_UNKNOWN;
/* FSReadFile rejects (and marks the volume FATAL for) unaligned buffers. */
static char g_probe_buffer[192] __attribute__((aligned(FS_IO_BUFFER_ALIGN)));
static char g_marker_text[160];

/* UI state */
static Tab g_tab = TAB_STATUS;
static BOOL g_focus = FALSE;          /* panel owns input */
static int g_log_scroll = 0;          /* lines up from the newest */

/* Launch state (System Config Tool or a title from the Launch tab). */
static BOOL g_launch_requested = FALSE;
static int g_launch_result = 0;
static BOOL g_launch_failed = FALSE;
static char g_launch_label[64];

/* Status tab */
static BOOL g_ac_ready = FALSE;
static BOOL g_net_ok = FALSE;
static u32 g_net_ip = 0;
static u32 g_net_error = 0;
static BOOL g_counts_ok = FALSE;
static MgrCounts g_counts;
static OSTime g_status_refreshed = 0;
static u32 g_console_type = 0;
static u32 g_security_level = 0;

/* ---- async file access ------------------------------------------------- */

static void fs_done(FSClient *client, FSCmdBlock *block, FSStatus result,
                    void *context)
{
    AsyncOp *op = (AsyncOp *)context;

    (void)client;
    (void)block;
    op->result = result;
    op->done = TRUE;
}

static const FSAsyncParams *begin_op(void)
{
    g_op.done = FALSE;
    g_op.result = FS_STATUS_FATAL_ERROR;
    g_async.userCallback = fs_done;
    g_async.userContext = &g_op;
    g_async.ioMsgQueue = NULL;
    return &g_async;
}

static BOOL deadline_passed(OSTime started)
{
    return OSGetTime() - started >
           (OSTime)OSMillisecondsToTicks(FS_TIMEOUT_MS);
}

/* Waits for the current command, sleeping so the FS callback can run. */
static BOOL wait_op(FSStatus *result)
{
    OSTime started = OSGetTime();

    while (!g_op.done) {
        if (deadline_passed(started)) {
            g_fs_stuck = TRUE;
            OSReport("CAT Recovery Menu: FS command timed out after %d ms\n",
                     FS_TIMEOUT_MS);
            return FALSE;
        }
        OSSleepMilliseconds(5);
    }
    *result = g_op.result;
    return TRUE;
}

/*
 * DEMOFontInit's SDK loader assumes its shader exists. If it does not, the
 * helper calls FSGetStatFile with an invalid handle and blocks forever, so
 * check it first. A missing file and a stalled read both mean "no UI".
 */
static BOOL required_asset_available(const char *path)
{
    FSFileHandle file = FS_INVALID_HANDLE_VALUE;
    FSStatus status;

    if (FSOpenFileAsync(&g_fs_client, &g_fs_cmd, path, "r", &file,
                        FS_RET_ALL_ERROR, begin_op()) != FS_STATUS_OK ||
        !wait_op(&status)) {
        OSReport("CAT Recovery Menu: could not start/finish open of %s\n", path);
        return FALSE;
    }
    if (status < FS_STATUS_OK) {
        OSReport("CAT Recovery Menu: required asset missing: %s (%d)\n",
                 path, status);
        return FALSE;
    }

    if (FSCloseFileAsync(&g_fs_client, &g_fs_cmd, file, FS_RET_ALL_ERROR,
                         begin_op()) != FS_STATUS_OK ||
        !wait_op(&status)) {
        OSReport("CAT Recovery Menu: could not close %s\n", path);
        return FALSE;
    }
    return TRUE;
}

static void finish_probe(MarkerState state, const char *text)
{
    g_probe = PROBE_IDLE;
    g_marker = state;
    snprintf(g_marker_text, sizeof(g_marker_text), "%s", text);
    menu_log("content marker: %s", g_marker_text);
}

static void start_probe(void)
{
    if (!g_fs_ready || g_fs_stuck || g_probe != PROBE_IDLE) {
        return;
    }

    g_probe_file = FS_INVALID_HANDLE_VALUE;
    g_probe_read = 0;
    memset(g_probe_buffer, 0, sizeof(g_probe_buffer));
    g_marker = MARKER_UNKNOWN;
    snprintf(g_marker_text, sizeof(g_marker_text), "Checking...");

    if (FSOpenFileAsync(&g_fs_client, &g_fs_cmd, PCFS_PROOF_PATH, "r",
                        &g_probe_file, FS_RET_ALL_ERROR,
                        begin_op()) != FS_STATUS_OK) {
        finish_probe(MARKER_ERROR, "Could not start the file check");
        return;
    }
    g_probe = PROBE_OPEN;
    g_probe_started = OSGetTime();
}

/* Advances the marker check by at most one step per frame; never blocks. */
static void poll_probe(void)
{
    char text[160];
    FSStatus result;

    if (g_probe == PROBE_IDLE) {
        return;
    }
    if (!g_op.done) {
        if (deadline_passed(g_probe_started)) {
            g_fs_stuck = TRUE;
            finish_probe(MARKER_TIMEOUT,
                         "No answer after 3 s (PCFS link down?)");
        }
        return;
    }

    result = g_op.result;
    switch (g_probe) {
    case PROBE_OPEN:
        if (result < FS_STATUS_OK) {
            snprintf(text, sizeof(text), "Not found (open=%d)", result);
            finish_probe(MARKER_ERROR, text);
            return;
        }
        if (FSReadFileAsync(&g_fs_client, &g_fs_cmd, g_probe_buffer, 1,
                            sizeof(g_probe_buffer) - 1, g_probe_file, 0,
                            FS_RET_ALL_ERROR, begin_op()) != FS_STATUS_OK) {
            /* Still close the handle so retries do not leak it. */
            g_probe_read = FS_STATUS_FATAL_ERROR;
            if (FSCloseFileAsync(&g_fs_client, &g_fs_cmd, g_probe_file,
                                 FS_RET_ALL_ERROR, begin_op()) != FS_STATUS_OK) {
                finish_probe(MARKER_ERROR, "Could not start the read");
                return;
            }
            g_probe = PROBE_CLOSE;
            break;
        }
        g_probe = PROBE_READ;
        break;

    case PROBE_READ:
        /* Close the handle whether or not the read worked. */
        g_probe_read = result;
        if (FSCloseFileAsync(&g_fs_client, &g_fs_cmd, g_probe_file,
                             FS_RET_ALL_ERROR, begin_op()) != FS_STATUS_OK) {
            finish_probe(MARKER_ERROR, "Could not start the close");
            return;
        }
        g_probe = PROBE_CLOSE;
        break;

    case PROBE_CLOSE:
        if (g_probe_read < FS_STATUS_OK) {
            snprintf(text, sizeof(text), "Read failed (read=%d)", g_probe_read);
            finish_probe(MARKER_ERROR, text);
        } else {
            char *newline;

            g_probe_buffer[g_probe_read] = '\0';
            newline = strpbrk(g_probe_buffer, "\r\n");
            if (newline) {
                *newline = '\0';
            }
            finish_probe(MARKER_OK, g_probe_buffer);
        }
        return;

    default:
        g_probe = PROBE_IDLE;
        return;
    }
    g_probe_started = OSGetTime();
}

/* ---- status ------------------------------------------------------------ */

static void refresh_network(void)
{
    ACIpAddress ip = 0;
    ACResult rc;

    if (!g_ac_ready) {
        return;
    }
    rc = ACGetAssignedAddress(&ip);
    if (ACIsSuccess(rc) && ip != 0) {
        if (!g_net_ok || (u32)ip != g_net_ip) {
            menu_log("network: %u.%u.%u.%u", (u32)(ip >> 24) & 0xFF,
                     (u32)(ip >> 16) & 0xFF, (u32)(ip >> 8) & 0xFF,
                     (u32)ip & 0xFF);
        }
        g_net_ok = TRUE;
        g_net_ip = (u32)ip;
    } else {
        if (g_net_ok) {
            menu_log("network: no address any more");
        }
        g_net_ok = FALSE;
        g_net_error = *(u32 *)&rc;
    }
}

static void refresh_status(void)
{
    BOOL had_pcfs = g_counts_ok && g_counts.pcfs > 0;

    refresh_network();
    if (!mgr_busy()) {
        g_counts_ok = mgr_counts(&g_counts);
        if (g_counts_ok && had_pcfs != (g_counts.pcfs > 0)) {
            menu_log("PCFS %s (%u PC titles listed)",
                     g_counts.pcfs > 0 ? "mounted" : "not mounted",
                     g_counts.pcfs);
        }
    }
    g_status_refreshed = OSGetTime();
}

static void poll_status(void)
{
    if (OSGetTime() - g_status_refreshed >
        (OSTime)OSMillisecondsToTicks(STATUS_REFRESH_MS)) {
        refresh_status();
    }
}

/* ---- launching --------------------------------------------------------- */

static void request_launch(u64 tid, const char *label)
{
    if (g_launch_requested || mgr_busy()) {
        return;
    }
    snprintf(g_launch_label, sizeof(g_launch_label), "%s", label);
    g_launch_requested = TRUE;
    g_launch_failed = FALSE;
    menu_log("AUDIT launch %016llX (%s)", tid, g_launch_label);
    g_launch_result = OSLaunchTitlel(tid, 0);
    menu_log("OSLaunchTitlel returned %d", g_launch_result);

    /*
     * On success the system asks this title to exit through ProcUI, so the
     * main loop must keep running until DEMOIsRunning() returns FALSE.
     * Only a negative result is a failure; allow another try then.
     */
    if (g_launch_result < 0) {
        g_launch_requested = FALSE;
        g_launch_failed = TRUE;
    }
}

static void request_system_config_tool(void)
{
    request_launch(TITLE_ID_SYSTEM_CONFIG_TOOL, "System Config Tool");
}

/* ---- tabs -------------------------------------------------------------- */

static BOOL tab_uses_mgr(Tab tab)
{
    return tab == TAB_INSTALL || tab == TAB_LAUNCH;
}

/* Called when a tab gets highlighted. The SD card is read only on A. */
static void enter_tab(Tab tab)
{
    if (tab == TAB_STATUS) {
        refresh_status();
    } else if (tab == TAB_LAUNCH) {
        mgr_open(MGR_SCREEN_LAUNCH);
    } else if (tab == TAB_LOGS) {
        g_log_scroll = 0;
    }
}

/* A on a highlighted tab. */
static void activate_tab(void)
{
    switch (g_tab) {
    case TAB_STATUS:
        refresh_status();
        start_probe();
        break;
    case TAB_INSTALL:
        if (mgr_screen() != MGR_SCREEN_INSTALL &&
            mgr_screen() != MGR_SCREEN_TITLES) {
            mgr_open(MGR_SCREEN_INSTALL);
        }
        g_focus = TRUE;
        break;
    case TAB_LAUNCH:
        if (mgr_screen() != MGR_SCREEN_LAUNCH) {
            mgr_open(MGR_SCREEN_LAUNCH);
        }
        g_focus = TRUE;
        break;
    case TAB_LOGS:
        g_focus = TRUE;
        break;
    case TAB_SCT:
        request_system_config_tool();
        break;
    default:
        break;
    }
}

static int log_rows(void)
{
    return 22;
}

static void logs_input(const VPADStatus *s)
{
    int max_scroll = menu_log_count() - log_rows();

    if (max_scroll < 0) {
        max_scroll = 0;
    }
    if (s->trig & VPAD_BUTTON_UP) {
        g_log_scroll++;
    }
    if (s->trig & VPAD_BUTTON_DOWN) {
        g_log_scroll--;
    }
    if (s->trig & VPAD_BUTTON_LEFT) {
        g_log_scroll += log_rows();
    }
    if (s->trig & VPAD_BUTTON_RIGHT) {
        g_log_scroll -= log_rows();
    }
    if (s->trig & VPAD_BUTTON_X) {
        g_log_scroll = 0;
    }
    if (g_log_scroll > max_scroll) {
        g_log_scroll = max_scroll;
    }
    if (g_log_scroll < 0) {
        g_log_scroll = 0;
    }
}

static void update_input(void)
{
    VPADStatus status;
    s32 error = VPAD_READ_ERR_NONE;
    u32 trig;

    if (g_launch_requested) {
        return;
    }
    if (VPADRead(0, &status, 1, &error) <= 0 ||
        error != VPAD_READ_ERR_NONE) {
        return;
    }
    trig = status.trig;

    if (g_launch_failed) {
        if (trig & (VPAD_BUTTON_A | VPAD_BUTTON_B)) {
            g_launch_failed = FALSE;
        }
        return;
    }
    /* + always starts System Config Tool, except during an install. */
    if ((trig & VPAD_BUTTON_PLUS) && !mgr_busy()) {
        request_system_config_tool();
        return;
    }

    if (g_focus) {
        if (tab_uses_mgr(g_tab)) {
            u64 tid;

            if (!mgr_box_up() && (trig & VPAD_BUTTON_B)) {
                g_focus = FALSE;
                return;
            }
            tid = mgr_input(&status);
            if (tid != 0) {
                const char *name = mgr_title_name(tid);
                char label[64];

                if (name == NULL) {
                    snprintf(label, sizeof(label), "%016llX", tid);
                    name = label;
                }
                request_launch(tid, name);
            }
            return;
        }
        if (trig & VPAD_BUTTON_B) {
            g_focus = FALSE;
            return;
        }
        logs_input(&status);
        return;
    }

    if (trig & (VPAD_BUTTON_DOWN | VPAD_BUTTON_UP)) {
        if (mgr_busy()) {
            return;
        }
        g_tab = (trig & VPAD_BUTTON_DOWN) ? (Tab)((g_tab + 1) % TAB_COUNT)
                                          : (Tab)((g_tab + TAB_COUNT - 1) % TAB_COUNT);
        enter_tab(g_tab);
        return;
    }
    if (trig & (VPAD_BUTTON_A | VPAD_BUTTON_RIGHT)) {
        activate_tab();
        return;
    }
    if (trig & VPAD_BUTTON_X) {
        if (g_tab == TAB_STATUS) {
            refresh_status();
            start_probe();
        } else if (g_tab == TAB_LAUNCH) {
            mgr_open(MGR_SCREEN_LAUNCH);
        }
    }
}

/* ---- drawing ----------------------------------------------------------- */

static void draw_tabs(void)
{
    const f32 x0 = 0.4f, x1 = 17.6f, height = 2.5f, gap = 0.3f;
    f32 y = 2.0f;
    int i;

    for (i = 0; i < TAB_COUNT; i++) {
        BOOL selected = (i == (int)g_tab);
        f32 center = (x0 + x1) * 0.5f;

        if (selected) {
            /* The selected tab reaches into the panel, as on the console. */
            ui_frame(x0, y, x1 + 0.15f, y + height, UI_TAB_SELECTED,
                     UI_TAB_SELECTED, 0);
            ui_text_center(center, y + 0.75f, UI_TAB_SELECTED_TEXT, "%s",
                           TAB_LABELS[i]);
        } else {
            ui_frame(x0, y, x1 - 0.2f, y + height, UI_TAB_FILL, UI_TAB_BORDER, 2);
            ui_text_center(center - 0.1f, y + 0.75f, UI_TAB_TEXT, "%s",
                           TAB_LABELS[i]);
        }
        y += height + gap;
    }
}

static void status_line(f32 line, const char *label, UiColor value_color,
                        const char *value)
{
    ui_text(UI_TEXT_X, line, UI_TEXT, "%s", label);
    ui_text(UI_TEXT_X + 12.0f, line, value_color, "%s", value);
}

static void draw_status(void)
{
    char text[128];
    OSCalendarTime now;
    OSTime up = OSGetSystemTime();
    u32 up_s = (u32)OSTicksToSeconds(up);
    f32 line = 1.0f;
    const f32 step = 1.15f;
    BOOL pcfs = g_counts_ok && g_counts.pcfs > 0;

    ui_text(UI_TEXT_X, line, UI_TEXT, "CAT Recovery Menu " MENU_VERSION
            "   Cafe OS SDK Version " MENU_SDK_VERSION);
    line += step;
    ui_text(UI_TEXT_X, line, UI_TEXT, "Title: %016llX v%u   OS: " MENU_OS_ID,
            TITLE_ID_SELF, MENU_TITLE_VERSION);
    line += step;
    ui_text(UI_TEXT_X, line, UI_TEXT, "ConsoleType: 0x%08X   SecurityLevel: %u (%s)",
            g_console_type, g_security_level,
            g_security_level == OS_SECURITY_LEVEL_DEV ? "DEV" : "PROD");
    line += step * 1.6f;

    if (!g_ac_ready) {
        status_line(line, "Network", UI_TEXT_BAD, "AC library not available");
    } else if (g_net_ok) {
        snprintf(text, sizeof(text), "up, %u.%u.%u.%u",
                 (g_net_ip >> 24) & 0xFF, (g_net_ip >> 16) & 0xFF,
                 (g_net_ip >> 8) & 0xFF, g_net_ip & 0xFF);
        status_line(line, "Network", UI_TEXT_GOOD, text);
    } else {
        snprintf(text, sizeof(text), "no address (0x%08X)", g_net_error);
        status_line(line, "Network", UI_TEXT_BAD, text);
    }
    line += step;

    if (!g_counts_ok) {
        status_line(line, "PCFS", UI_TEXT_DIM, "unknown (title list unavailable)");
    } else if (pcfs) {
        snprintf(text, sizeof(text), "mounted, %u PC titles listed", g_counts.pcfs);
        status_line(line, "PCFS", UI_TEXT_GOOD, text);
    } else {
        status_line(line, "PCFS", UI_TEXT_WARN, "not mounted (catr pcfs up)");
    }
    line += step;

    switch (g_marker) {
    case MARKER_OK:
        snprintf(text, sizeof(text), "%.60s", g_marker_text);
        status_line(line, "Content", UI_TEXT_GOOD, text);
        break;
    case MARKER_TIMEOUT:
    case MARKER_ERROR:
        snprintf(text, sizeof(text), "%.60s", g_marker_text);
        status_line(line, "Content", UI_TEXT_BAD, text);
        break;
    default:
        status_line(line, "Content", UI_TEXT_DIM, g_marker_text);
        break;
    }
    line += step;
    if (g_fs_stuck) {
        status_line(line, "", UI_TEXT_BAD, "File access off until restart.");
        line += step;
    }

    if (g_counts_ok) {
        snprintf(text, sizeof(text), "%u  (MLC %u, USB %u, SLC %u, PC %u)",
                 g_counts.total, g_counts.mlc, g_counts.usb, g_counts.slc,
                 g_counts.pcfs);
        status_line(line, "Titles", UI_TEXT, text);
    } else {
        status_line(line, "Titles", UI_TEXT_DIM, mgr_status_line());
    }
    line += step;

    snprintf(text, sizeof(text), "%u:%02u:%02u", up_s / 3600, (up_s / 60) % 60,
             up_s % 60);
    status_line(line, "Uptime", UI_TEXT, text);
    line += step;
    OSTicksToCalendarTime(OSGetTime(), &now);
    snprintf(text, sizeof(text), "%04d-%02d-%02d %02d:%02d:%02d", now.year,
             now.mon + 1, now.mday, now.hour, now.min, now.sec);
    status_line(line, "Clock", UI_TEXT, text);
    line += step * 1.6f;

    if (g_net_ok && pcfs) {
        ui_text(UI_TEXT_X, line, UI_TEXT_GOOD,
                "Ready for catr: network up, PCFS mounted, this menu in front.");
    } else if (g_net_ok) {
        ui_text(UI_TEXT_X, line, UI_TEXT_WARN,
                "Network up. catr run needs PCFS: catr pcfs up (or CatR-Tools).");
    } else {
        ui_text(UI_TEXT_X, line, UI_TEXT_BAD,
                "No network: catr and CatR-Tools cannot reach the console.");
    }
    line += step;
    ui_text(UI_TEXT_X, line, UI_TEXT_DIM,
            "This menu is the default boot title. Leave it in front for catr.");

    ui_text(UI_TEXT_X, UI_FOOTER_Y, UI_TEXT_DIM,
            "A / X: refresh and check content   +: System Config Tool");
}

static void draw_install_placeholder(void)
{
    ui_text(UI_TEXT_X, 1.0f, UI_TEXT, "Install from SD card  (sd:/install)");
    ui_text(UI_TEXT_X, 4.0f, UI_TEXT,
            "Press A to read the SD card and list the packages in /install.");
    ui_text(UI_TEXT_X, 5.3f, UI_TEXT,
            "In the list, - switches to installed titles for uninstall.");
    ui_text(UI_TEXT_X, 7.6f, UI_TEXT_DIM,
            "Every install and uninstall needs L + R held while pressing A.");
    ui_text(UI_TEXT_X, 8.9f, UI_TEXT_DIM,
            "From the PC, catr install / uninstall do the same with read-back.");
    ui_text(UI_TEXT_X, UI_FOOTER_Y, UI_TEXT_DIM,
            "A: open   D-Pad: choose a menu   +: System Config Tool");
}

static void draw_logs(void)
{
    const f32 top = 2.6f, step = 1.08f;
    int rows = log_rows();
    int count = menu_log_count();
    int last = count - 1 - g_log_scroll;
    int first = last - rows + 1;
    int i;

    ui_text(UI_TEXT_X, 1.0f, UI_TEXT, "Console Log");
    ui_text(UI_TEXT_X + 12.0f, 1.0f, UI_TEXT_DIM,
            "%d lines%s   (also on USB serial: catr log)", count,
            g_log_scroll > 0 ? ", scrolled" : "");
    if (first < 0) {
        first = 0;
    }
    for (i = first; i <= last; i++) {
        const char *text = menu_log_line(i);
        UiColor c = UI_TEXT;

        if (strstr(text, "AUDIT") != NULL) {
            c = UI_ROW_TEXT;
        } else if (strstr(text, "fail") != NULL || strstr(text, "FAIL") != NULL ||
                   strstr(text, "No answer") != NULL) {
            c = UI_TEXT_BAD;
        }
        ui_text(UI_TEXT_X, top + (i - first) * step, c, "%.96s", text);
    }
    ui_text(UI_TEXT_X, UI_FOOTER_Y, UI_TEXT_DIM, g_focus ?
            "Up/Down: scroll   Left/Right: page   X: newest   B: back" :
            "A: scroll   D-Pad: choose a menu   +: System Config Tool");
}

static void draw_sct(void)
{
    ui_text(UI_TEXT_X, 1.0f, UI_TEXT, "System Config Tool");
    ui_text(UI_TEXT_X, 4.0f, UI_TEXT,
            "Press A to start System Config Tool (000500101F700500).");
    ui_text(UI_TEXT_X, 6.3f, UI_TEXT_DIM,
            "SCT suspends the network, so catr and PCFS stop answering");
    ui_text(UI_TEXT_X, 7.6f, UI_TEXT_DIM,
            "until you return here. This menu stays the default boot title;");
    ui_text(UI_TEXT_X, 8.9f, UI_TEXT_DIM,
            "a restart from SCT comes back to it.");
    ui_text(UI_TEXT_X, UI_FOOTER_Y, UI_TEXT_DIM,
            "A: start   D-Pad: choose a menu   +: System Config Tool from any menu");
}

static void draw_launch_box(void)
{
    char text[96];

    if (g_launch_requested) {
        ui_box("Starting title, please wait...", g_launch_label, NULL, FALSE,
               0, 0);
    } else if (g_launch_failed) {
        snprintf(text, sizeof(text), "OSLaunchTitlel returned %d", g_launch_result);
        ui_box("Launch failed", g_launch_label, text, FALSE, 0, 0);
        ui_text_center((26.0f + 63.0f) * 0.5f, 17.8f, UI_BOX_TEXT, "A: OK");
    }
}

static void draw_target(GX2ColorBuffer *target)
{
    ui_begin(target);
    ui_frame(UI_PANEL_X0, UI_PANEL_Y0, UI_PANEL_X1, UI_PANEL_Y1, UI_BACKGROUND,
             UI_PANEL_BORDER, 2);
    draw_tabs();

    switch (g_tab) {
    case TAB_STATUS:
        draw_status();
        break;
    case TAB_INSTALL:
        if (mgr_screen() == MGR_SCREEN_INSTALL ||
            mgr_screen() == MGR_SCREEN_TITLES) {
            mgr_draw(g_focus);
        } else {
            draw_install_placeholder();
        }
        break;
    case TAB_LAUNCH:
        mgr_draw(g_focus);
        break;
    case TAB_LOGS:
        draw_logs();
        break;
    default:
        draw_sct();
        break;
    }
    draw_launch_box();
}

static void draw_frame(void)
{
    DEMOGfxBeforeRender();
    draw_target(&DEMOColorBuffer);
    DEMOGfxDoneRender();

    DEMODRCBeforeRender();
    draw_target(&DEMODRCColorBuffer);
    DEMODRCDoneRender();
}

/* ---- lifecycle --------------------------------------------------------- */

static void release_fs(void)
{
    if (!g_fs_ready) {
        return;
    }
    /* Let a check that is still running finish before removing the client. */
    if (g_probe != PROBE_IDLE && !g_fs_stuck) {
        FSStatus ignored;

        if (!wait_op(&ignored)) {
            g_probe = PROBE_IDLE;
        }
    }
    /* FSDelClient waits for pending commands; skip it if one never returns. */
    if (!g_fs_stuck) {
        FSDelClient(&g_fs_client, FS_RET_ALL_ERROR);
    }
    g_fs_ready = FALSE;
}

static void log_console(void)
{
    g_console_type = OSGetConsoleType();
    g_security_level = OSGetSecurityLevel();
    menu_log("console type 0x%08X, security level %u", g_console_type,
             g_security_level);
}

/*
 * Without a UI, still go through ProcUI: request the launch, then keep
 * answering system messages until the system tells this title to exit.
 * Returning from main straight after OSLaunchTitlel left the console hanging.
 */
static int run_without_ui(int code)
{
    DEMOInit();
    request_system_config_tool();
    while (DEMOIsRunning()) {
        if (!g_launch_requested) {
            OSSleepMilliseconds(500);
            request_system_config_tool();
        }
        OSSleepMilliseconds(16);
    }
    release_fs();
    DEMOShutdown();
    return code;
}

int main(int argc, char **argv)
{
    FSStatus fs_status;

    OSReport("CAT Recovery Menu " MENU_VERSION " starting\n");
    menu_log("started " MENU_VERSION " (title version %d)", MENU_TITLE_VERSION);
    snprintf(g_marker_text, sizeof(g_marker_text), "Not checked yet");
    log_console();

    FSInit();
    FSInitCmdBlock(&g_fs_cmd);
    fs_status = FSAddClient(&g_fs_client, FS_RET_ALL_ERROR);
    if (fs_status < FS_STATUS_OK) {
        menu_log("FSAddClient failed before UI: %d", fs_status);
        return run_without_ui(1);
    }
    g_fs_ready = TRUE;

    if (!required_asset_available(DEMO_FONT_SHADER_PATH)) {
        menu_log("UI assets unavailable; falling back to System Config Tool");
        return run_without_ui(2);
    }

    DEMOInit();
    DEMOGfxInit(argc, argv);
    DEMODRCInit(argc, argv);
    DEMOFontInit();
    VPADInit();

    g_ac_ready = ACIsSuccess(ACInitialize());
    if (!g_ac_ready) {
        menu_log("ACInitialize failed; network status unavailable");
    }
    mgr_init();

    start_probe();
    refresh_status();

    while (DEMOIsRunning()) {
        poll_probe();
        mgr_poll();
        if (g_tab == TAB_STATUS && !g_launch_requested) {
            poll_status();
        }
        update_input();
        draw_frame();
    }

    mgr_shutdown();
    if (g_ac_ready) {
        ACFinalize();
    }
    release_fs();
    VPADShutdown();
    DEMOFontShutdown();
    DEMODRCShutdown();
    DEMOGfxShutdown();
    DEMOShutdown();
    return 0;
}
