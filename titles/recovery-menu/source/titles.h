#ifndef CAT_RECOVERY_TITLES_H
#define CAT_RECOVERY_TITLES_H

#include <cafe/demo.h>
#include <cafe/vpad.h>

/*
 * Title manager behind the Install and Launch tabs: install from SD
 * (/install/<dir>), guarded uninstall and the launch list, through the same
 * MCP and FSA calls System Config Tool uses. All MCP/FSA entry points are
 * resolved from coreinit.rpl at runtime, since the Cafe SDK headers do not
 * declare them.
 */

typedef enum
{
    MGR_SCREEN_NONE,
    MGR_SCREEN_INSTALL,     /* packages in sd:/install */
    MGR_SCREEN_TITLES,      /* installed titles, uninstall */
    MGR_SCREEN_LAUNCH       /* installed titles, launch */
} MgrScreen;

typedef struct
{
    u32 total;
    u32 mlc;
    u32 usb;
    u32 slc;
    u32 pcfs;       /* PC copies listed while PCFS is mounted (hfio device) */
} MgrCounts;

void mgr_init(void);
void mgr_shutdown(void);

/* Switches the panel to a screen and refreshes its list. FALSE (with the
 * reason in the status line) if MCP or the SD card is unavailable; the
 * screen is still shown, with that reason. */
BOOL mgr_open(MgrScreen screen);

MgrScreen mgr_screen(void);

/* TRUE while an install or uninstall is running (no launch, no tab change). */
BOOL mgr_busy(void);

/* TRUE while the confirm / progress / result box is up; it owns input. */
BOOL mgr_box_up(void);

void mgr_poll(void);

/* Panel input. Returns a title ID to launch (Launch screen, A), else 0. */
u64 mgr_input(const VPADStatus *status);

/* Name for a title ID if known, else NULL. */
const char *mgr_title_name(u64 tid);

/* Draws the current screen in the panel; focused = panel owns input. */
void mgr_draw(BOOL focused);

/* Refreshes the title list and counts it by device. */
BOOL mgr_counts(MgrCounts *out);

/* Last status line of the manager. */
const char *mgr_status_line(void);

#endif
