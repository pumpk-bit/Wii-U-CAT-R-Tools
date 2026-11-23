#ifndef CAT_RECOVERY_UI_H
#define CAT_RECOVERY_UI_H

#include <cafe/demo.h>

/*
 * Recovery-menu style drawing on top of the DEMO font.
 *
 * Everything is placed on one grid of UI_GRID_X columns by UI_GRID_Y lines
 * covering the whole target, so the TV (1280x720) and the GamePad (854x480)
 * get the same layout. Text at line L fills grid rows [L, L+1].
 *
 * Filled rectangles are GX2UTClearSurfaceRect calls (the same path as
 * GX2ClearColor). Each one leaves state shadowing off, so the font context
 * and target are restored after every rectangle.
 */

#define UI_GRID_X 72.0f
#define UI_GRID_Y 30.0f

typedef struct
{
    f32 r, g, b;
} UiColor;

/* Palette, matched by eye to the console's own recovery menu. */
extern const UiColor UI_BACKGROUND;
extern const UiColor UI_TAB_FILL;
extern const UiColor UI_TAB_BORDER;
extern const UiColor UI_TAB_TEXT;
extern const UiColor UI_TAB_SELECTED;
extern const UiColor UI_TAB_SELECTED_TEXT;
extern const UiColor UI_PANEL_BORDER;
extern const UiColor UI_TEXT;
extern const UiColor UI_TEXT_DIM;
extern const UiColor UI_TEXT_GOOD;
extern const UiColor UI_TEXT_BAD;
extern const UiColor UI_TEXT_WARN;
extern const UiColor UI_ROW_HIGHLIGHT;
extern const UiColor UI_ROW_HIGHLIGHT_IDLE;
extern const UiColor UI_ROW_TEXT;
extern const UiColor UI_BOX_FILL;
extern const UiColor UI_BOX_BORDER;
extern const UiColor UI_BOX_TEXT;
extern const UiColor UI_BAR_TRACK;
extern const UiColor UI_BAR_BORDER;
extern const UiColor UI_BAR_FILL;

/* Panel area (right of the tab column), in grid units. */
#define UI_PANEL_X0 17.6f
#define UI_PANEL_Y0 0.4f
#define UI_PANEL_X1 71.6f
#define UI_PANEL_Y1 29.6f
#define UI_TEXT_X   (UI_PANEL_X0 + 1.4f)
#define UI_FOOTER_Y 27.6f

/* Starts a frame on a target: clears it to the background colour. */
void ui_begin(GX2ColorBuffer *target);

void ui_rect(f32 x0, f32 y0, f32 x1, f32 y1, UiColor c);

/* Filled rectangle with a border of border_px pixels drawn inside it. */
void ui_frame(f32 x0, f32 y0, f32 x1, f32 y1, UiColor fill, UiColor border,
              int border_px);

void ui_text(f32 col, f32 line, UiColor c, const char *fmt, ...);
void ui_text_center(f32 center_col, f32 line, UiColor c, const char *fmt, ...);

/* Progress bar; total 0 draws an empty track. */
void ui_bar(f32 x0, f32 y0, f32 x1, f32 y1, u64 done, u64 total);

/*
 * The light-cyan box over the panel. line2/line3 may be NULL. With show_bar
 * a progress bar and "done / total" are drawn under the title.
 */
void ui_box(const char *title, const char *line2, const char *line3,
            BOOL show_bar, u64 done, u64 total);

#endif
