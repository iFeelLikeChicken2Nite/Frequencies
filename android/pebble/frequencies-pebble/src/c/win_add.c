// Add Frequencies: Search, a greyed "Find more on phone" (the phone's Add screen for URL, QR, Local, YouTube,
// Twitch, iBroadcast), then today's featured picks as a sideways row of pictures, "Previously featured" last.
// Select on a picture listens, hold saves (ring shows +).
#include "freq.h"

static Window *s_win;
static Layer *s_layer;
static Hold s_hold;
static int s_focus;            // 0 Search, 1 Find more, 2.. pictures, last = Previously featured
static int s_xoff;             // the picture row's sideways scroll
static char s_toast[40];
static AppTimer *s_toast_timer;
#if defined(PBL_TOUCH)
static GPoint s_t0;
static bool s_tracking;
static int s_x0;
#endif

static int n_featured(void) { return F.loaded ? min_i(F.total, 8) : 0; }
static int n_items(void) { return 2 + n_featured() + 1; }
static int tile(void) { return is_large() ? 80 : 56; }
static int gap(void) { return 8; }

static GRect btn_rect(GRect b, int i) {
  bool big = is_large();
  int top = HEADER_H + (big ? 6 : 4), h = big ? 58 : 40;
  int w = (b.size.w - 14) / 2;
  return GRect(4 + i * (w + 6), top, w, h);
}

static int row_y(GRect b) { return btn_rect(b, 0).origin.y + btn_rect(b, 0).size.h + (is_large() ? 24 : 18); }

static GRect pic_rect(GRect b, int k) {  // k = 0.. featured, n_featured() = Previously featured
  return GRect(4 + k * (tile() + gap()) - s_xoff, row_y(b), tile(), tile());
}

static void keep_in_view(GRect b) {
  if (s_focus < 2) { s_xoff = 0; return; }
  int k = s_focus - 2;
  int x = 4 + k * (tile() + gap());
  if (x - s_xoff + tile() > b.size.w - 4) s_xoff = x + tile() - (b.size.w - 4);
  if (x - s_xoff < 4) s_xoff = x - 4;
  if (s_xoff < 0) s_xoff = 0;
}

static void dashed_rect(GContext *ctx, GRect r, GColor c) {
  graphics_context_set_fill_color(ctx, c);
  for (int x = r.origin.x + 4; x < r.origin.x + r.size.w - 4; x += 6) {
    graphics_fill_rect(ctx, GRect(x, r.origin.y, 3, 1), 0, GCornerNone);
    graphics_fill_rect(ctx, GRect(x, r.origin.y + r.size.h - 1, 3, 1), 0, GCornerNone);
  }
  for (int y = r.origin.y + 4; y < r.origin.y + r.size.h - 4; y += 6) {
    graphics_fill_rect(ctx, GRect(r.origin.x, y, 1, 3), 0, GCornerNone);
    graphics_fill_rect(ctx, GRect(r.origin.x + r.size.w - 1, y, 1, 3), 0, GCornerNone);
  }
}

static void draw(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  bool big = is_large();
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  draw_header(ctx, b, "Add Frequency…", s_toast);
  // Search
  GRect s = btn_rect(b, 0);
  bool f0 = s_focus == 0;
  graphics_context_set_fill_color(ctx, f0 ? T.accent : T.bg);
  graphics_fill_rect(ctx, s, 8, GCornersAll);
  if (!f0) { graphics_context_set_stroke_color(ctx, T.text3); graphics_draw_round_rect(ctx, s, 8); }
  int is = big ? 20 : 16;
  GFont fb = fonts_get_system_font(big ? FONT_KEY_GOTHIC_24_BOLD : FONT_KEY_GOTHIC_18_BOLD);
  int sw = graphics_text_layout_get_content_size("Search", fb, GRect(0, 0, 100, 30), GTextOverflowModeWordWrap, GTextAlignmentLeft).w;
  int sx = s.origin.x + (s.size.w - is - 4 - sw) / 2;
  draw_icon(ctx, IC_SEARCH, GRect(sx, s.origin.y + (s.size.h - is) / 2, is, is), f0 ? GColorBlack : T.text);
  draw_text(ctx, "Search", fb, GRect(sx + is + 4, s.origin.y + (s.size.h - (big ? 30 : 22)) / 2 - (big ? 1 : 2), sw + 4, 30),
            GTextAlignmentLeft, f0 ? GColorBlack : T.text);
  // Find more on phone (greyed, dashed)
  GRect m = btn_rect(b, 1);
  bool f1 = s_focus == 1;
  if (f1) { graphics_context_set_fill_color(ctx, T.stripe); graphics_fill_rect(ctx, m, 8, GCornersAll); }
  dashed_rect(ctx, m, f1 ? T.accent : T.text3);
  int pi = big ? 18 : 14;
  draw_icon(ctx, IC_PHONE, GRect(m.origin.x + 4, m.origin.y + (m.size.h - pi) / 2, pi, pi), f1 ? T.text : T.text3);
  GFont fm = fonts_get_system_font(big ? FONT_KEY_GOTHIC_18 : FONT_KEY_GOTHIC_14);
  draw_text(ctx, "Find more on phone", fm, GRect(m.origin.x + pi + 6, m.origin.y + (big ? 6 : 2), m.size.w - pi - 8, m.size.h - 4),
            GTextAlignmentCenter, f1 ? T.text : T.text3);
  // FEATURED and the picture row
  int ry = row_y(b);
  draw_text(ctx, "FEATURED", fonts_get_system_font(big ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_14_BOLD),
            GRect(6, ry - (big ? 24 : 18), b.size.w - 12, 22), GTextAlignmentLeft, T.text3);
  GFont fname = fonts_get_system_font(big ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_14_BOLD);
  int nf = n_featured();
  if (!F.loaded) {
    draw_text(ctx, P.connected ? "Loading…" : "", fonts_get_system_font(FONT_KEY_GOTHIC_18), GRect(6, ry + 8, b.size.w - 12, 24),
              GTextAlignmentLeft, T.text2);
  }
  for (int k = 0; k <= nf; k++) {
    if (!F.loaded && k == 0) continue;
    GRect r = pic_rect(b, k);
    if (r.origin.x > b.size.w || r.origin.x + r.size.w < 0) continue;
    bool foc = s_focus == 2 + k;
    if (k < nf) {
      const Row *row = list_row(&F, k);
      if (P.featured[k]) draw_bitmap_scaled(ctx, P.featured[k], r);
      else { graphics_context_set_fill_color(ctx, T.stripe); graphics_fill_rect(ctx, r, 0, GCornerNone); draw_icon(ctx, IC_RADIO, grect_inset(r, GEdgeInsets(r.size.w / 4)), T.text3); }
      if (row && (row->flags & RF_SAVED)) {
        int c = big ? 22 : 18;
        GPoint ctr = GPoint(r.origin.x + r.size.w - c / 2 - 3, r.origin.y + c / 2 + 3);
        graphics_context_set_fill_color(ctx, GColorBlack);
        graphics_fill_circle(ctx, ctr, c / 2);
        draw_icon(ctx, IC_CHECK, GRect(ctr.x - c / 2 + 3, ctr.y - c / 2 + 3, c - 6, c - 6), T.accent);
      }
      if (row) draw_text(ctx, row->name, fname, GRect(r.origin.x - 2, r.origin.y + r.size.h, r.size.w + 4, 22), GTextAlignmentCenter,
                         foc ? T.accent : T.text);
    } else {
      // Previously featured, last
      graphics_context_set_stroke_color(ctx, T.text3);
      graphics_draw_round_rect(ctx, r, 6);
      int ci = r.size.w / 3;
      draw_icon(ctx, IC_CLOCK, GRect(r.origin.x + (r.size.w - ci) / 2, r.origin.y + 8, ci, ci), foc ? T.accent : T.text2);
      draw_text(ctx, "Previously featured", fonts_get_system_font(big ? FONT_KEY_GOTHIC_14_BOLD : FONT_KEY_GOTHIC_09),
                GRect(r.origin.x + 2, r.origin.y + ci + 10, r.size.w - 4, r.size.h - ci - 10), GTextAlignmentCenter, foc ? T.accent : T.text2);
    }
    graphics_context_set_stroke_color(ctx, foc ? T.accent : T.text3);
    graphics_context_set_stroke_width(ctx, foc ? 3 : 1);
    graphics_draw_rect(ctx, foc ? grect_inset(r, GEdgeInsets(-1)) : r);
    graphics_context_set_stroke_width(ctx, 1);
    if (foc && k < nf) {
      int p = hold_progress(&s_hold, BUTTON_ID_SELECT);
      if (p > 0) draw_hold_ring(ctx, b, 1, IC_PLUS, T.accent, p);
    }
  }
}

static void toast_off(void *d) { s_toast_timer = NULL; s_toast[0] = 0; if (s_layer) layer_mark_dirty(s_layer); }
static void toast(const char *t) {
  strncpy(s_toast, t, sizeof s_toast - 1);
  if (s_toast_timer) app_timer_cancel(s_toast_timer);
  s_toast_timer = app_timer_register(2500, toast_off, NULL);
}

static void act(bool hold) {
  int nf = n_featured();
  // the first time a press-to-listen / hold-to-save list opens, explain it once
  if (s_focus == 0) { if (!hold) { if (hint_seen()) list_push(LIST_SEARCH); else hint_push(LIST_SEARCH); } return; }
  if (s_focus == 1) { if (!hold) { send_cmd(CMD_FIND_MORE, 0, 0, NULL); toast("On your phone"); } return; }
  int k = s_focus - 2;
  if (k == nf) { if (!hold) { if (hint_seen()) list_push(LIST_PREV_FEATURED); else hint_push(LIST_PREV_FEATURED); } return; }
  const Row *r = list_row(&F, k);
  if (!r) return;
  if (!hint_seen()) { hint_push(0); return; }
  if (hold) {
    send_cmd(CMD_SAVE, r->id, LIST_FEATURED, NULL);
    ((Row *)r)->flags |= RF_SAVED;
    toast("Saved");
  } else {
    send_cmd(CMD_LISTEN, r->id, LIST_FEATURED, NULL);
    toast("Tuning in");
  }
}

static void on_click(Hold *h, ButtonId b) {
  GRect bb = layer_get_bounds(s_layer);
  if (b == BUTTON_ID_UP && s_focus > 0) s_focus--;
  else if (b == BUTTON_ID_DOWN && s_focus < n_items() - 1) s_focus++;
  else if (b == BUTTON_ID_SELECT) act(false);
  keep_in_view(bb);
  layer_mark_dirty(s_layer);
}

static void on_hold(Hold *h, ButtonId b) {
  if (b == BUTTON_ID_SELECT) act(true);
  layer_mark_dirty(s_layer);
}

#if defined(PBL_TOUCH)
static void touch(const TouchEvent *e, void *ctx) {
  input_seen();
  if (edge_back(e)) { s_tracking = false; window_stack_pop(true); return; }
  if (edge_tracking()) { s_tracking = false; return; }
  GPoint p = GPoint(e->x, e->y);
  GRect b = layer_get_bounds(s_layer);
  int nf = n_featured();
  int span = max_i(0, 4 + (nf + 1) * (tile() + gap()) - b.size.w);
  if (e->type == TouchEvent_Touchdown) { s_t0 = p; s_tracking = true; s_x0 = s_xoff; return; }
  if (!s_tracking) return;
  if (e->type == TouchEvent_PositionUpdate) {
    if (s_t0.y >= row_y(b) - 4) { s_xoff = max_i(0, min_i(span, s_x0 - (p.x - s_t0.x))); layer_mark_dirty(s_layer); }
    return;
  }
  s_tracking = false;
  if (abs(p.x - s_t0.x) > 10 || abs(p.y - s_t0.y) > 10) return;
  for (int i = 0; i < 2; i++) {
    GRect r = btn_rect(b, i);
    if (grect_contains_point(&r, &p)) { s_focus = i; act(false); layer_mark_dirty(s_layer); return; }
  }
  for (int k = 0; k <= nf; k++) {
    GRect r = pic_rect(b, k);
    r.size.h += 22;
    if (grect_contains_point(&r, &p)) { s_focus = 2 + k; act(false); layer_mark_dirty(s_layer); return; }
  }
}
#endif

static void load(Window *w) {
  Layer *root = window_get_root_layer(w);
  s_layer = layer_create(layer_get_bounds(root));
  layer_set_update_proc(s_layer, draw);
  layer_add_child(root, s_layer);
  memset(&s_hold, 0, sizeof s_hold);
  s_hold.layer = s_layer;
  s_hold.on_click = on_click;
  s_hold.on_hold = on_hold;
  s_hold.hold_ms[BUTTON_ID_SELECT] = HOLD_MS;
  window_set_click_config_provider_with_context(w, hold_click_config, &s_hold);
#if defined(PBL_TOUCH)
  window_set_touch_bridge_disabled(w, true);
#endif
}

static void appear(Window *w) {
#if defined(PBL_TOUCH)
  if (touch_service_is_enabled()) touch_service_subscribe(touch, NULL);
#endif
  if (!F.loaded) { F.kind = LIST_FEATURED; send_cmd(CMD_GET_FEATURED, 0, 0, NULL); }
}

static void disappear(Window *w) {
#if defined(PBL_TOUCH)
  touch_service_unsubscribe();
#endif
  hold_cancel(&s_hold);
}

static void unload(Window *w) {
  if (s_toast_timer) { app_timer_cancel(s_toast_timer); s_toast_timer = NULL; }
  s_toast[0] = 0;
  featured_free();   // about 25-45 KB of pictures: the heap is only about 70 KB
  layer_destroy(s_layer); s_layer = NULL;
  window_destroy(w);
  s_win = NULL;
}

void add_push(void) {
  if (s_win) return;
  s_focus = 0; s_xoff = 0;
  s_win = window_create();
  window_set_background_color(s_win, T.bg);
  window_set_window_handlers(s_win, (WindowHandlers){ .load = load, .unload = unload, .appear = appear, .disappear = disappear });
  window_stack_push(s_win, true);
}

void add_refresh(uint32_t what) {
  if (!s_layer) return;
  if (what & (CH_FEATURED | CH_THEME | CH_CONN)) {
    if (s_focus >= n_items()) s_focus = n_items() - 1;
    layer_mark_dirty(s_layer);
  }
}
