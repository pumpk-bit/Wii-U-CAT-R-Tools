#include "ui.h"

#include <cafe/gx2ut.h>

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

const UiColor UI_BACKGROUND         = { 1.00f, 1.00f, 1.00f };
const UiColor UI_TAB_FILL           = { 0.56f, 0.61f, 0.85f };
const UiColor UI_TAB_BORDER         = { 0.36f, 0.40f, 0.62f };
const UiColor UI_TAB_TEXT           = { 0.80f, 0.82f, 0.86f };
const UiColor UI_TAB_SELECTED       = { 1.00f, 0.48f, 0.24f };
const UiColor UI_TAB_SELECTED_TEXT  = { 0.28f, 0.18f, 0.85f };
const UiColor UI_PANEL_BORDER       = { 0.55f, 0.56f, 0.60f };
const UiColor UI_TEXT               = { 0.08f, 0.20f, 0.22f };
const UiColor UI_TEXT_DIM           = { 0.45f, 0.50f, 0.54f };
const UiColor UI_TEXT_GOOD          = { 0.05f, 0.55f, 0.20f };
const UiColor UI_TEXT_BAD           = { 0.80f, 0.12f, 0.10f };
const UiColor UI_TEXT_WARN          = { 0.80f, 0.45f, 0.05f };
const UiColor UI_ROW_HIGHLIGHT      = { 1.00f, 0.48f, 0.24f };
const UiColor UI_ROW_HIGHLIGHT_IDLE = { 0.86f, 0.88f, 0.97f };
const UiColor UI_ROW_TEXT           = { 0.28f, 0.18f, 0.85f };
const UiColor UI_BOX_FILL           = { 0.80f, 0.95f, 1.00f };
const UiColor UI_BOX_BORDER         = { 0.45f, 0.58f, 0.95f };
const UiColor UI_BOX_TEXT           = { 0.10f, 0.20f, 0.70f };
const UiColor UI_BAR_TRACK          = { 1.00f, 1.00f, 1.00f };
const UiColor UI_BAR_BORDER         = { 0.55f, 0.60f, 0.78f };
const UiColor UI_BAR_FILL           = { 0.22f, 0.38f, 0.82f };

/* Average proportional glyph width in grid columns, for centring text. */
#define UI_AVG_CHAR_COLS 0.52f

static GX2ColorBuffer *s_target = NULL;

static void restore_font_state(void)
{
    DEMOFontSetContextState();
    DEMOFontSetTarget(s_target);
    DEMOFontSetGridSize(UI_GRID_X, UI_GRID_Y);
}

void ui_begin(GX2ColorBuffer *target)
{
    s_target = target;
    GX2ClearColor(target, UI_BACKGROUND.r, UI_BACKGROUND.g, UI_BACKGROUND.b,
                  1.0f);
    restore_font_state();
}

static s32 to_px_x(f32 col)
{
    return (s32)(col / UI_GRID_X * (f32)s_target->surface.width + 0.5f);
}

static s32 to_px_y(f32 line)
{
    return (s32)(line / UI_GRID_Y * (f32)s_target->surface.height + 0.5f);
}

static void rect_px(s32 left, s32 top, s32 right, s32 bottom, UiColor c)
{
    GX2UTRect r;
    s32 w = (s32)s_target->surface.width;
    s32 h = (s32)s_target->surface.height;

    r.left = left < 0 ? 0 : left;
    r.top = top < 0 ? 0 : top;
    r.right = right > w ? w : right;
    r.bottom = bottom > h ? h : bottom;
    if (r.right <= r.left || r.bottom <= r.top) {
        return;
    }
    GX2UTClearSurfaceRect(&s_target->surface, 0, 0, &r, s_target->auxPtr,
                          s_target->auxSize, c.r, c.g, c.b, 1.0f, NULL, 0.0f,
                          0, GX2_CLEAR_NONE, NULL);
    restore_font_state();
}

void ui_rect(f32 x0, f32 y0, f32 x1, f32 y1, UiColor c)
{
    rect_px(to_px_x(x0), to_px_y(y0), to_px_x(x1), to_px_y(y1), c);
}

void ui_frame(f32 x0, f32 y0, f32 x1, f32 y1, UiColor fill, UiColor border,
              int border_px)
{
    s32 l = to_px_x(x0), t = to_px_y(y0), r = to_px_x(x1), b = to_px_y(y1);

    if (border_px > 0) {
        rect_px(l, t, r, b, border);
    }
    rect_px(l + border_px, t + border_px, r - border_px, b - border_px, fill);
}

static void puts_color(f32 col, f32 line, UiColor c, const char *text)
{
    DEMOFontSetColor(c.r, c.g, c.b, 1.0f);
    DEMOFontPuts(col, line, text);
}

void ui_text(f32 col, f32 line, UiColor c, const char *fmt, ...)
{
    char text[256];
    va_list args;

    va_start(args, fmt);
    vsnprintf(text, sizeof(text), fmt, args);
    va_end(args);
    puts_color(col, line, c, text);
}

void ui_text_center(f32 center_col, f32 line, UiColor c, const char *fmt, ...)
{
    char text[256];
    va_list args;

    va_start(args, fmt);
    vsnprintf(text, sizeof(text), fmt, args);
    va_end(args);
    puts_color(center_col - (f32)strlen(text) * UI_AVG_CHAR_COLS * 0.5f, line,
               c, text);
}

void ui_bar(f32 x0, f32 y0, f32 x1, f32 y1, u64 done, u64 total)
{
    s32 l = to_px_x(x0), t = to_px_y(y0), r = to_px_x(x1), b = to_px_y(y1);
    s32 inner_l = l + 2, inner_r = r - 2;

    rect_px(l, t, r, b, UI_BAR_BORDER);
    rect_px(inner_l, t + 2, inner_r, b - 2, UI_BAR_TRACK);
    if (total > 0 && done > 0) {
        u64 capped = done > total ? total : done;
        s32 fill = (s32)((u64)(inner_r - inner_l) * capped / total);

        if (fill < 4) {
            fill = 4;   /* show that something started, as the original does */
        }
        rect_px(inner_l, t + 2, inner_l + fill, b - 2, UI_BAR_FILL);
    }
}

void ui_box(const char *title, const char *line2, const char *line3,
            BOOL show_bar, u64 done, u64 total)
{
    const f32 x0 = 26.0f, x1 = 63.0f, y0 = 10.0f, y1 = 19.5f;
    const f32 center = (x0 + x1) * 0.5f;
    f32 line = y0 + 1.6f;

    ui_frame(x0, y0, x1, y1, UI_BOX_FILL, UI_BOX_BORDER, 2);
    ui_text_center(center, line, UI_BOX_TEXT, "%s", title);
    line += 2.0f;
    if (show_bar) {
        ui_bar(x0 + 3.0f, line + 0.25f, x1 - 3.0f, line + 0.85f, done, total);
        line += 1.8f;
        if (total > 0) {
            ui_text_center(center, line, UI_BOX_TEXT, "%llu / %llu",
                           done > total ? total : done, total);
        }
        line += 1.6f;
    }
    if (line2 != NULL && line2[0] != '\0') {
        ui_text_center(center, line, UI_BOX_TEXT, "%s", line2);
        line += 1.3f;
    }
    if (line3 != NULL && line3[0] != '\0') {
        ui_text_center(center, line, UI_BOX_TEXT, "%s", line3);
    }
}
