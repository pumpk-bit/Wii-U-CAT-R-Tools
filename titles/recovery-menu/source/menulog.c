#include "menulog.h"

#include <cafe/os.h>

#include <stdarg.h>
#include <stdio.h>

static char g_lines[MENU_LOG_LINES][MENU_LOG_WIDTH];
static int g_next = 0;     /* slot the next line goes into */
static int g_count = 0;

void menu_log(const char *fmt, ...)
{
    char text[MENU_LOG_WIDTH];
    OSCalendarTime ct;
    va_list args;

    va_start(args, fmt);
    vsnprintf(text, sizeof(text), fmt, args);
    va_end(args);

    OSReport("CAT Recovery Menu: %s\n", text);

    OSTicksToCalendarTime(OSGetTime(), &ct);
    snprintf(g_lines[g_next], MENU_LOG_WIDTH, "[%02d:%02d:%02d] %s",
             ct.hour, ct.min, ct.sec, text);
    g_next = (g_next + 1) % MENU_LOG_LINES;
    if (g_count < MENU_LOG_LINES) {
        g_count++;
    }
}

int menu_log_count(void)
{
    return g_count;
}

const char *menu_log_line(int i)
{
    int oldest = (g_next - g_count + MENU_LOG_LINES) % MENU_LOG_LINES;

    if (i < 0 || i >= g_count) {
        return "";
    }
    return g_lines[(oldest + i) % MENU_LOG_LINES];
}
