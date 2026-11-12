// First time: how adding works. Shown once, the first time a press-to-listen / hold-to-save list opens.
// Hold Up and Down together for 2 s to go on (rings fill on both arrows); Back leaves without opening the list.
#include "freq.h"

static Window *s_win;
static Layer *s_layer;
static Hold s_hold;
static uint8_t s_then;
static bool s_swipe;  // the Now Playing swipes hint rather than the adding one

bool hint_seen(void) { return persist_exists(PK_HINT) && persist_read_bool(PK_HINT); }
bool swipe_hint_seen(void) { return persist_exists(PK_SWIPE_HINT) && persist_read_bool(PK_SWIPE_HINT); }

// three stacked chevrons, brightest at the end they point to
static void chevrons(GContext *ctx, int cx, int y, int w, int h, int gap, bool up) {
  GColor col[3] = { T.accent, T.text2, T.text3 };
  graphics_context_set_stroke_width(ctx, h >= 8 ? 3 : 2);
  graphics_context_set_antialiased(ctx, false);
  for (int i = 0; i < 3; i++) {
    int yy = up ? y + i * gap : y + (2 - i) * gap;
    graphics_context_set_stroke_color(ctx, col[i]);
    if (up) {
      graphics_draw_line(ctx, GPoint(cx - w / 2, yy + h), GPoint(cx, yy));
      graphics_draw_line(ctx, GPoint(cx, yy), GPoint(cx + w / 2, yy + h));
    } else {
      graphics_draw_line(ctx, GPoint(cx - w / 2, yy), GPoint(cx, yy + h));
      graphics_draw_line(ctx, GPoint(cx, yy + h), GPoint(cx + w / 2, yy));
    }
  }
}

// First time on Now Playing (touch watches, user 2026-11-10): what the swipes do.
static void draw_swipe(GContext *ctx, GRect b) {
  bool big = is_large();
  int w = b.size.w - 30, x = 4, cx = x + w / 2;
  int ch = big ? 9 : 7, cw = big ? 30 : 22, gap = big ? 8 : 6;
  GFont fk = fonts_get_system_font(big ? FONT_KEY_GOTHIC_24_BOLD : FONT_KEY_GOTHIC_18_BOLD);
  GFont fb = fonts_get_system_font(big ? FONT_KEY_GOTHIC_18 : FONT_KEY_GOTHIC_14);
  GFont fs = fonts_get_system_font(big ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_14_BOLD);
  int kh = big ? 24 : 18, bh = big ? 20 : 16;
  int y = big ? 8 : 4;
  chevrons(ctx, cx, y, cw, ch, gap, true);
  y += 2 * gap + ch + (big ? 4 : 2);
  draw_text(ctx, "Swipe up", fk, GRect(x, y - 4, w, kh + 4), GTextAlignmentCenter, T.text);
  y += kh;
  draw_text(ctx, "for the artist's bio", fb, GRect(x, y - 3, w, bh + 4), GTextAlignmentCenter, T.text2);
  y += bh + (big ? 6 : 3);
  draw_dotted_hline(ctx, x + 16, x + w - 16, y, T.text3);
  y += big ? 4 : 2;
  draw_text(ctx, "Swipe down", fk, GRect(x, y - 4, w, kh + 4), GTextAlignmentCenter, T.text);
  y += kh;
  draw_text(ctx, "for the lyrics", fb, GRect(x, y - 3, w, bh + 4), GTextAlignmentCenter, T.text2);
  y += bh + (big ? 4 : 2);
  chevrons(ctx, cx, y, cw, ch, gap, false);
  y += 2 * gap + ch + (big ? 6 : 3);
  draw_text(ctx, "Hold Up and Down for 2 seconds to go on", fs, GRect(x, y - 2, w, b.size.h - y + 2), GTextAlignmentCenter,
            T.accent);
}

static void draw(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  bool big = is_large();
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  if (s_swipe) {
    draw_swipe(ctx, b);
    int p = combo_progress(&s_hold);
    draw_hold_ring(ctx, b, 0, IC_UP, T.accent, p);
    draw_hold_ring(ctx, b, 2, IC_DOWN, T.accent, p);
    return;
  }
  int w = b.size.w - 30, x = 6;
  int y = big ? 4 : 0;
  GFont ft = fonts_get_system_font(big ? FONT_KEY_GOTHIC_28_BOLD : FONT_KEY_GOTHIC_24_BOLD);
  GFont fb = fonts_get_system_font(big ? FONT_KEY_GOTHIC_24 : FONT_KEY_GOTHIC_18);
  GFont fs = fonts_get_system_font(big ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_14_BOLD);
  draw_text(ctx, "How adding works", ft, GRect(x, y, b.size.w - 12, 34), GTextAlignmentCenter, T.accent);
  y += big ? 36 : 28;
  int is = big ? 16 : 12;
  draw_icon(ctx, IC_PLAY, GRect(x + (w - is) / 2, y, is, is), T.text);
  y += is + 2;
  const char *a = "Press Select to listen. Nothing is saved.";
  int ha = text_h(a, fb, w);
  draw_text(ctx, a, fb, GRect(x, y - 4, w, ha + 4), GTextAlignmentCenter, T.text2);
  y += ha + 2;
  int pr = big ? 11 : 8;
  GPoint c = GPoint(x + w / 2, y + pr);
  graphics_context_set_stroke_color(ctx, T.accent);
  graphics_context_set_stroke_width(ctx, 2);
  graphics_draw_circle(ctx, c, pr);
  draw_icon(ctx, IC_PLUS, GRect(c.x - pr + 4, c.y - pr + 4, pr * 2 - 8, pr * 2 - 8), T.accent);
  y += pr * 2 + 2;
  const char *s = "Hold Select to save it to Saved Frequencies.";
  int hs = text_h(s, fb, w);
  draw_text(ctx, s, fb, GRect(x, y - 4, w, hs + 4), GTextAlignmentCenter, T.text2);
  y += hs + 2;
  draw_text(ctx, "Hold Up and Down for 2 seconds to go on", fs, GRect(x, y, w, b.size.h - y), GTextAlignmentCenter, T.text);
  int p = combo_progress(&s_hold);
  draw_hold_ring(ctx, b, 0, IC_UP, T.accent, p);
  draw_hold_ring(ctx, b, 2, IC_DOWN, T.accent, p);
}

static void got_it(Hold *h) {
  persist_write_bool(s_swipe ? PK_SWIPE_HINT : PK_HINT, true);
  uint8_t then = s_then;
  window_stack_remove(s_win, false);
  if (then) list_push(then);
}

static void load(Window *w) {
  Layer *root = window_get_root_layer(w);
  s_layer = layer_create(layer_get_bounds(root));
  layer_set_update_proc(s_layer, draw);
  layer_add_child(root, s_layer);
  memset(&s_hold, 0, sizeof s_hold);
  s_hold.layer = s_layer;
  s_hold.on_combo = got_it;
  s_hold.combo_ms = 2000;
  window_set_click_config_provider_with_context(w, hold_click_config, &s_hold);
}

static void disappear(Window *w) { hold_cancel(&s_hold); }

static void unload(Window *w) {
  layer_destroy(s_layer); s_layer = NULL;
  window_destroy(w);
  s_win = NULL;
}

void swipe_hint_push(void) {
  if (s_win) return;
  hint_push(0);
  s_swipe = true;
}

void hint_push(uint8_t then_kind) {
  if (s_win) return;
  s_then = then_kind;
  s_swipe = false;
  s_win = window_create();
  window_set_background_color(s_win, T.bg);
  window_set_window_handlers(s_win, (WindowHandlers){ .load = load, .unload = unload, .disappear = disappear });
  window_stack_push(s_win, true);
}
