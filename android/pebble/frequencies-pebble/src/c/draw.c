// Drawing helpers: theme, icons on a 40-unit grid, edge icons, hold rings, pills, dial.
#include "freq.h"

Theme T;

int min_i(int a, int b) { return a < b ? a : b; }
uint32_t now_ms(void) {
  time_t s; uint16_t ms;
  time_ms(&s, &ms);
  return (uint32_t)s * 1000 + ms;
}
int max_i(int a, int b) { return a > b ? a : b; }

static uint32_t s_input_ms;
void input_seen(void) { s_input_ms = now_ms(); }
// The playing bars run at 15 fps, and drop to 2 fps after 10 s without a press or touch (user, round 2).
uint32_t bars_ms(void) { return now_ms() - s_input_ms < 10000 ? 66 : 500; }

bool is_large(void) {
  GRect b = layer_get_bounds(window_get_root_layer(window_stack_get_top_window()));
  return b.size.w >= 180;
}

GColor gc8(uint8_t argb) { return (GColor){ .argb = argb }; }

// Outrun on the phone, snapped to Pebble's 64 colors (spec section 4).
static const uint8_t OUTRUN[9] = {
  0xC0 /*Black*/, 0xFF /*White*/, 0xFB /*RichBrilliantLavender*/, 0xE6 /*Purpureus*/,
  0xF8 /*ChromeYellow*/, 0xDF /*ElectricBlue*/, 0xF1 /*Folly*/, 0xC1 /*OxfordBlue*/, 0xDD /*ScreaminGreen*/,
};

void theme_apply_bytes(const uint8_t *b, int n) {
  uint8_t c[9];
  memcpy(c, OUTRUN, 9);
  if (b) memcpy(c, b, n < 9 ? n : 9);
#ifdef PBL_COLOR
  T.bg = gc8(c[0]); T.text = gc8(c[1]); T.text2 = gc8(c[2]); T.text3 = gc8(c[3]); T.accent = gc8(c[4]);
  T.playing = gc8(c[5]); T.down = gc8(c[6]); T.stripe = gc8(c[7]); T.good = gc8(c[8]);
#else
  T.bg = GColorBlack; T.text = T.text2 = T.text3 = T.accent = T.playing = T.down = T.good = GColorWhite;
  T.stripe = GColorBlack;
#endif
}

void theme_load(void) {
  uint8_t b[9];
  if (persist_exists(PK_THEME) && persist_read_data(PK_THEME, b, 9) == 9) theme_apply_bytes(b, 9);
  else theme_apply_bytes(NULL, 0);
}

// ---- icons ----
enum { OP_END, OP_L, OP_C, OP_F, OP_P, OP_O, OP_R, OP_A, OP_Z };  // Z: closed outline
#define END OP_END

static const uint8_t I_PLAY[] = { OP_P, 3, 12, 7, 33, 20, 12, 33, END };
static const uint8_t I_PAUSE[] = { OP_R, 10, 8, 7, 24, OP_R, 23, 8, 7, 24, END };
static const uint8_t I_PREV[] = { OP_P, 3, 31, 8, 14, 20, 31, 32, OP_R, 8, 8, 5, 24, END };
static const uint8_t I_NEXT[] = { OP_P, 3, 9, 8, 26, 20, 9, 32, OP_R, 27, 8, 5, 24, END };
#define STAR_PTS 10, 20, 3, 25, 14, 37, 15, 28, 23, 31, 35, 20, 29, 9, 35, 12, 23, 3, 15, 15, 14
static const uint8_t I_STAR[] = { OP_P, STAR_PTS, END };
static const uint8_t I_STAR_O[] = { OP_Z, STAR_PTS, END };
static const uint8_t I_GLASS[] = { OP_L, 10, 5, 30, 5, OP_L, 10, 35, 30, 35, OP_O, 5, 12, 5, 12, 12, 19, 20, 12, 28, 12, 35,
  OP_O, 5, 28, 5, 28, 12, 21, 20, 28, 28, 28, 35, OP_P, 3, 14, 34, 20, 27, 26, 34, OP_P, 3, 16, 11, 24, 11, 20, 17, END };
#define THUMB_DOWN_PTS 10, 13, 23, 19, 33, 23, 33, 24, 30, 23, 25, 32, 25, 35, 22, 32, 9, 29, 7, 13, 7
#define THUMB_UP_PTS 10, 13, 17, 19, 7, 23, 7, 24, 10, 23, 15, 32, 15, 35, 18, 32, 31, 29, 33, 13, 33
static const uint8_t I_THUMB_DOWN[] = { OP_Z, THUMB_DOWN_PTS, OP_Z, 4, 6, 7, 13, 7, 13, 23, 6, 23, END };
static const uint8_t I_THUMB_UP[] = { OP_Z, THUMB_UP_PTS, OP_Z, 4, 6, 33, 13, 33, 13, 17, 6, 17, END };
static const uint8_t I_THUMB_DOWN_F[] = { OP_P, THUMB_DOWN_PTS, OP_R, 5, 7, 8, 17, END };
static const uint8_t I_THUMB_UP_F[] = { OP_P, THUMB_UP_PTS, OP_R, 5, 16, 8, 17, END };
static const uint8_t I_SONGID[] = { OP_C, 20, 20, 15, OP_O, 10, 26, 14, 22, 12, 17, 12, 14, 15, 17, 19, 23, 21, 26, 24, 23, 28, 17, 28, 14, 25, END };
static const uint8_t I_MOON[] = { OP_A, 19, 21, 14, 30, 165, OP_A, 27, 13, 11, 95, 160, END };
static const uint8_t I_SHUFFLE[] = { OP_O, 6, 5, 12, 11, 12, 16, 14, 24, 26, 29, 28, 35, 28, OP_O, 6, 5, 28, 11, 28, 16, 26, 24, 14, 29, 12, 35, 12,
  OP_O, 3, 31, 8, 35, 12, 31, 16, OP_O, 3, 31, 24, 35, 28, 31, 32, END };
static const uint8_t I_LIST[] = { OP_L, 14, 10, 34, 10, OP_L, 14, 20, 34, 20, OP_L, 14, 30, 34, 30, OP_R, 5, 8, 5, 5, OP_R, 5, 18, 5, 5, OP_R, 5, 28, 5, 5, END };
static const uint8_t I_LYRICS[] = { OP_L, 6, 10, 26, 10, OP_L, 6, 18, 26, 18, OP_L, 6, 26, 16, 26, OP_L, 30, 31, 30, 13, OP_L, 30, 13, 36, 11, OP_F, 27, 31, 4, END };
static const uint8_t I_PERSON[] = { OP_C, 20, 13, 7, OP_O, 7, 7, 35, 9, 27, 14, 23, 20, 22, 26, 23, 31, 27, 33, 35, END };
static const uint8_t I_DEADPHONE[] = { OP_Z, 8, 12, 4, 28, 4, 31, 7, 31, 33, 28, 36, 12, 36, 9, 33, 9, 7,
  OP_L, 13, 10, 18, 15, OP_L, 18, 10, 13, 15, OP_L, 22, 10, 27, 15, OP_L, 27, 10, 22, 15, OP_O, 4, 14, 27, 17, 24, 23, 24, 26, 27, END };
static const uint8_t I_RADIO[] = { OP_Z, 4, 6, 15, 34, 15, 34, 33, 6, 33, OP_L, 10, 15, 28, 6, OP_C, 25, 24, 4, OP_R, 10, 20, 7, 2, OP_R, 10, 25, 7, 2, END };
static const uint8_t I_CHECK[] = { OP_O, 3, 8, 21, 16, 29, 32, 11, END };
static const uint8_t I_NOTE[] = { OP_O, 4, 16, 30, 16, 9, 32, 6, 32, 26, OP_F, 12, 30, 4, OP_F, 28, 27, 4, END };
static const uint8_t I_TAG[] = { OP_Z, 5, 6, 6, 20, 6, 34, 20, 20, 34, 6, 20, OP_F, 13, 13, 2, END };
static const uint8_t I_PLUS[] = { OP_L, 20, 7, 20, 33, OP_L, 7, 20, 33, 20, END };
static const uint8_t I_CLOCK[] = { OP_C, 20, 20, 14, OP_O, 3, 20, 12, 20, 20, 26, 24, END };
static const uint8_t I_GEAR[] = { OP_C, 20, 20, 6, OP_L, 20, 4, 20, 9, OP_L, 20, 31, 20, 36, OP_L, 4, 20, 9, 20, OP_L, 31, 20, 36, 20,
  OP_L, 9, 9, 12, 12, OP_L, 28, 28, 31, 31, OP_L, 9, 31, 12, 28, OP_L, 28, 12, 31, 9, END };
static const uint8_t I_MIC[] = { OP_Z, 8, 17, 5, 23, 5, 25, 8, 25, 20, 23, 24, 17, 24, 15, 20, 15, 8,
  OP_O, 7, 9, 19, 10, 25, 14, 29, 20, 30, 26, 29, 30, 25, 31, 19, OP_L, 20, 30, 20, 35, END };
static const uint8_t I_DOTS[] = { OP_F, 8, 20, 4, OP_F, 20, 20, 4, OP_F, 32, 20, 4, END };
static const uint8_t I_DICE[] = { OP_Z, 4, 8, 8, 32, 8, 32, 32, 8, 32, OP_F, 14, 14, 2, OP_F, 26, 26, 2, OP_F, 20, 20, 2, END };
static const uint8_t I_SORT[] = { OP_L, 6, 10, 34, 10, OP_L, 6, 20, 26, 20, OP_L, 6, 30, 18, 30, END };
static const uint8_t I_UP[] = { OP_L, 20, 33, 20, 7, OP_O, 3, 9, 18, 20, 7, 31, 18, END };
static const uint8_t I_DOWN[] = { OP_L, 20, 7, 20, 33, OP_O, 3, 9, 22, 20, 33, 31, 22, END };
static const uint8_t I_BANDAID[] = { OP_Z, 6, 13, 36, 36, 13, 33, 5, 27, 4, 4, 27, 5, 33, OP_P, 4, 20, 13, 27, 20, 20, 27, 13, 20, END };
static const uint8_t I_NO[] = { OP_C, 20, 20, 14, OP_L, 10, 30, 30, 10, END };
static const uint8_t I_MINUS[] = { OP_L, 8, 20, 32, 20, END };
static const uint8_t I_SEARCH[] = { OP_C, 17, 17, 11, OP_L, 25, 25, 34, 34, END };
static const uint8_t I_PHONE[] = { OP_Z, 8, 12, 5, 28, 5, 30, 7, 30, 33, 28, 35, 12, 35, 10, 33, 10, 7, OP_L, 17, 30, 23, 30, END };
static const uint8_t I_FREQ[] = { OP_A, 20, 20, 6, 115, 155, OP_A, 20, 20, 6, 25, 65, OP_A, 20, 20, 11, 115, 155, OP_A, 20, 20, 11, 25, 65,
  OP_A, 20, 20, 16, 115, 155, OP_A, 20, 20, 16, 25, 65, OP_F, 20, 20, 3, END };
static const uint8_t I_VOLUME[] = { OP_P, 6, 4, 15, 12, 15, 21, 7, 21, 33, 12, 25, 4, 25, OP_A, 23, 20, 7, 23, 67, OP_A, 23, 20, 13, 23, 67, END };
// a paintbrush, handle up to the right (user, 2026-11-10: Watch theme)
static const uint8_t I_BRUSH[] = { OP_L, 34, 6, 24, 16, OP_L, 31, 4, 21, 14, OP_P, 4, 19, 15, 25, 21, 22, 24, 16, 18,
  OP_P, 6, 16, 18, 22, 24, 19, 31, 10, 36, 4, 36, 7, 25, END };
// equalizer: three sliders (station settings, 2026-11-10)
static const uint8_t I_EQ[] = { OP_L, 10, 5, 10, 35, OP_L, 20, 5, 20, 35, OP_L, 30, 5, 30, 35, OP_R, 6, 22, 9, 5, OP_R, 16, 10, 9, 5, OP_R, 26, 17, 9, 5, END };
static const uint8_t I_PALETTE[] = { OP_C, 20, 20, 14, OP_F, 14, 15, 3, OP_F, 22, 12, 3, OP_F, 28, 19, 3, OP_F, 15, 25, 3, END };

static const uint8_t *const ICONS[IC_COUNT] = {
  I_PLAY, I_PAUSE, I_PREV, I_NEXT, I_STAR, I_STAR_O, I_GLASS, I_THUMB_DOWN, I_THUMB_UP,
  I_SONGID, I_MOON, I_SHUFFLE, I_LIST, I_LYRICS, I_PERSON, I_DEADPHONE, I_RADIO, I_CHECK,
  I_NOTE, I_TAG, I_PLUS, I_CLOCK, I_GEAR, I_MIC, I_DOTS, I_DICE, I_SORT, I_UP, I_DOWN,
  I_BANDAID, I_NO, I_MINUS, I_SEARCH, I_PHONE, I_FREQ, I_THUMB_DOWN_F, I_THUMB_UP_F, I_PALETTE, I_VOLUME, I_BRUSH, I_EQ,
};

void draw_icon(GContext *ctx, int icon, GRect r, GColor c) {
  if (icon < 0 || icon >= IC_COUNT) return;
  const uint8_t *p = ICONS[icon];
  int s = min_i(r.size.w, r.size.h);
  int ox = r.origin.x + (r.size.w - s) / 2, oy = r.origin.y + (r.size.h - s) / 2;
#define X(v) (ox + ((v) * s + 20) / 40)
#define Y(v) (oy + ((v) * s + 20) / 40)
#define S(v) (((v) * s + 20) / 40)
  graphics_context_set_stroke_color(ctx, c);
  graphics_context_set_fill_color(ctx, c);
  graphics_context_set_stroke_width(ctx, s >= 26 ? 3 : s >= 13 ? 2 : 1);
  // crisp pixels: antialiasing smudged the small icons (user, round 2)
  graphics_context_set_antialiased(ctx, false);
  while (*p != OP_END) {
    uint8_t op = *p++;
    switch (op) {
      case OP_L: graphics_draw_line(ctx, GPoint(X(p[0]), Y(p[1])), GPoint(X(p[2]), Y(p[3]))); p += 4; break;
      case OP_C: graphics_draw_circle(ctx, GPoint(X(p[0]), Y(p[1])), S(p[2])); p += 3; break;
      case OP_F: graphics_fill_circle(ctx, GPoint(X(p[0]), Y(p[1])), max_i(1, S(p[2]))); p += 3; break;
      case OP_R: graphics_fill_rect(ctx, GRect(X(p[0]), Y(p[1]), max_i(1, S(p[2])), max_i(1, S(p[3]))), 0, GCornerNone); p += 4; break;
      case OP_A: {
        int rr = S(p[2]);
        GRect box = GRect(X(p[0]) - rr, Y(p[1]) - rr, rr * 2, rr * 2);
        graphics_draw_arc(ctx, box, GOvalScaleModeFitCircle, DEG_TO_TRIGANGLE(p[3] * 2), DEG_TO_TRIGANGLE(p[4] * 2));
        p += 5;
        break;
      }
      case OP_P: case OP_O: case OP_Z: {
        int n = *p++;
        GPoint pts[12];
        for (int i = 0; i < n && i < 12; i++) pts[i] = GPoint(X(p[i * 2]), Y(p[i * 2 + 1]));
        GPathInfo info = { .num_points = (uint32_t)n, .points = pts };
        GPath path = { .num_points = info.num_points, .points = pts, .rotation = 0, .offset = GPointZero };
        if (op == OP_P) { gpath_draw_filled(ctx, &path); gpath_draw_outline(ctx, &path); }
        else if (op == OP_Z) gpath_draw_outline(ctx, &path);
        else gpath_draw_outline_open(ctx, &path);
        p += n * 2;
        break;
      }
      default: return;
    }
  }
#undef X
#undef Y
#undef S
}

// Shuffle as on the desktop (user, 2026-11-10): two arrows, parallel when off (f = 0) and crossed when on (f = 1000).
// The same drawing in between, so the button is seen changing its mind. Desktop shell.py shuffle_N, on its 24 grid.
void draw_shuffle(GContext *ctx, GRect r, int f, GColor c) {
  int s = min_i(r.size.w, r.size.h);
  int ox = r.origin.x + (r.size.w - s) / 2, oy = r.origin.y + (r.size.h - s) / 2;
#define X(m) (ox + ((m) * s + 12000) / 24000)  // m in thousandths of the desktop's 24-unit grid
#define Y(m) (oy + ((m) * s + 12000) / 24000)
  graphics_context_set_stroke_color(ctx, c);
  graphics_context_set_fill_color(ctx, c);
  graphics_context_set_stroke_width(ctx, s >= 26 ? 3 : s >= 13 ? 2 : 1);
  graphics_context_set_antialiased(ctx, false);
  int ends[2] = { 8000 + 8 * f, 16000 - 8 * f };  // the paths start level and swap ends
  int starts[2] = { 8000, 16000 };
  for (int i = 0; i < 2; i++) {
    GPoint pts[4] = { GPoint(X(3000), Y(starts[i])), GPoint(X(8000), Y(starts[i])),
                      GPoint(X(15000), Y(ends[i])), GPoint(X(17500), Y(ends[i])) };
    GPath line = { .num_points = 4, .points = pts };
    gpath_draw_outline_open(ctx, &line);
    GPoint tri[3] = { GPoint(X(17000), Y(ends[i] - 3500)), GPoint(X(21500), Y(ends[i])), GPoint(X(17000), Y(ends[i] + 3500)) };
    GPath head = { .num_points = 3, .points = tri };
    gpath_draw_filled(ctx, &head);
    graphics_context_set_stroke_width(ctx, 1);  // a thin edge keeps the heads sharp
    gpath_draw_outline(ctx, &head);
    graphics_context_set_stroke_width(ctx, s >= 26 ? 3 : s >= 13 ? 2 : 1);
  }
#undef X
#undef Y
}

void draw_text(GContext *ctx, const char *s, GFont f, GRect r, GTextAlignment a, GColor c) {
  graphics_context_set_text_color(ctx, c);
  graphics_draw_text(ctx, s, f, r, GTextOverflowModeTrailingEllipsis, a, NULL);
}

int text_h(const char *s, GFont f, int w) {
  return graphics_text_layout_get_content_size(s, f, GRect(0, 0, w, 2000), GTextOverflowModeWordWrap, GTextAlignmentLeft).h;
}

const char *fmt_time(time_t t, char *buf, int n) {
  struct tm *tm = localtime(&t);
  if (clock_is_24h_style()) strftime(buf, n, "%H:%M", tm);
  else {
    strftime(buf, n, "%I:%M", tm);
    if (buf[0] == '0') memmove(buf, buf + 1, strlen(buf));
  }
  return buf;
}

// Every clock in the app is the same size: Gothic 24 Bold (18 Bold on the small screens). `bold` is kept for callers.
GFont clock_font(void) { return fonts_get_system_font(is_large() ? FONT_KEY_GOTHIC_24_BOLD : FONT_KEY_GOTHIC_18_BOLD); }
void draw_clock(GContext *ctx, GRect r, GTextAlignment align, GColor c, bool bold) {
  (void)bold;
  char buf[8];
  fmt_time(time(NULL), buf, sizeof buf);
  draw_text(ctx, buf, clock_font(), r, align, c);
}

void draw_dotted_hline(GContext *ctx, int x0, int x1, int y, GColor c) {
  graphics_context_set_fill_color(ctx, c);
  for (int x = x0; x < x1; x += 3) graphics_fill_rect(ctx, GRect(x, y, 1, 1), 0, GCornerNone);
}

// Title in the accent at the left, an extra note beside it, the clock at the right (user, 2026-11-09).
void draw_header(GContext *ctx, GRect b, const char *title, const char *extra) {
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, GRect(0, 0, b.size.w, HEADER_H), 0, GCornerNone);
  bool big = b.size.w >= 180;
  GFont tf = fonts_get_system_font(big ? FONT_KEY_GOTHIC_24_BOLD : FONT_KEY_GOTHIC_18_BOLD);
  int cw = big ? 60 : 46;
  int tw = graphics_text_layout_get_content_size(title, tf, GRect(0, 0, b.size.w - cw - 8, 30),
                                                 GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft).w;
  draw_text(ctx, title, tf, GRect(6, big ? -3 : 1, b.size.w - cw - 8, 30), GTextAlignmentLeft, T.accent);
  if (extra && extra[0]) {
    draw_text(ctx, extra, fonts_get_system_font(FONT_KEY_GOTHIC_18), GRect(6 + tw + 6, big ? 2 : 2, b.size.w - cw - tw - 14, 22),
              GTextAlignmentLeft, T.text2);
  }
  draw_clock(ctx, GRect(b.size.w - cw - 4, big ? -3 : 1, cw, 28), GTextAlignmentRight, T.text, false);
  graphics_context_set_fill_color(ctx, T.stripe);
  graphics_fill_rect(ctx, GRect(0, HEADER_H - 2, b.size.w, 2), 0, GCornerNone);
}

GRect edge_icon_rect(GRect b, int slot) {
  int s = b.size.w >= 180 ? 18 : 14;
  static const int ys[3] = { 22, 50, 78 };
  int y = b.size.h * ys[slot] / 100 - s / 2;
  return GRect(b.size.w - s - 3, y, s, s);
}

// Slim button hints on the right edge, no bar behind them (user, 2026-11-09).
void draw_edge_icons(GContext *ctx, GRect b, const int icons[3], const GColor *cols) {
  for (int i = 0; i < 3; i++) {
    if (icons[i] < 0) continue;
    draw_icon(ctx, icons[i], edge_icon_rect(b, i), cols ? cols[i] : T.text2);
  }
}

// While a button is held, its edge icon becomes the hold action's icon in a filling ring (HOLD_MS, 0.7 s).
void draw_hold_ring(GContext *ctx, GRect b, int slot, int icon, GColor c, int permille) {
  GRect r = edge_icon_rect(b, slot);
  int rad = r.size.w / 2 + 4;
  GPoint ctr = GPoint(r.origin.x + r.size.w / 2 - 2, r.origin.y + r.size.h / 2);
  GRect box = GRect(ctr.x - rad, ctr.y - rad, rad * 2, rad * 2);
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_circle(ctx, ctr, rad + 2);
  graphics_context_set_stroke_color(ctx, T.text3);
  graphics_context_set_stroke_width(ctx, 1);
  graphics_draw_circle(ctx, ctr, rad);
  graphics_context_set_stroke_color(ctx, c);
  graphics_context_set_stroke_width(ctx, 2);
  int ang = permille >= 1000 ? 360 : permille * 360 / 1000;
  if (ang > 0) graphics_draw_arc(ctx, box, GOvalScaleModeFitCircle, 0, DEG_TO_TRIGANGLE(ang));
  draw_icon(ctx, icon, GRect(r.origin.x - 2, r.origin.y, r.size.w, r.size.h), c);
}

// The app's three playing bars.
// Heights follow the clock, not the frame count, so they move smoothly at 15 fps and still make sense at 2 fps.
void draw_bars(GContext *ctx, GRect r, GColor c, int phase) {
  (void)phase;
  static const uint16_t period[3] = { 740, 560, 910 };
  static const uint16_t offset[3] = { 0, 21000, 43000 };
  uint32_t t = now_ms();
  int bw = max_i(2, r.size.h / 5);
  graphics_context_set_fill_color(ctx, c);
  for (int i = 0; i < 3; i++) {
    int32_t a = (int32_t)((t % period[i]) * TRIG_MAX_ANGLE / period[i]) + offset[i];
    int pct = 58 + 40 * sin_lookup(a) / TRIG_MAX_RATIO;
    int h = max_i(2, r.size.h * pct / 100);
    graphics_fill_rect(ctx, GRect(r.origin.x + i * bw * 2, r.origin.y + r.size.h - h, bw, h), 0, GCornerNone);
  }
}

// The dial at the bottom: ticks, and the needle at this frequency's place in the list.
void draw_dial(GContext *ctx, GRect b, int y, int x0, int x1, int pos, int count) {
  graphics_context_set_fill_color(ctx, T.text3);
  for (int x = x0; x <= x1; x += 4) {
    bool tall = ((x - x0) % 20) == 0;
    graphics_fill_rect(ctx, GRect(x, y - (tall ? 4 : 2), 1, tall ? 4 : 2), 0, GCornerNone);
  }
  if (count > 0 && pos >= 0) {
    int nx = x0 + (x1 - x0) * pos / max_i(1, count - 1);
    bool big = b.size.w >= 180;
    graphics_context_set_fill_color(ctx, T.accent);
    graphics_fill_rect(ctx, GRect(nx, y - (big ? 10 : 8), 2, big ? 12 : 9), 0, GCornerNone);
  }
}

static GColor ink_on(GColor bg) {
#ifdef PBL_COLOR
  int r = (bg.argb >> 4) & 3, g = (bg.argb >> 2) & 3, b = bg.argb & 3;
  return (r * 3 + g * 6 + b) >= 18 ? GColorBlack : GColorWhite;
#else
  return gcolor_equal(bg, GColorWhite) ? GColorBlack : GColorWhite;
#endif
}

// A desktop-style tag pill: filled = shown, outlined = off, struck = hidden. Returns its width.
int draw_pill(GContext *ctx, int x, int y, const Tag *t, int maxw, bool small) {
  GFont f = fonts_get_system_font(small ? FONT_KEY_GOTHIC_14_BOLD : FONT_KEY_GOTHIC_18_BOLD);
  int h = small ? 16 : 22;
  GSize ts = graphics_text_layout_get_content_size(t->name, f, GRect(0, 0, maxw - 8, h), GTextOverflowModeTrailingEllipsis,
                                                   GTextAlignmentLeft);
  int w = min_i(maxw, ts.w + (small ? 8 : 12));
  if (w < 16) return 0;
  GColor col = PBL_IF_COLOR_ELSE(gc8(t->color ? t->color : 0xFF), GColorWhite);
  GRect r = GRect(x, y, w, h);
  GColor ink;
  if (t->state == 0) {
    graphics_context_set_stroke_color(ctx, col);
    graphics_context_set_stroke_width(ctx, 2);
    graphics_draw_round_rect(ctx, GRect(x + 1, y + 1, w - 2, h - 2), small ? 4 : 6);
    ink = col;
  } else {
    graphics_context_set_fill_color(ctx, col);
    graphics_fill_rect(ctx, r, small ? 4 : 6, GCornersAll);
    ink = ink_on(col);
  }
  draw_text(ctx, t->name, f, GRect(x + (small ? 4 : 6), y + (small ? -1 : -2), w - (small ? 8 : 12), h), GTextAlignmentLeft, ink);
  if (t->state == 2) {
    graphics_context_set_stroke_color(ctx, ink);
    graphics_context_set_stroke_width(ctx, 2);
    graphics_draw_line(ctx, GPoint(x + 4, y + h / 2 + 1), GPoint(x + w - 5, y + h / 2 + 1));
  }
  return w;
}

// A smaller copy of a color picture: each new pixel averages the pixels it covers, then Floyd–Steinberg
// spreads the rounding error, so it doesn't sparkle the way skipping pixels does (user, round 2). Color only.
GBitmap *shrink_bitmap(GBitmap *src, int size) {
#ifdef PBL_COLOR
  if (!src || gbitmap_get_format(src) != GBitmapFormat8Bit) return NULL;
  GRect sb = gbitmap_get_bounds(src);
  int sw = sb.size.w, sh = sb.size.h;
  if (size <= 0 || size >= sw || size >= sh) return NULL;
  GBitmap *dst = gbitmap_create_blank(GSize(size, size), GBitmapFormat8Bit);
  if (!dst) return NULL;
  int16_t *err = calloc(2 * 3 * (size + 2), sizeof(int16_t));   // this row's and the next row's error, per channel
  if (!err) { gbitmap_destroy(dst); return NULL; }
  for (int y = 0; y < size; y++) {
    int16_t *cur = err + (y & 1) * 3 * (size + 2), *nxt = err + ((y + 1) & 1) * 3 * (size + 2);
    memset(nxt, 0, 3 * (size + 2) * sizeof(int16_t));
    int y0 = y * sh / size, y1 = max_i(y0 + 1, (y + 1) * sh / size);
    GBitmapDataRowInfo drow = gbitmap_get_data_row_info(dst, y);
    bool ltr = !(y & 1);   // serpentine
    for (int k = 0; k < size; k++) {
      int x = ltr ? k : size - 1 - k;
      int x0 = x * sw / size, x1 = max_i(x0 + 1, (x + 1) * sw / size);
      int sum[3] = { 0, 0, 0 }, n = 0;
      for (int yy = y0; yy < y1; yy++) {
        GBitmapDataRowInfo srow = gbitmap_get_data_row_info(src, yy);
        for (int xx = x0; xx < x1; xx++) {
          uint8_t c = srow.data[xx];
          sum[0] += (c >> 4) & 3; sum[1] += (c >> 2) & 3; sum[2] += c & 3;
          n++;
        }
      }
      uint8_t out = 0xC0;
      for (int ch = 0; ch < 3; ch++) {
        int v = sum[ch] * 85 / n + cur[ch * (size + 2) + x + 1] / 16;   // 0..255 plus carried error
        int q = max_i(0, min_i(3, (v + 42) / 85));
        int e = (v - q * 85) * 16;
        out |= q << (4 - ch * 2);
        int16_t *c0 = cur + ch * (size + 2) + 1, *n0 = nxt + ch * (size + 2) + 1;
        int f = ltr ? 1 : -1;
        if (x + f >= 0 && x + f < size) c0[x + f] += e * 7 / 16;
        if (x - f >= 0 && x - f < size) n0[x - f] += e * 3 / 16;
        n0[x] += e * 5 / 16;
        if (x + f >= 0 && x + f < size) n0[x + f] += e / 16;
      }
      drow.data[x] = out;
    }
  }
  free(err);
  return dst;
#else
  return NULL;
#endif
}

// Nearest-neighbour scaling straight into the frame buffer (no floats).
void draw_bitmap_scaled(GContext *ctx, GBitmap *bm, GRect dst) {
  if (!bm) return;
  GRect src = gbitmap_get_bounds(bm);
  if (src.size.w == dst.size.w && src.size.h == dst.size.h) {
    graphics_context_set_compositing_mode(ctx, GCompOpAssign);
    graphics_draw_bitmap_in_rect(ctx, bm, dst);
    return;
  }
#ifdef PBL_COLOR
  if (gbitmap_get_format(bm) != GBitmapFormat8Bit) {
    graphics_draw_bitmap_in_rect(ctx, bm, GRect(dst.origin.x, dst.origin.y, min_i(dst.size.w, src.size.w), min_i(dst.size.h, src.size.h)));
    return;
  }
  GBitmap *fb = graphics_capture_frame_buffer(ctx);
  if (!fb) return;
  GRect fbb = gbitmap_get_bounds(fb);
  for (int y = 0; y < dst.size.h; y++) {
    int fy = dst.origin.y + y;
    if (fy < 0 || fy >= fbb.size.h) continue;
    GBitmapDataRowInfo row = gbitmap_get_data_row_info(fb, fy);
    GBitmapDataRowInfo srow = gbitmap_get_data_row_info(bm, y * src.size.h / dst.size.h);
    for (int x = 0; x < dst.size.w; x++) {
      int fx = dst.origin.x + x;
      if (fx < row.min_x || fx > row.max_x) continue;
      row.data[fx] = srow.data[x * src.size.w / dst.size.w];
    }
  }
  graphics_release_frame_buffer(ctx, fb);
#else
  graphics_draw_bitmap_in_rect(ctx, bm, dst);
#endif
}

// The band logo: a 1-bit shape from the phone (bits MSB first), tinted with the second-line color,
// shrunk to fit the box when needed, centered.
void draw_logo(GContext *ctx, GBitmap *logo, GRect r, GColor c) { draw_logo_up(ctx, logo, r, c, 256); }

// like draw_logo, but may grow the logo up to max_k/256 to fill the box (the artist bio's big view)
void draw_logo_up(GContext *ctx, GBitmap *logo, GRect r, GColor c, int max_k) {
  if (!logo) return;
  GRect lb = gbitmap_get_bounds(logo);
  int sw = lb.size.w, sh = lb.size.h;
  if (sw <= 0 || sh <= 0 || r.size.w <= 0 || r.size.h <= 0) return;
  // scale in 1/256ths, up to max_k
  int k = max_k;
  if (sw * k / 256 > r.size.w) k = r.size.w * 256 / sw;
  if (sh * k / 256 > r.size.h) k = r.size.h * 256 / sh;
  int w = sw * k / 256, h = sh * k / 256;
  if (w <= 0 || h <= 0) return;
  int x0 = r.origin.x + (r.size.w - w) / 2, y0 = r.origin.y + (r.size.h - h) / 2;
  const uint8_t *d = gbitmap_get_data(logo);
  int bpr = gbitmap_get_bytes_per_row(logo);
  graphics_context_set_stroke_color(ctx, c);
  for (int y = 0; y < h; y++) {
    const uint8_t *row = d + (y * 256 / k) * bpr;
    for (int x = 0; x < w; x++) {
      int sx = x * 256 / k;
      if (row[sx >> 3] & (0x80 >> (sx & 7))) graphics_draw_pixel(ctx, GPoint(x0 + x, y0 + y));
    }
  }
}
