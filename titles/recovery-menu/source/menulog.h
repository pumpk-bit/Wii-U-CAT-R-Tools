#ifndef CAT_RECOVERY_MENULOG_H
#define CAT_RECOVERY_MENULOG_H

/*
 * The menu's own log: every line goes to OSReport (USB serial, read by catr)
 * as "CAT Recovery Menu: <text>" and into a ring shown on the Logs tab.
 */

#define MENU_LOG_LINES 128
#define MENU_LOG_WIDTH 100

void menu_log(const char *fmt, ...);

/* Lines kept so far (at most MENU_LOG_LINES). */
int menu_log_count(void);

/* Line i, 0 being the oldest kept; "[hh:mm:ss] text". */
const char *menu_log_line(int i);

#endif
