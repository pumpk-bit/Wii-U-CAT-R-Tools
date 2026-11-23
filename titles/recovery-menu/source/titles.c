#include "titles.h"
#include "menulog.h"
#include "ui.h"

#include <cafe/fs.h>
#include <cafe/os.h>
#include <cafe/os/OSDynLoad.h>

#include <stdio.h>
#include <string.h>

#define SELF_TITLE_ID 0x000500101F700700ULL
#define SCT_TITLE_ID  0x000500101F700500ULL

#define MAX_TITLES    512
#define MAX_PACKAGES  64

/* List geometry in the panel (grid lines). */
#define LIST_Y        4.0f
#define LIST_STEP     1.15f
#define LIST_ROWS     18
#define DETAIL_Y      (LIST_Y + LIST_ROWS * LIST_STEP + 0.6f)

/* Column offsets from UI_TEXT_X. */
#define COL_VERSION   31.0f
#define COL_DEVICE    37.0f
#define COL_FLAG      43.5f

/* System Config Tool mounts the SD card for MCP the same way. */
#define FSA_SD_DEVICE  "/dev/sdcard01"
#define FSA_SD_MOUNT   "/vol/app_sd"
#define SD_INSTALL_DIR "install"

/* Install/uninstall only report progress; a run with no progress at all is
 * treated as finished after this long. */
#define START_GRACE_MS     3000
#define UNINSTALL_LIMIT_MS 120000

/* ---- MCP / FSA layouts (match wut's coreinit headers) ------------------ */

PACKED_STRUCT_BEGIN
typedef struct
{
    u64  titleId;
    u32  groupId;
    char path[56];
    u32  appType;
    u16  titleVersion;
    u64  osVersion;
    u32  sdkVersion;
    char indexedDevice[10];
    u8   unk0x60;
} PACKED_STRUCT_ATTRIBUTE McpTitle;          /* 0x61 bytes */

typedef struct
{
    u32 inProgress;
    u64 tid;
    u64 sizeTotal;
    u64 sizeProgress;
    u32 contentsTotal;
    u32 contentsProgress;
} PACKED_STRUCT_ATTRIBUTE McpProgress;       /* 0x24 bytes */
PACKED_STRUCT_END

#define APP_TYPE_GAME        0x80000000u
#define APP_TYPE_GAME_UPDATE 0x0800001Bu
#define APP_TYPE_GAME_DLC    0x0800000Eu

typedef s32 (*mcp_open_fn)(void);
typedef s32 (*mcp_close_fn)(s32 handle);
typedef s32 (*mcp_title_list_fn)(s32 handle, u32 *count, McpTitle *list, u32 bytes);
typedef s32 (*mcp_get_title_info_fn)(s32 handle, u64 tid, McpTitle *info);
typedef s32 (*mcp_set_target_fn)(s32 handle, u32 target);
typedef s32 (*mcp_install_info_fn)(s32 handle, const char *path, void *out);
typedef s32 (*mcp_install_async_fn)(s32 handle, const char *path, void *out);
typedef s32 (*mcp_progress_fn)(s32 handle, McpProgress *out);
typedef s32 (*mcp_uninstall_async_fn)(s32 handle, const char *path, void *out);
typedef s32 (*fsa_init_fn)(void);
typedef s32 (*fsa_add_client_fn)(void *async_data);
typedef s32 (*fsa_del_client_fn)(s32 client);
typedef s32 (*fsa_mount_fn)(s32 client, const char *src, const char *dst,
                            u32 flags, void *arg, u32 arg_len);
typedef s32 (*fsa_unmount_fn)(s32 client, const char *dst, u32 flags);

static struct
{
    mcp_open_fn            open;
    mcp_close_fn           close;
    mcp_title_list_fn      title_list;
    mcp_get_title_info_fn  get_title_info;
    mcp_set_target_fn      set_target;
    mcp_install_info_fn    install_info;
    mcp_install_async_fn   install_async;
    mcp_progress_fn        progress;
    mcp_uninstall_async_fn uninstall_async;
    fsa_init_fn            fsa_init;
    fsa_add_client_fn      fsa_add_client;
    fsa_del_client_fn      fsa_del_client;
    fsa_mount_fn           fsa_mount;
    fsa_unmount_fn         fsa_unmount;
} g_api;

static BOOL g_api_ok = FALSE;
static char g_missing[96];

/* ---- state ------------------------------------------------------------- */

typedef struct
{
    char name[64];
    u64  tid;
    u16  version;
    BOOL tmd_ok;
    BOOL installed;
    u16  installed_version;
} Package;

typedef enum
{
    JOB_NONE,
    JOB_INSTALL,
    JOB_UNINSTALL
} JobKind;

typedef enum
{
    STAGE_BROWSE,
    STAGE_CONFIRM,
    STAGE_RUNNING,
    STAGE_DONE
} Stage;

static McpTitle g_titles[MAX_TITLES] __attribute__((aligned(0x40)));
static u32 g_title_count = 0;
static BOOL g_titles_ok = FALSE;
static Package g_packages[MAX_PACKAGES];
static int g_package_count = 0;
static BOOL g_packages_ok = FALSE;

/* Rows shown on the current screen: indices into g_titles or g_packages.
 * Only the useful rows, unless Y toggled "show all". */
static int g_view[MAX_TITLES];
static int g_view_count = 0;
static int g_hidden = 0;
static BOOL g_show_all = FALSE;

/* Names come from each installed title's meta/meta.xml, read on demand. */
#define NAME_LEN 48
enum { NAME_UNKNOWN, NAME_OK, NAME_NONE };
static char g_title_names[MAX_TITLES][NAME_LEN];
static u8 g_title_name_state[MAX_TITLES];
static u8 g_meta[0x4000] __attribute__((aligned(FS_IO_BUFFER_ALIGN)));
static int g_name_reads_this_frame = 0;

static u8 g_install_info[0x300] __attribute__((aligned(0x40)));
static u8 g_job_info[0x300] __attribute__((aligned(0x40)));
static McpProgress g_progress __attribute__((aligned(0x40)));
static McpTitle g_probe_title __attribute__((aligned(0x40)));
static u8 g_tmd[0x200] __attribute__((aligned(FS_IO_BUFFER_ALIGN)));

static s32 g_mcp = -1;
static s32 g_fsa = -1;
static BOOL g_fsa_mounted = FALSE;

static FSClient g_fs;
static FSCmdBlock g_fs_cmd;
static BOOL g_fs_ok = FALSE;
static char g_sd_root[FS_MAX_MOUNTPATH_SIZE];   /* PPC-side SD path */
static BOOL g_sd_ok = FALSE;

static MgrScreen g_screen = MGR_SCREEN_NONE;
static Stage g_stage = STAGE_BROWSE;
static int g_cursor = 0;
static int g_scroll = 0;

static JobKind g_job = JOB_NONE;
static u64 g_job_tid = 0;
static char g_job_path[128];
static char g_job_label[64];
static OSTime g_job_started;
static BOOL g_job_seen_progress = FALSE;
static BOOL g_job_ok = FALSE;
static char g_job_text[128];
static u64 g_job_done_bytes = 0;
static u64 g_job_total_bytes = 0;

static char g_status[128] = "Title manager not started";

/* ---- helpers ----------------------------------------------------------- */

static void set_status(const char *text)
{
    snprintf(g_status, sizeof(g_status), "%s", text);
    menu_log("%s", g_status);
}

static u64 read_be64(const u8 *p)
{
    return ((u64)p[0] << 56) | ((u64)p[1] << 48) | ((u64)p[2] << 40) |
           ((u64)p[3] << 32) | ((u64)p[4] << 24) | ((u64)p[5] << 16) |
           ((u64)p[6] << 8) | (u64)p[7];
}

static u32 ms_since(OSTime started)
{
    return (u32)OSTicksToMilliseconds(OSGetTime() - started);
}

static BOOL find(OSDynLoad_ModuleHandle mod, const char *name, void **out)
{
    *out = NULL;
    if (OSDynLoad_FindExport(mod, 0, name, out) != 0 || *out == NULL) {
        if (g_missing[0] == '\0') {
            snprintf(g_missing, sizeof(g_missing), "%s", name);
        }
        OSReport("CAT Recovery Menu: export not found: %s\n", name);
        return FALSE;
    }
    return TRUE;
}

static void resolve_api(void)
{
    OSDynLoad_ModuleHandle core = NULL;
    BOOL ok = TRUE;

    g_missing[0] = '\0';
    if (OSDynLoad_Acquire("coreinit.rpl", &core) != 0 || core == NULL) {
        snprintf(g_missing, sizeof(g_missing), "coreinit.rpl");
        g_api_ok = FALSE;
        return;
    }
    ok &= find(core, "MCP_Open", (void **)&g_api.open);
    ok &= find(core, "MCP_Close", (void **)&g_api.close);
    ok &= find(core, "MCP_TitleList", (void **)&g_api.title_list);
    ok &= find(core, "MCP_GetTitleInfo", (void **)&g_api.get_title_info);
    ok &= find(core, "MCP_InstallSetTargetDevice", (void **)&g_api.set_target);
    ok &= find(core, "MCP_InstallGetInfo", (void **)&g_api.install_info);
    ok &= find(core, "MCP_InstallTitleAsync", (void **)&g_api.install_async);
    ok &= find(core, "MCP_InstallGetProgress", (void **)&g_api.progress);
    if (!find(core, "MCP_UninstallTitleAsync", (void **)&g_api.uninstall_async)) {
        /* System Config Tool itself imports this name for uninstall. */
        g_missing[0] = '\0';
        ok &= find(core, "MCP_DeleteTitleAsync", (void **)&g_api.uninstall_async);
    }
    ok &= find(core, "FSAInit", (void **)&g_api.fsa_init);
    ok &= find(core, "FSAAddClient", (void **)&g_api.fsa_add_client);
    ok &= find(core, "FSADelClient", (void **)&g_api.fsa_del_client);
    ok &= find(core, "FSAMount", (void **)&g_api.fsa_mount);
    ok &= find(core, "FSAUnmount", (void **)&g_api.fsa_unmount);
    /* coreinit stays loaded for the life of the process; keep the handle. */
    g_api_ok = ok;
}

/* Titles that must never be installed over or removed from this menu. */
static BOOL is_boot_critical(u64 tid)
{
    u32 hi = (u32)(tid >> 32);
    u32 lo = (u32)tid;

    if (tid == SELF_TITLE_ID) {
        return TRUE;
    }
    if (hi != 0x00050010) {
        return FALSE;
    }
    return lo == 0x10000100 ||                 /* boot1 */
           lo == 0x10000200 ||                 /* DRH firmware */
           lo == 0x1000400A || lo == 0x1000800A || lo == 0x100040FF || /* OS */
           (lo & 0xFFFFF0FF) == 0x10045000 ||  /* system updater */
           lo == 0x1F700300 || lo == 0x1F700400; /* DRC firmware/language */
}

/* A PC copy listed while PCFS is mounted: its files live on the host. */
static BOOL is_pcfs_copy(const McpTitle *t)
{
    return strncmp(t->indexedDevice, "hfio", 4) == 0 ||
           strstr(t->path, "hfio") != NULL;
}

static BOOL can_uninstall(const McpTitle *t)
{
    if (t->titleId == SCT_TITLE_ID || is_boot_critical(t->titleId) ||
        is_pcfs_copy(t)) {
        return FALSE;
    }
    return t->appType == APP_TYPE_GAME || t->appType == APP_TYPE_GAME_UPDATE ||
           t->appType == APP_TYPE_GAME_DLC;
}

/*
 * Games only. OSLaunchTitlel from this master title makes the launched title
 * the new master. A system applet started that way has no game behind it:
 * e-Manual (000500301001710A) threw 157-0100 and the console went into a
 * fatal-error shutdown (2026-11-17).
 */
static BOOL can_launch(const McpTitle *t)
{
    if (t->titleId == SELF_TITLE_ID || is_boot_critical(t->titleId)) {
        return FALSE;
    }
    return t->appType == APP_TYPE_GAME;
}

static int installed_index(u64 tid)
{
    u32 i;

    for (i = 0; i < g_title_count; i++) {
        if (g_titles[i].titleId == tid && !is_pcfs_copy(&g_titles[i])) {
            return (int)i;
        }
    }
    for (i = 0; i < g_title_count; i++) {
        if (g_titles[i].titleId == tid) {
            return (int)i;
        }
    }
    return -1;
}

static const McpTitle *installed_info(u64 tid)
{
    int i = installed_index(tid);
    return i >= 0 ? &g_titles[i] : NULL;
}

/* Copies the text of <tag ...>text</tag> as printable ASCII (the DEMO font
 * has no other glyphs). Returns FALSE if the tag is missing or empty. */
static BOOL xml_text(const char *xml, const char *tag, char *out, int out_len)
{
    char open[40];
    const char *p, *end;
    int n = 0;

    snprintf(open, sizeof(open), "<%s", tag);
    p = strstr(xml, open);
    if (p == NULL) {
        return FALSE;
    }
    p = strchr(p, '>');
    if (p == NULL) {
        return FALSE;
    }
    p++;
    end = strchr(p, '<');
    if (end == NULL) {
        return FALSE;
    }
    while (p < end && n < out_len - 1) {
        unsigned char c = (unsigned char)*p;

        if (c == '&') {
            const char *semi = strchr(p, ';');
            char ch = '?';

            if (semi && semi < end) {
                if (strncmp(p, "&amp;", 5) == 0) ch = '&';
                else if (strncmp(p, "&lt;", 4) == 0) ch = '<';
                else if (strncmp(p, "&gt;", 4) == 0) ch = '>';
                else if (strncmp(p, "&quot;", 6) == 0) ch = '"';
                else if (strncmp(p, "&apos;", 6) == 0) ch = '\'';
                p = semi + 1;
            } else {
                p++;
            }
            out[n++] = ch;
            continue;
        }
        if (c == '\r' || c == '\n' || c == '\t') {
            if (n > 0 && out[n - 1] != ' ') {
                out[n++] = ' ';
            }
        } else if (c >= 0x20 && c < 0x7F) {
            out[n++] = (char)c;
        } else if (c >= 0xC0) {
            out[n++] = '?';     /* one '?' per multi-byte UTF-8 character */
        }
        p++;
    }
    while (n > 0 && out[n - 1] == ' ') {
        n--;
    }
    out[n] = '\0';
    return n > 0;
}

static BOOL ensure_fs(void)
{
    if (!g_fs_ok) {
        FSInitCmdBlock(&g_fs_cmd);
        if (FSAddClient(&g_fs, FS_RET_ALL_ERROR) < FS_STATUS_OK) {
            set_status("FSAddClient failed for the title manager");
            return FALSE;
        }
        g_fs_ok = TRUE;
    }
    return TRUE;
}

static BOOL read_title_name(const McpTitle *t, char *out, int out_len)
{
    char path[FS_MAX_FULLPATH_SIZE];
    FSFileHandle fh;
    FSStatus st;

    snprintf(path, sizeof(path), "%.*s/meta/meta.xml", (int)sizeof(t->path),
             t->path);
    if (FSOpenFile(&g_fs, &g_fs_cmd, path, "r", &fh, FS_RET_ALL_ERROR) !=
        FS_STATUS_OK) {
        return FALSE;
    }
    st = FSReadFile(&g_fs, &g_fs_cmd, g_meta, 1, sizeof(g_meta) - 1, fh, 0,
                    FS_RET_ALL_ERROR);
    FSCloseFile(&g_fs, &g_fs_cmd, fh, FS_RET_ALL_ERROR);
    if (st <= 0) {
        return FALSE;
    }
    g_meta[st] = '\0';
    return xml_text((const char *)g_meta, "longname_en", out, out_len) ||
           xml_text((const char *)g_meta, "shortname_en", out, out_len);
}

/*
 * Names of PC copies (PCFS). A PCFS read whose host link dropped never
 * returns, so these are read asynchronously, one file at a time, on a separate
 * FS client and command block, with a 3 s deadline. After a timeout the
 * stuck command still owns that client: PC names stop until the next start
 * and the client is never deleted.
 */
#define PC_NAME_TIMEOUT_MS 3000

typedef enum
{
    PC_IDLE,
    PC_OPEN,
    PC_READ,
    PC_CLOSE
} PcStep;

static FSClient g_pc_fs;
static FSCmdBlock g_pc_cmd;
static FSAsyncParams g_pc_async;
static volatile BOOL g_pc_done = FALSE;
static volatile FSStatus g_pc_result = 0;
static BOOL g_pc_ready = FALSE;
static BOOL g_pc_stuck = FALSE;
static PcStep g_pc_step = PC_IDLE;
static u64 g_pc_tid = 0;
static OSTime g_pc_started;
static FSFileHandle g_pc_fh;
static FSStatus g_pc_read = 0;
static u8 g_pc_meta[0x4000] __attribute__((aligned(FS_IO_BUFFER_ALIGN)));

static void pc_done(FSClient *client, FSCmdBlock *block, FSStatus result,
                    void *context)
{
    (void)client;
    (void)block;
    (void)context;
    g_pc_result = result;
    g_pc_done = TRUE;
}

static const FSAsyncParams *pc_begin(void)
{
    g_pc_done = FALSE;
    g_pc_result = FS_STATUS_FATAL_ERROR;
    g_pc_async.userCallback = pc_done;
    g_pc_async.userContext = NULL;
    g_pc_async.ioMsgQueue = NULL;
    g_pc_started = OSGetTime();
    return &g_pc_async;
}

/* Sets the name (or "none") on every PC-copy row of the title just read. */
static void pc_store(u64 tid, const char *name)
{
    u32 i;

    for (i = 0; i < g_title_count; i++) {
        if (g_titles[i].titleId == tid && is_pcfs_copy(&g_titles[i])) {
            if (name != NULL) {
                snprintf(g_title_names[i], NAME_LEN, "%s", name);
                g_title_name_state[i] = NAME_OK;
            } else {
                g_title_name_state[i] = NAME_NONE;
            }
        }
    }
}

static void pc_finish(BOOL ok)
{
    char name[NAME_LEN];

    if (ok && g_pc_read > 0) {
        g_pc_meta[g_pc_read] = '\0';
        if (xml_text((const char *)g_pc_meta, "longname_en", name, NAME_LEN) ||
            xml_text((const char *)g_pc_meta, "shortname_en", name, NAME_LEN)) {
            pc_store(g_pc_tid, name);
            g_pc_step = PC_IDLE;
            return;
        }
    }
    pc_store(g_pc_tid, NULL);
    g_pc_step = PC_IDLE;
}

/* Index of a PC copy whose name is still unknown and has no own copy. */
static int pc_next_unknown(void)
{
    u32 i;

    for (i = 0; i < g_title_count; i++) {
        const McpTitle *t = &g_titles[i];
        int own;

        if (!is_pcfs_copy(t) || g_title_name_state[i] != NAME_UNKNOWN) {
            continue;
        }
        own = installed_index(t->titleId);
        if (own >= 0 && !is_pcfs_copy(&g_titles[own])) {
            continue;       /* the console's own copy gives the name */
        }
        return (int)i;
    }
    return -1;
}

/* One step per frame; never blocks. */
static void pc_poll(void)
{
    char path[FS_MAX_FULLPATH_SIZE];
    int i;

    if (g_pc_stuck) {
        return;
    }
    if (g_pc_step == PC_IDLE) {
        i = pc_next_unknown();
        if (i < 0) {
            return;
        }
        if (!g_pc_ready) {
            FSInitCmdBlock(&g_pc_cmd);
            if (FSAddClient(&g_pc_fs, FS_RET_ALL_ERROR) < FS_STATUS_OK) {
                menu_log("PC title names: FSAddClient failed");
                g_pc_stuck = TRUE;
                return;
            }
            g_pc_ready = TRUE;
        }
        g_pc_tid = g_titles[i].titleId;
        snprintf(path, sizeof(path), "%.*s/meta/meta.xml",
                 (int)sizeof(g_titles[i].path), g_titles[i].path);
        if (FSOpenFileAsync(&g_pc_fs, &g_pc_cmd, path, "r", &g_pc_fh,
                            FS_RET_ALL_ERROR, pc_begin()) != FS_STATUS_OK) {
            pc_finish(FALSE);
            return;
        }
        g_pc_step = PC_OPEN;
        return;
    }
    if (!g_pc_done) {
        if (ms_since(g_pc_started) > PC_NAME_TIMEOUT_MS) {
            g_pc_stuck = TRUE;
            pc_store(g_pc_tid, NULL);
            menu_log("PC title names: no answer after 3 s (PCFS link down?); "
                     "stopped until restart");
        }
        return;
    }
    switch (g_pc_step) {
    case PC_OPEN:
        if (g_pc_result < FS_STATUS_OK) {
            pc_finish(FALSE);
            return;
        }
        g_pc_read = 0;
        if (FSReadFileAsync(&g_pc_fs, &g_pc_cmd, g_pc_meta, 1,
                            sizeof(g_pc_meta) - 1, g_pc_fh, 0,
                            FS_RET_ALL_ERROR, pc_begin()) != FS_STATUS_OK) {
            g_pc_read = FS_STATUS_FATAL_ERROR;
            if (FSCloseFileAsync(&g_pc_fs, &g_pc_cmd, g_pc_fh, FS_RET_ALL_ERROR,
                                 pc_begin()) != FS_STATUS_OK) {
                pc_finish(FALSE);
                return;
            }
            g_pc_step = PC_CLOSE;
            return;
        }
        g_pc_step = PC_READ;
        return;
    case PC_READ:
        g_pc_read = g_pc_result;
        if (FSCloseFileAsync(&g_pc_fs, &g_pc_cmd, g_pc_fh, FS_RET_ALL_ERROR,
                             pc_begin()) != FS_STATUS_OK) {
            pc_finish(g_pc_read > 0);
            return;
        }
        g_pc_step = PC_CLOSE;
        return;
    case PC_CLOSE:
        pc_finish(g_pc_read > 0);
        return;
    default:
        g_pc_step = PC_IDLE;
        return;
    }
}

static void pc_shutdown(void)
{
    if (!g_pc_ready) {
        return;
    }
    /* Give a running read its deadline; never delete a stuck client. */
    while (g_pc_step != PC_IDLE && !g_pc_stuck) {
        pc_poll();
        OSSleepMilliseconds(5);
    }
    if (!g_pc_stuck) {
        FSDelClient(&g_pc_fs, FS_RET_ALL_ERROR);
    }
    g_pc_ready = FALSE;
}

/*
 * Title name for row i, or NULL if it has none (or is not read yet).
 * At most a few meta.xml reads per frame so scrolling stays smooth. PC copies
 * use the console's own copy's name, else the asynchronous reader above.
 */
static const char *title_name(int i)
{
    const McpTitle *t;

    if (i < 0 || i >= (int)g_title_count) {
        return NULL;
    }
    t = &g_titles[i];
    if (is_pcfs_copy(t)) {
        int own = installed_index(t->titleId);

        if (own >= 0 && own != i && !is_pcfs_copy(&g_titles[own])) {
            return title_name(own);
        }
        return g_title_name_state[i] == NAME_OK ? g_title_names[i] : NULL;
    }
    if (g_title_name_state[i] == NAME_UNKNOWN && ensure_fs() &&
        g_name_reads_this_frame < 4) {
        g_name_reads_this_frame++;
        g_title_name_state[i] =
            read_title_name(t, g_title_names[i], NAME_LEN) ? NAME_OK : NAME_NONE;
    }
    return g_title_name_state[i] == NAME_OK ? g_title_names[i] : NULL;
}

/* ---- MCP / SD setup ---------------------------------------------------- */

static BOOL open_mcp(void)
{
    if (g_mcp >= 0) {
        return TRUE;
    }
    if (!g_api_ok) {
        return FALSE;
    }
    g_mcp = g_api.open();
    if (g_mcp < 0) {
        char text[64];
        snprintf(text, sizeof(text), "MCP_Open failed: %d", g_mcp);
        set_status(text);
        return FALSE;
    }
    return TRUE;
}

static void sort_titles(void)
{
    u32 i, j;
    McpTitle tmp;

    for (i = 1; i < g_title_count; i++) {
        tmp = g_titles[i];
        j = i;
        while (j > 0 && g_titles[j - 1].titleId > tmp.titleId) {
            g_titles[j] = g_titles[j - 1];
            j--;
        }
        g_titles[j] = tmp;
    }
}

static BOOL refresh_titles(void)
{
    u32 count = 0;
    u32 old_count = g_title_count;
    u64 first = old_count > 0 ? g_titles[0].titleId : 0;
    u64 last = old_count > 0 ? g_titles[old_count - 1].titleId : 0;
    s32 rc;

    if (!open_mcp()) {
        g_titles_ok = FALSE;
        return FALSE;
    }
    memset(g_titles, 0, sizeof(g_titles));
    rc = g_api.title_list(g_mcp, &count, g_titles, sizeof(g_titles));
    if (rc < 0) {
        char text[64];
        snprintf(text, sizeof(text), "MCP_TitleList failed: %d", rc);
        set_status(text);
        g_title_count = 0;
        g_titles_ok = FALSE;
        memset(g_title_name_state, 0, sizeof(g_title_name_state));
        return FALSE;
    }
    g_title_count = count > MAX_TITLES ? MAX_TITLES : count;
    sort_titles();
    g_titles_ok = TRUE;
    /* Names are cached by row; keep them when the list did not change. */
    if (g_title_count != old_count ||
        (g_title_count > 0 && (g_titles[0].titleId != first ||
                               g_titles[g_title_count - 1].titleId != last))) {
        memset(g_title_name_state, 0, sizeof(g_title_name_state));
    }
    return TRUE;
}

/* PPC side: read the package list and TMDs (the normal SDK mount). */
static BOOL mount_sd(void)
{
    FSMountSource source;
    FSStatus st;
    char text[96];

    if (!ensure_fs()) {
        return FALSE;
    }

    if (!g_sd_ok) {
        st = FSGetMountSource(&g_fs, &g_fs_cmd, FS_SOURCETYPE_EXTERNAL,
                              &source, FS_RET_ALL_ERROR);
        if (st == FS_STATUS_OK) {
            st = FSMount(&g_fs, &g_fs_cmd, &source, g_sd_root,
                         sizeof(g_sd_root), FS_RET_ALL_ERROR);
        }
        if (st != FS_STATUS_OK) {
            snprintf(text, sizeof(text), "SD mount failed: %d (card inserted?)", st);
            set_status(text);
            return FALSE;
        }
        g_sd_ok = TRUE;
        menu_log("SD mounted at %s", g_sd_root);
    }
    return TRUE;
}

/*
 * IOSU side: MCP reads packages from /vol/app_sd, like SCT. Done only once an
 * install is confirmed, never just to list packages: a second FSA mount of
 * sdcard01 (over wupserver, 2026-11-14) hung the whole console.
 */
static BOOL mount_sd_for_mcp(void)
{
    char text[96];

    if (!g_fsa_mounted) {
        s32 rc;

        if (g_fsa < 0) {
            g_api.fsa_init();
            g_fsa = g_api.fsa_add_client(NULL);
            if (g_fsa < 0) {
                snprintf(text, sizeof(text), "FSAAddClient failed: %d", g_fsa);
                set_status(text);
                return FALSE;
            }
        }
        rc = g_api.fsa_mount(g_fsa, FSA_SD_DEVICE, FSA_SD_MOUNT, 2, NULL, 0);
        menu_log("FSAMount %s -> %s (global) = %d", FSA_SD_DEVICE,
                 FSA_SD_MOUNT, rc);
        if (rc < 0) {
            rc = g_api.fsa_mount(g_fsa, FSA_SD_DEVICE, FSA_SD_MOUNT, 0, NULL, 0);
            menu_log("FSAMount (local) = %d", rc);
        }
        /* A failure here may just mean it is already mounted; MCP will
         * report a bad path at install time if not. */
        g_fsa_mounted = (rc >= 0);
    }
    return TRUE;
}

static BOOL read_tmd(const char *dir, u64 *tid, u16 *version)
{
    char path[FS_MAX_FULLPATH_SIZE];
    FSFileHandle fh;
    FSStatus st;

    snprintf(path, sizeof(path), "%s/%s/%s/title.tmd", g_sd_root,
             SD_INSTALL_DIR, dir);
    if (FSOpenFile(&g_fs, &g_fs_cmd, path, "r", &fh, FS_RET_ALL_ERROR) !=
        FS_STATUS_OK) {
        return FALSE;
    }
    st = FSReadFile(&g_fs, &g_fs_cmd, g_tmd, 1, sizeof(g_tmd), fh, 0,
                    FS_RET_ALL_ERROR);
    FSCloseFile(&g_fs, &g_fs_cmd, fh, FS_RET_ALL_ERROR);
    if (st < 0x1E0) {
        return FALSE;
    }
    *tid = read_be64(g_tmd + 0x18C);
    *version = (u16)((g_tmd[0x1DC] << 8) | g_tmd[0x1DD]);
    return TRUE;
}

static BOOL scan_packages(void)
{
    char path[FS_MAX_FULLPATH_SIZE];
    FSDirHandle dh;
    FSDirEntry entry;
    FSStatus st;
    int i;

    g_package_count = 0;
    g_packages_ok = FALSE;
    if (!mount_sd()) {
        return FALSE;
    }
    snprintf(path, sizeof(path), "%s/%s", g_sd_root, SD_INSTALL_DIR);
    st = FSOpenDir(&g_fs, &g_fs_cmd, path, &dh, FS_RET_ALL_ERROR);
    if (st != FS_STATUS_OK) {
        char text[96];
        snprintf(text, sizeof(text), "No /%s folder on SD (%d)", SD_INSTALL_DIR, st);
        set_status(text);
        return FALSE;
    }
    while (g_package_count < MAX_PACKAGES &&
           FSReadDir(&g_fs, &g_fs_cmd, dh, &entry, FS_RET_ALL_ERROR) ==
               FS_STATUS_OK) {
        Package *p;

        if (!(entry.stat.flag & FS_STAT_FLAG_IS_DIRECTORY) ||
            entry.name[0] == '.') {
            continue;
        }
        p = &g_packages[g_package_count++];
        memset(p, 0, sizeof(*p));
        snprintf(p->name, sizeof(p->name), "%s", entry.name);
        p->tmd_ok = read_tmd(p->name, &p->tid, &p->version);
    }
    FSCloseDir(&g_fs, &g_fs_cmd, dh, FS_RET_ALL_ERROR);

    for (i = 0; i < g_package_count; i++) {
        const McpTitle *t = g_packages[i].tmd_ok ?
                            installed_info(g_packages[i].tid) : NULL;
        g_packages[i].installed = (t != NULL);
        g_packages[i].installed_version = t ? t->titleVersion : 0;
    }
    g_packages_ok = TRUE;
    return TRUE;
}

/* Why a package may not be installed, or NULL if it may. */
static const char *install_block_reason(const Package *p)
{
    if (!p->tmd_ok) {
        return "No readable title.tmd in this folder";
    }
    if (is_boot_critical(p->tid)) {
        return "Boot-critical title: blocked here";
    }
    if (p->installed && p->installed_version > p->version) {
        return "Older than the installed version";
    }
    return NULL;
}

/* Name of the title a package holds if it is installed, else its folder. */
static const char *package_name(const Package *p)
{
    const char *name = NULL;

    if (p->tmd_ok && p->installed) {
        name = title_name(installed_index(p->tid));
    }
    return name ? name : p->name;
}

static void build_view(void)
{
    int i, total;

    g_view_count = 0;
    if (g_screen == MGR_SCREEN_INSTALL) {
        total = g_package_count;
        for (i = 0; i < total; i++) {
            if (g_show_all || install_block_reason(&g_packages[i]) == NULL) {
                g_view[g_view_count++] = i;
            }
        }
    } else {
        total = (int)g_title_count;
        for (i = 0; i < total; i++) {
            const McpTitle *t = &g_titles[i];
            BOOL show;

            if (g_screen == MGR_SCREEN_LAUNCH) {
                show = can_launch(t);
            } else {
                show = g_show_all || can_uninstall(t);
            }
            if (show) {
                g_view[g_view_count++] = i;
            }
        }
    }
    g_hidden = total - g_view_count;
    if (g_cursor >= g_view_count) {
        g_cursor = g_view_count > 0 ? g_view_count - 1 : 0;
    }
    if (g_scroll > g_cursor) {
        g_scroll = g_cursor;
    }
}

/* ---- jobs -------------------------------------------------------------- */

static void finish_job(BOOL ok, const char *text)
{
    g_job_ok = ok;
    snprintf(g_job_text, sizeof(g_job_text), "%s", text);
    menu_log("AUDIT %s %s %016llX: %s",
             g_job == JOB_INSTALL ? "install" : "uninstall", g_job_label,
             g_job_tid, ok ? "OK" : "FAILED");
    menu_log("%s", g_job_text);
    g_stage = STAGE_DONE;
    refresh_titles();
    if (g_screen == MGR_SCREEN_INSTALL) {
        scan_packages();
    }
    build_view();
}

static void start_install(const Package *p)
{
    s32 rc;
    char text[128];

    g_job = JOB_INSTALL;
    g_job_tid = p->tid;
    g_job_done_bytes = 0;
    g_job_total_bytes = 0;
    snprintf(g_job_label, sizeof(g_job_label), "%s", package_name(p));
    snprintf(g_job_path, sizeof(g_job_path), "%s/%s/%s", FSA_SD_MOUNT,
             SD_INSTALL_DIR, p->name);
    menu_log("AUDIT install start %s (%016llX v%u) from %s", p->name, p->tid,
             p->version, g_job_path);

    if (!mount_sd_for_mcp()) {
        g_stage = STAGE_RUNNING;
        finish_job(FALSE, g_status);
        return;
    }
    g_api.set_target(g_mcp, 0); /* MLC */
    memset(g_install_info, 0, sizeof(g_install_info));
    rc = g_api.install_info(g_mcp, g_job_path, g_install_info);
    if (rc < 0) {
        snprintf(text, sizeof(text), "MCP_InstallGetInfo failed: %d (0x%08X)",
                 rc, (u32)rc);
        g_stage = STAGE_RUNNING;
        finish_job(FALSE, text);
        return;
    }
    memset(g_job_info, 0, sizeof(g_job_info));
    rc = g_api.install_async(g_mcp, g_job_path, g_job_info);
    if (rc < 0) {
        snprintf(text, sizeof(text), "MCP_InstallTitleAsync failed: %d (0x%08X)",
                 rc, (u32)rc);
        g_stage = STAGE_RUNNING;
        finish_job(FALSE, text);
        return;
    }
    g_job_started = OSGetTime();
    g_job_seen_progress = FALSE;
    g_stage = STAGE_RUNNING;
    snprintf(g_job_text, sizeof(g_job_text), "Starting install...");
}

static void start_uninstall(int index)
{
    const McpTitle *t = &g_titles[index];
    const char *name = title_name(index);
    s32 rc;
    char text[128];

    g_job = JOB_UNINSTALL;
    g_job_tid = t->titleId;
    if (name != NULL) {
        snprintf(g_job_label, sizeof(g_job_label), "%s", name);
    } else {
        snprintf(g_job_label, sizeof(g_job_label), "%016llX", t->titleId);
    }
    snprintf(g_job_path, sizeof(g_job_path), "%.*s",
             (int)sizeof(t->path), t->path);
    menu_log("AUDIT uninstall start %016llX at %s", t->titleId, g_job_path);

    memset(g_job_info, 0, sizeof(g_job_info));
    rc = g_api.uninstall_async(g_mcp, g_job_path, g_job_info);
    g_stage = STAGE_RUNNING;
    if (rc < 0) {
        snprintf(text, sizeof(text), "Uninstall call failed: %d (0x%08X)",
                 rc, (u32)rc);
        finish_job(FALSE, text);
        return;
    }
    g_job_started = OSGetTime();
    snprintf(g_job_text, sizeof(g_job_text), "Uninstalling...");
}

static void poll_install(void)
{
    s32 rc;
    char text[128];
    u32 result;

    memset(&g_progress, 0, sizeof(g_progress));
    rc = g_api.progress(g_mcp, &g_progress);
    if (rc >= 0 && g_progress.inProgress) {
        g_job_seen_progress = TRUE;
        if (g_progress.sizeTotal > 0) {
            g_job_done_bytes = g_progress.sizeProgress;
            g_job_total_bytes = g_progress.sizeTotal;
            snprintf(g_job_text, sizeof(g_job_text), "%u of %u contents",
                     g_progress.contentsProgress, g_progress.contentsTotal);
        }
        return;
    }
    if (!g_job_seen_progress && ms_since(g_job_started) < START_GRACE_MS) {
        return;
    }
    /* Done: MCP writes the result into the first word of the info buffer. */
    result = ((u32)g_job_info[0] << 24) | ((u32)g_job_info[1] << 16) |
             ((u32)g_job_info[2] << 8) | g_job_info[3];
    if (result == 0) {
        const McpTitle *t;

        refresh_titles();
        t = installed_info(g_job_tid);
        g_job_done_bytes = g_job_total_bytes;
        snprintf(text, sizeof(text), "Install done: %016llX now v%u",
                 g_job_tid, t ? t->titleVersion : 0);
        finish_job(TRUE, text);
    } else {
        snprintf(text, sizeof(text), "Install failed: 0x%08X", result);
        finish_job(FALSE, text);
    }
}

static void poll_uninstall(void)
{
    char text[128];
    s32 rc;

    memset(&g_probe_title, 0, sizeof(g_probe_title));
    rc = g_api.get_title_info(g_mcp, g_job_tid, &g_probe_title);
    if (rc < 0) {
        snprintf(text, sizeof(text), "Uninstall done: %016llX removed", g_job_tid);
        finish_job(TRUE, text);
        return;
    }
    if (ms_since(g_job_started) > UNINSTALL_LIMIT_MS) {
        snprintf(text, sizeof(text),
                 "Title still listed after %u s; check SCT's Title Manager",
                 UNINSTALL_LIMIT_MS / 1000);
        finish_job(FALSE, text);
    }
}

/* ---- public ------------------------------------------------------------ */

void mgr_init(void)
{
    resolve_api();
    if (g_api_ok) {
        set_status("Title manager ready");
        refresh_titles();
    } else {
        char text[128];
        snprintf(text, sizeof(text), "Title manager disabled: %s missing",
                 g_missing);
        set_status(text);
    }
}

void mgr_shutdown(void)
{
    pc_shutdown();
    if (g_fsa >= 0) {
        if (g_fsa_mounted) {
            g_api.fsa_unmount(g_fsa, FSA_SD_MOUNT, 2);
        }
        g_api.fsa_del_client(g_fsa);
        g_fsa = -1;
    }
    if (g_fs_ok) {
        if (g_sd_ok) {
            FSUnmount(&g_fs, &g_fs_cmd, g_sd_root, FS_RET_ALL_ERROR);
        }
        FSDelClient(&g_fs, FS_RET_ALL_ERROR);
        g_fs_ok = FALSE;
    }
    if (g_mcp >= 0) {
        g_api.close(g_mcp);
        g_mcp = -1;
    }
}

BOOL mgr_open(MgrScreen screen)
{
    BOOL ok;

    if (g_stage == STAGE_RUNNING) {
        return FALSE;
    }
    if (screen != g_screen) {
        g_cursor = 0;
        g_scroll = 0;
        g_show_all = FALSE;
    }
    g_screen = screen;
    g_stage = STAGE_BROWSE;
    g_job = JOB_NONE;
    g_package_count = 0;
    g_packages_ok = FALSE;
    if (!g_api_ok) {
        g_view_count = 0;
        return FALSE;
    }
    ok = refresh_titles();
    if (ok && screen == MGR_SCREEN_INSTALL) {
        ok = scan_packages();
    }
    build_view();
    return ok;
}

MgrScreen mgr_screen(void)
{
    return g_screen;
}

BOOL mgr_busy(void)
{
    return g_stage == STAGE_RUNNING;
}

BOOL mgr_box_up(void)
{
    return g_stage != STAGE_BROWSE;
}

const char *mgr_status_line(void)
{
    return g_status;
}

void mgr_poll(void)
{
    g_name_reads_this_frame = 0;   /* called once per frame */
    if (g_stage != STAGE_RUNNING && g_titles_ok) {
        pc_poll();
    }
    if (g_stage != STAGE_RUNNING) {
        return;
    }
    if (g_job == JOB_INSTALL) {
        poll_install();
    } else if (g_job == JOB_UNINSTALL) {
        poll_uninstall();
    }
}

BOOL mgr_counts(MgrCounts *out)
{
    u32 i;

    memset(out, 0, sizeof(*out));
    if (g_stage == STAGE_RUNNING || !refresh_titles()) {
        return FALSE;
    }
    if (g_screen != MGR_SCREEN_INSTALL) {
        build_view();
    }
    for (i = 0; i < g_title_count; i++) {
        const McpTitle *t = &g_titles[i];

        out->total++;
        if (is_pcfs_copy(t)) {
            out->pcfs++;
        } else if (strncmp(t->indexedDevice, "mlc", 3) == 0) {
            out->mlc++;
        } else if (strncmp(t->indexedDevice, "usb", 3) == 0) {
            out->usb++;
        } else if (strncmp(t->indexedDevice, "slc", 3) == 0) {
            out->slc++;
        }
    }
    return TRUE;
}

const char *mgr_title_name(u64 tid)
{
    return title_name(installed_index(tid));
}

/* Index into g_titles / g_packages of the selected row. */
static int selected_index(void)
{
    return g_view[g_cursor];
}

u64 mgr_input(const VPADStatus *s)
{
    int count = g_view_count;

    if (g_stage == STAGE_RUNNING) {
        return 0;
    }
    if (g_stage == STAGE_DONE) {
        if (s->trig & (VPAD_BUTTON_A | VPAD_BUTTON_B)) {
            g_stage = STAGE_BROWSE;
            g_job = JOB_NONE;
        }
        return 0;
    }
    if (g_stage == STAGE_CONFIRM) {
        if (s->trig & VPAD_BUTTON_B) {
            g_stage = STAGE_BROWSE;
            return 0;
        }
        /* Deliberate two-hand confirm: hold L and R, press A. */
        if ((s->trig & VPAD_BUTTON_A) && (s->hold & VPAD_BUTTON_L) &&
            (s->hold & VPAD_BUTTON_R)) {
            if (g_screen == MGR_SCREEN_INSTALL) {
                start_install(&g_packages[selected_index()]);
            } else {
                start_uninstall(selected_index());
            }
        }
        return 0;
    }

    if ((s->trig & VPAD_BUTTON_MINUS) &&
        (g_screen == MGR_SCREEN_INSTALL || g_screen == MGR_SCREEN_TITLES)) {
        mgr_open(g_screen == MGR_SCREEN_INSTALL ? MGR_SCREEN_TITLES :
                                                  MGR_SCREEN_INSTALL);
        return 0;
    }
    if (s->trig & VPAD_BUTTON_X) {
        mgr_open(g_screen);
        count = g_view_count;
    }
    if ((s->trig & VPAD_BUTTON_Y) && g_screen != MGR_SCREEN_LAUNCH) {
        g_show_all = !g_show_all;
        g_cursor = 0;
        g_scroll = 0;
        build_view();
        count = g_view_count;
    }
    if (count <= 0) {
        g_cursor = 0;
        return 0;
    }
    if (s->trig & VPAD_BUTTON_DOWN) {
        g_cursor = (g_cursor + 1) % count;
    }
    if (s->trig & VPAD_BUTTON_UP) {
        g_cursor = (g_cursor + count - 1) % count;
    }
    if (s->trig & VPAD_BUTTON_RIGHT) {
        g_cursor = g_cursor + LIST_ROWS < count ? g_cursor + LIST_ROWS : count - 1;
    }
    if (s->trig & VPAD_BUTTON_LEFT) {
        g_cursor = g_cursor - LIST_ROWS > 0 ? g_cursor - LIST_ROWS : 0;
    }
    if (g_cursor >= count) {
        g_cursor = count - 1;
    }

    if (s->trig & VPAD_BUTTON_A) {
        if (g_screen == MGR_SCREEN_INSTALL) {
            if (install_block_reason(&g_packages[selected_index()]) == NULL) {
                g_stage = STAGE_CONFIRM;
            }
        } else if (g_screen == MGR_SCREEN_TITLES) {
            if (can_uninstall(&g_titles[selected_index()])) {
                g_stage = STAGE_CONFIRM;
            }
        } else if (g_screen == MGR_SCREEN_LAUNCH) {
            const McpTitle *t = &g_titles[selected_index()];
            if (can_launch(t)) {
                return t->titleId;
            }
        }
    }
    return 0;
}

/* ---- drawing ----------------------------------------------------------- */

static void draw_row_background(f32 y, BOOL selected, BOOL focused)
{
    if (selected) {
        ui_rect(UI_PANEL_X0 + 0.6f, y - 0.05f, UI_PANEL_X1 - 0.6f, y + 1.1f,
                focused ? UI_ROW_HIGHLIGHT : UI_ROW_HIGHLIGHT_IDLE);
    }
}

static UiColor row_color(BOOL selected, BOOL focused, BOOL usable)
{
    if (selected && focused) {
        return UI_ROW_TEXT;
    }
    return usable ? UI_TEXT : UI_TEXT_DIM;
}

static void draw_title_row(f32 y, int i, BOOL selected, BOOL focused)
{
    const McpTitle *t = &g_titles[i];
    BOOL usable = g_screen == MGR_SCREEN_LAUNCH ? can_launch(t) : can_uninstall(t);
    const char *name = title_name(i);
    UiColor c = row_color(selected, focused, usable);
    char dev[12];

    draw_row_background(y, selected, focused);
    if (name != NULL) {
        ui_text(UI_TEXT_X, y, c, "%.44s", name);
    } else {
        ui_text(UI_TEXT_X, y, c, "%016llX", t->titleId);
    }
    ui_text(UI_TEXT_X + COL_VERSION, y, c, "v%u", t->titleVersion);
    snprintf(dev, sizeof(dev), "%.*s", (int)sizeof(t->indexedDevice),
             t->indexedDevice);
    ui_text(UI_TEXT_X + COL_DEVICE, y, c, "%s", is_pcfs_copy(t) ? "PC" : dev);
    if (!usable) {
        ui_text(UI_TEXT_X + COL_FLAG, y, c, "locked");
    }
}

static void draw_package_row(f32 y, int i, BOOL selected, BOOL focused)
{
    const Package *p = &g_packages[i];
    const char *block = install_block_reason(p);
    UiColor c = row_color(selected, focused, block == NULL);

    draw_row_background(y, selected, focused);
    if (!p->tmd_ok) {
        ui_text(UI_TEXT_X, y, c, "%.44s", p->name);
        ui_text(UI_TEXT_X + COL_VERSION, y, c, "no title.tmd");
        return;
    }
    ui_text(UI_TEXT_X, y, c, "%.44s", package_name(p));
    ui_text(UI_TEXT_X + COL_VERSION, y, c, "v%u", p->version);
    if (p->installed) {
        ui_text(UI_TEXT_X + COL_DEVICE, y, c, "have v%u", p->installed_version);
    } else {
        ui_text(UI_TEXT_X + COL_DEVICE, y, c, "new");
    }
}

static const char *screen_title(void)
{
    switch (g_screen) {
    case MGR_SCREEN_INSTALL: return "Install from SD card  (sd:/install)";
    case MGR_SCREEN_TITLES:  return "Installed titles  (uninstall)";
    case MGR_SCREEN_LAUNCH:  return "Launch a title";
    default:                 return "";
    }
}

static const char *footer_text(BOOL focused)
{
    if (!focused) {
        return "A: open   D-Pad: choose a menu   +: System Config Tool";
    }
    switch (g_screen) {
    case MGR_SCREEN_INSTALL:
        return g_show_all ? "A: install  X: rescan  Y: installable only  -: uninstall  B: back"
                          : "A: install  X: rescan  Y: show all  -: uninstall  B: back";
    case MGR_SCREEN_TITLES:
        return g_show_all ? "A: uninstall  X: refresh  Y: removable only  -: install  B: back"
                          : "A: uninstall  X: refresh  Y: show all  -: install  B: back";
    default:
        return "A: start  X: refresh  B: back   (games only; PC = PCFS copy)";
    }
}

static void draw_box(void)
{
    char line2[96], line3[96];

    if (g_stage == STAGE_CONFIRM) {
        if (g_screen == MGR_SCREEN_INSTALL) {
            const Package *p = &g_packages[selected_index()];

            snprintf(line2, sizeof(line2), "%016llX  v%u  %s", p->tid, p->version,
                     p->installed ? (p->installed_version == p->version ?
                                     "(reinstall)" : "(upgrade)") : "(new)");
            ui_box("Install this title?", package_name(p), line2, FALSE, 0, 0);
        } else {
            const McpTitle *t = &g_titles[selected_index()];
            const char *name = title_name(selected_index());

            snprintf(line2, sizeof(line2), "%016llX  v%u", t->titleId,
                     t->titleVersion);
            ui_box("Uninstall this title?", name ? name : "(no name)", line2,
                   FALSE, 0, 0);
        }
        ui_text_center((26.0f + 63.0f) * 0.5f, 17.8f, UI_TEXT_BAD,
                       "Hold L + R and press A.   B: cancel");
        return;
    }
    if (g_stage == STAGE_RUNNING) {
        if (g_job == JOB_INSTALL) {
            ui_box("Installing title, please wait...", g_job_label, g_job_text,
                   TRUE, g_job_done_bytes, g_job_total_bytes);
        } else {
            snprintf(line3, sizeof(line3), "%u s", ms_since(g_job_started) / 1000);
            ui_box("Removing title, please wait...", g_job_label, line3, FALSE,
                   0, 0);
        }
        return;
    }
    if (g_stage == STAGE_DONE) {
        ui_box(g_job_ok ? (g_job == JOB_INSTALL ? "Install finished" :
                                                  "Uninstall finished")
                        : (g_job == JOB_INSTALL ? "Install failed" :
                                                  "Uninstall failed"),
               g_job_label, g_job_text, FALSE, 0, 0);
        ui_text_center((26.0f + 63.0f) * 0.5f, 17.8f, UI_BOX_TEXT, "A: OK");
    }
}

void mgr_draw(BOOL focused)
{
    int count = g_view_count;
    int i;
    const char *info = NULL;

    if (g_cursor < g_scroll) {
        g_scroll = g_cursor;
    }
    if (g_cursor >= g_scroll + LIST_ROWS) {
        g_scroll = g_cursor - LIST_ROWS + 1;
    }

    ui_text(UI_TEXT_X, 1.0f, UI_TEXT, "%s", screen_title());

    if (!g_api_ok || !g_titles_ok ||
        (g_screen == MGR_SCREEN_INSTALL && !g_packages_ok)) {
        ui_text(UI_TEXT_X, LIST_Y, UI_TEXT_BAD, "%.80s", g_status);
        ui_text(UI_TEXT_X, LIST_Y + 1.5f, UI_TEXT_DIM, "X: try again");
        ui_text(UI_TEXT_X, UI_FOOTER_Y, UI_TEXT_DIM, "%s", footer_text(focused));
        return;
    }

    if (g_show_all) {
        ui_text(UI_TEXT_X, 2.2f, UI_TEXT_DIM, "%d %s (all)", count,
                g_screen == MGR_SCREEN_INSTALL ? "package folders" : "titles");
    } else {
        ui_text(UI_TEXT_X, 2.2f, UI_TEXT_DIM, "%d %s, %d hidden", count,
                g_screen == MGR_SCREEN_INSTALL ? "installable" :
                g_screen == MGR_SCREEN_LAUNCH ? "games" : "removable",
                g_hidden);
    }

    for (i = 0; i < LIST_ROWS && g_scroll + i < count; i++) {
        f32 y = LIST_Y + i * LIST_STEP;
        BOOL selected = (g_scroll + i == g_cursor);

        if (g_screen == MGR_SCREEN_INSTALL) {
            draw_package_row(y, g_view[g_scroll + i], selected, focused);
        } else {
            draw_title_row(y, g_view[g_scroll + i], selected, focused);
        }
    }
    if (count == 0) {
        ui_text(UI_TEXT_X, LIST_Y, UI_TEXT_DIM,
                g_screen == MGR_SCREEN_INSTALL ?
                "Nothing installable. Y shows every folder." :
                "Nothing to show. Y shows every title.");
    } else {
        if (g_screen == MGR_SCREEN_INSTALL) {
            const Package *p = &g_packages[selected_index()];
            info = install_block_reason(p);
            if (info == NULL) {
                ui_text(UI_TEXT_X, DETAIL_Y, UI_TEXT_DIM, "%016llX   folder %.40s",
                        p->tid, p->name);
            }
        } else {
            const McpTitle *t = &g_titles[selected_index()];
            ui_text(UI_TEXT_X, DETAIL_Y, UI_TEXT_DIM, "%016llX   type %08X   %.40s",
                    t->titleId, t->appType, t->path);
        }
        if (info != NULL) {
            ui_text(UI_TEXT_X, DETAIL_Y, UI_TEXT_WARN, "%s", info);
        }
    }
    ui_text(UI_TEXT_X, UI_FOOTER_Y, UI_TEXT_DIM, "%s", footer_text(focused));

    draw_box();
}
