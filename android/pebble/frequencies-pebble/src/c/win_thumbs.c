// Thumbs down, step 2: the report waits the phone's grace time (a draining bar), then goes.
// Up + Down together undo, hold Select sends now, Back goes to Now playing and the report still goes.
#include "freq.h"

static Window *s_win;
static Layer *s_layer;
static Hold s_hold;
static AppTimer *s_timer, *s_close;
static uint32_t s_end_ms;       // when the report goes, in local ms
#define GRACE_MS 15000


static int left_ms(void) {
  int l = (int)(s_end_ms - now_ms());
  return l < 0 ? 0 : l;
}

static const char *what_text(void) {
  switch (P.thumbs_what) {
    case 1: return "Bio report";
    case 2: return "Photo report";
    default: return "Bio and photo report";
  }
}

static void draw(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  bool big = is_large();
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  draw_clock(ctx, GRect(b.size.w / 2, big ? -5 : -4, b.size.w / 2 - 4, 30), GTextAlignmentRight, T.text2, false);
  int w = b.size.w - 12;
  int is = big ? 40 : 30;
  bool gone = P.verdict != 2;
  int left = left_ms();
  draw_icon(ctx, gone && P.thumbs_left == 0 && P.verdict == 0 ? IC_THUMB_DOWN : IC_THUMB_DOWN, GRect((b.size.w - is) / 2, big ? 18 : 14, is, is), T.accent);
  int y = big ? 60 : 46;
  GFont fn = fonts_get_system_font(big ? FONT_KEY_GOTHIC_28_BOLD : FONT_KEY_GOTHIC_24_BOLD);
  const char *name = P.bio_name[0] ? P.bio_name : P.artist;
  int hn = min_i(text_h(name, fn, w), big ? 34 : 28);
  draw_text(ctx, name, fn, GRect(6, y, w, hn + 4), GTextAlignmentCenter, T.text);
  y += hn + 2;
  char msg[64];
  GFont fm = fonts_get_system_font(big ? FONT_KEY_GOTHIC_24 : FONT_KEY_GOTHIC_18);
  if (P.verdict == 0) snprintf(msg, sizeof msg, "Undone. Nothing was sent.");
  else if (left == 0 && P.thumbs_left == 0) snprintf(msg, sizeof msg, "%s sent. Thanks!", what_text());
  else snprintf(msg, sizeof msg, "%s goes out in %d s", what_text(), (left + 999) / 1000);
  int hm = text_h(msg, fm, w);
  draw_text(ctx, msg, fm, GRect(6, y - 2, w, hm + 4), GTextAlignmentCenter, T.text2);
  y += hm + (big ? 10 : 6);
  // the draining bar
  int bw = b.size.w - (big ? 48 : 36);
  int bx = (b.size.w - bw) / 2;
  graphics_context_set_fill_color(ctx, T.stripe);
  graphics_fill_rect(ctx, GRect(bx, y, bw, 4), 0, GCornerNone);
  if (P.verdict == 2) {
    graphics_context_set_fill_color(ctx, T.accent);
    graphics_fill_rect(ctx, GRect(bx, y, bw * left / GRACE_MS, 4), 0, GCornerNone);
  }
  if (P.verdict != 2 || left == 0) return;
  // footer: ↑ + ↓ to undo / Hold Select to send now
  GFont ff = fonts_get_system_font(big ? FONT_KEY_GOTHIC_24 : FONT_KEY_GOTHIC_18);
  int lh = big ? 26 : 20;
  int fy = b.size.h - lh * 2 - 6;
  const char *undo = "to undo";
  int tw = graphics_text_layout_get_content_size(undo, ff, GRect(0, 0, 200, 30), GTextOverflowModeWordWrap, GTextAlignmentLeft).w;
  int ic = big ? 14 : 12;
  int plus_w = graphics_text_layout_get_content_size("+", ff, GRect(0, 0, 50, 30), GTextOverflowModeWordWrap, GTextAlignmentLeft).w;
  int total = ic + 6 + plus_w + 6 + ic + 6 + tw;
  int x = (b.size.w - total) / 2;
  int iy = fy + (big ? 9 : 6);
  draw_icon(ctx, IC_UP, GRect(x, iy, ic, ic), T.text2); x += ic + 6;
  draw_text(ctx, "+", ff, GRect(x, fy, plus_w + 2, lh + 4), GTextAlignmentLeft, T.text2); x += plus_w + 6;
  draw_icon(ctx, IC_DOWN, GRect(x, iy, ic, ic), T.text2); x += ic + 6;
  draw_text(ctx, undo, ff, GRect(x, fy, tw + 4, lh + 4), GTextAlignmentLeft, T.text3);
  draw_text(ctx, "Hold Select to send now", ff, GRect(2, fy + lh, b.size.w - 4, lh + 4), GTextAlignmentCenter, T.text3);
  int p = hold_progress(&s_hold, BUTTON_ID_SELECT);
  if (p > 0) draw_hold_ring(ctx, b, 1, IC_THUMB_DOWN, T.accent, p);
}

static void close_now(void *d) {
  s_close = NULL;
  now_push();  // back to Now playing
}

static void finish(int ms) {
  if (s_timer) { app_timer_cancel(s_timer); s_timer = NULL; }
  if (!s_close) s_close = app_timer_register(ms, close_now, NULL);
}

static void tick(void *d) {
  s_timer = NULL;
  if (left_ms() == 0) { P.thumbs_left = 0; layer_mark_dirty(s_layer); finish(1500); return; }
  layer_mark_dirty(s_layer);
  s_timer = app_timer_register(250, tick, NULL);
}

static void undo(Hold *h) {
  if (P.verdict != 2 || left_ms() == 0) return;
  P.verdict = 0;
  send_cmd(CMD_THUMB_UNDO, 0, 0, NULL);
  layer_mark_dirty(s_layer);
  finish(1200);
}

static void on_hold(Hold *h, ButtonId b) {
  if (b != BUTTON_ID_SELECT || P.verdict != 2) return;
  send_cmd(CMD_THUMB_SEND_NOW, 0, 0, NULL);
  s_end_ms = now_ms();
  P.thumbs_left = 0;
  layer_mark_dirty(s_layer);
  finish(1500);
}

static void back(ClickRecognizerRef r, void *ctx) { now_push(); }

static void click_config(void *ctx) {
  hold_click_config(ctx);
  window_single_click_subscribe(BUTTON_ID_BACK, back);
}

static void load(Window *w) {
  Layer *root = window_get_root_layer(w);
  s_layer = layer_create(layer_get_bounds(root));
  layer_set_update_proc(s_layer, draw);
  layer_add_child(root, s_layer);
  memset(&s_hold, 0, sizeof s_hold);
  s_hold.layer = s_layer;
  s_hold.on_hold = on_hold;
  s_hold.on_combo = undo;
  s_hold.combo_ms = 0;
  s_hold.hold_ms[BUTTON_ID_SELECT] = HOLD_MS;
  window_set_click_config_provider_with_context(w, click_config, &s_hold);
}

static void appear(Window *w) {
  if (!s_timer) s_timer = app_timer_register(250, tick, NULL);
  edge_back_subscribe();
}

static void disappear(Window *w) {
  touch_off();
  hold_cancel(&s_hold);
  if (s_timer) { app_timer_cancel(s_timer); s_timer = NULL; }
  if (s_close) { app_timer_cancel(s_close); s_close = NULL; }
}

static void unload(Window *w) {
  layer_destroy(s_layer); s_layer = NULL;
  window_destroy(w);
  s_win = NULL;
}

void thumbs_send_push(void) {
  if (s_win) window_stack_remove(s_win, false);
  s_end_ms = now_ms() + (P.thumbs_left ? P.thumbs_left : 15) * 1000;
  s_win = window_create();
  window_set_background_color(s_win, T.bg);
  window_set_window_handlers(s_win, (WindowHandlers){ .load = load, .unload = unload, .appear = appear, .disappear = disappear });
  window_stack_push(s_win, true);
}

void thumbs_refresh(uint32_t what) {
  if (!s_layer) return;
  if (what & CH_THUMBS) {
    // the phone's own count wins (it may have sent the report already)
    if (P.verdict == 2 && P.thumbs_left) s_end_ms = now_ms() + P.thumbs_left * 1000;
    if (P.verdict == 2 && P.thumbs_left == 0 && left_ms() > 0) { s_end_ms = now_ms(); finish(1500); }
    if (P.verdict == 0) finish(1200);
  }
  layer_mark_dirty(s_layer);
}
