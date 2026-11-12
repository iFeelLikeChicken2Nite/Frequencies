// Artist bio: photo, band logo (else the name), source, then the bio.
// Up and Down scroll it smoothly; hold Select for thumbs down (no thumbs up any more, user 2026-11-10).
// The photo starts big and moves aside when you scroll down or tap it; back at the top it grows again.
// Touch: drag to scroll; at the top, pull down to go back to Now Playing (the screen follows the thumb).
#include "freq.h"

static Window *s_win;
static Layer *s_wrap, *s_layer, *s_body, *s_mid, *s_over;
static Hold s_hold;
static int s_scroll, s_scroll_to, s_text_h, s_pitch;
static AppTimer *s_scroll_timer, *s_exp_timer;
static int s_exp = 1000, s_exp_to = 1000, s_exp_t = 1000;  // the photo: 1000 big, 0 aside (eased / linear)
static uint32_t s_exp_last;
static GBitmap *s_photo_small;   // an averaged copy for the small size
#if defined(PBL_TOUCH)
static GPoint s_t0;
static bool s_tracking;
static int s_mode;               // 0 undecided, 1 scrolling, 2 pulling back
static int s_scroll0;
static Pull s_pull;
static int s_last_y, s_vel;      // the finger's speed, px per second (smoothed), for the glide after a flick
static uint32_t s_last_ms;
#endif

static GFont body_font(void) { return fonts_get_system_font(is_large() ? FONT_KEY_GOTHIC_24 : FONT_KEY_GOTHIC_18); }
static int edge_w(void) { return is_large() ? 24 : 20; }
static int lerp(int a, int b, int g) { return a + (b - a) * g / 1000; }
// the photo's two sizes: big (the phone sends this size) and aside
#define PB (is_large() ? 116 : 76)
#define PS (is_large() ? 76 : 56)
static int body_top(int e) {
  bool big = is_large();
  int small_top = big ? 84 : 64;
  int big_top = (big ? 22 : 18) + PB + (big ? 46 : 38);
  return lerp(small_top, big_top, e);
}

static const char *bio(void) {
  if (P.bio_text && P.bio_text[0]) return P.bio_text;
  return P.bio_name[0] ? "No bio found for this artist." : "Looking for the bio…";
}

static void measure(void) {
  GRect b = layer_get_bounds(s_body);
  GFont f = body_font();
  s_text_h = text_h(bio(), f, b.size.w);
  s_pitch = (text_h("A\nA\nA\nA\nA", f, 200) - text_h("A", f, 200)) / 4;
  if (s_pitch < 12) s_pitch = 18;
}

static int max_scroll(void) {
  int h = layer_get_bounds(s_body).size.h;
  int lines = (s_text_h + s_pitch / 2) / s_pitch, shown = h / s_pitch;
  return max_i(0, (lines - shown) * s_pitch);
}

static void draw_text_at(GContext *ctx, Layer *l, int dy, GColor c) {
  GRect b = layer_get_bounds(l);
  graphics_context_set_text_color(ctx, c);
  graphics_draw_text(ctx, bio(), body_font(), GRect(0, -s_scroll - dy, b.size.w, s_text_h + 40),
                     GTextOverflowModeWordWrap, GTextAlignmentLeft, NULL);
}

// the whole text in the darker color; the middle child redraws all but the edge lines in the normal one
static void draw_body(Layer *l, GContext *ctx) {
  draw_text_at(ctx, l, 0, T.text3);
  if (s_scroll > 0) {  // hide the tails of the line above
    graphics_context_set_fill_color(ctx, T.bg);
    graphics_fill_rect(ctx, GRect(0, 0, layer_get_bounds(l).size.w, s_pitch / 4), 0, GCornerNone);
  }
}
static void draw_mid(Layer *l, GContext *ctx) {
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, layer_get_bounds(l), 0, GCornerNone);
  draw_text_at(ctx, l, layer_get_frame(l).origin.y, T.text2);
}

static void place_body(void) {
  GRect b = layer_get_bounds(window_get_root_layer(s_win));
  int t = body_top(P.photo ? s_exp : 0);  // no photo: no room kept for one
  int h = max_i(s_pitch, (b.size.h - t - 2) / s_pitch * s_pitch);  // whole lines only
  layer_set_frame(s_body, GRect(4, t, b.size.w - edge_w() - 6, h));
  layer_set_bounds(s_body, GRect(0, 0, b.size.w - edge_w() - 6, h));
}

static void place_mid(void) {
  GRect b = layer_get_bounds(s_body);
  int top = s_scroll > 0 ? s_pitch : 0;                       // once scrolled, the top line is darker too
  int bottom = s_scroll < max_scroll() ? s_pitch : 0;         // more follows: the last line is darker
  // line up the edges with the text lines
  int first = top ? ((s_scroll + top + s_pitch - 1) / s_pitch) * s_pitch - s_scroll : 0;
  int last = bottom ? ((s_scroll + b.size.h - bottom) / s_pitch) * s_pitch - s_scroll : b.size.h;
  if (last <= first) last = b.size.h;
  layer_set_frame(s_mid, GRect(0, first, b.size.w, last - first));
  layer_mark_dirty(s_body);
  layer_mark_dirty(s_mid);
}

static void draw(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  bool big = is_large();
  int e = s_exp;
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  draw_clock(ctx, GRect(b.size.w / 2, big ? -5 : -4, b.size.w / 2 - 4, 30), GTextAlignmentRight, T.text2, false);
  int right = b.size.w - edge_w() - 2;
  int x = 4;
  if (P.photo) {
    int sz = lerp(PS, PB, e);
    GRect r = GRect(lerp(4, (right - PB) / 2, e), lerp(4, big ? 22 : 18, e), sz, sz);
    GBitmap *bm = P.photo;
    if (s_photo_small && sz == PS) bm = s_photo_small;
    draw_bitmap_scaled(ctx, bm, r);
    if (e < 500) x = PS + 10;
  }
  if (P.photo && e >= 500) {
    // big photo: the band logo (else the name, large) fills the room between the photo and the bio (user, 2026-11-10)
    int py = lerp(4, big ? 22 : 18, e) + lerp(PS, PB, e);
    GRect r = GRect(4, py + 2, right - 4, body_top(e) - py - 4);
    if (P.logo && (P.flags & FL_LOGO)) draw_logo_up(ctx, P.logo, r, T.text, 512);
    else {
      const char *n = P.bio_name[0] ? P.bio_name : P.artist;
      // the biggest font that fits the box (long names drop a size)
      static const char *const keys[] = { FONT_KEY_GOTHIC_28_BOLD, FONT_KEY_GOTHIC_24_BOLD, FONT_KEY_GOTHIC_18_BOLD };
      GFont f = NULL;
      int h = 0;
      for (int i = big ? 0 : 1; i < 3; i++) {
        f = fonts_get_system_font(keys[i]);
        h = text_h(n, f, r.size.w);
        if (h <= r.size.h + 6) break;
      }
      h = min_i(h, r.size.h + 6);
      draw_text(ctx, n, f, GRect(r.origin.x, r.origin.y + (r.size.h - h) / 2 - 4, r.size.w, h + 4), GTextAlignmentCenter, T.text);
    }
    return;
  }
  if (P.photo) {
    // photo aside: no logo or name (user, 2026-11-10); just where the bio comes from, beside the photo
    if (P.bio_from[0])
      draw_text(ctx, P.bio_from, fonts_get_system_font(big ? FONT_KEY_GOTHIC_18 : FONT_KEY_GOTHIC_14),
                GRect(x, big ? 24 : 18, right - x, 22), GTextAlignmentLeft, T.text3);
    return;
  }
  // no photo: the logo (else the name) and the source at the top, as before
  int y = big ? 26 : 20;
  if (P.logo && (P.flags & FL_LOGO)) {
    draw_logo(ctx, P.logo, GRect(x, y, right - x, big ? 20 : 16), T.text);
    y += big ? 24 : 20;
  } else {
    const char *n = P.bio_name[0] ? P.bio_name : P.artist;
    GFont f = fonts_get_system_font(big ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_14_BOLD);
    int h = min_i(text_h(n, f, right - x), big ? 40 : 32);
    draw_text(ctx, n, f, GRect(x, y - 4, right - x, h + 2), GTextAlignmentLeft, T.text);
    y += h;
  }
  if (P.bio_from[0])
    draw_text(ctx, P.bio_from, fonts_get_system_font(big ? FONT_KEY_GOTHIC_18 : FONT_KEY_GOTHIC_14),
              GRect(x, y - 2, right - x, 22), GTextAlignmentLeft, T.text3);
}

static void draw_over(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  int icons[3] = { IC_UP, P.verdict == 2 ? IC_THUMB_DOWN_F : IC_THUMB_DOWN, IC_DOWN };
  GColor cols[3] = { s_scroll > 0 || s_exp_to == 0 ? T.text2 : T.text3, P.verdict == 2 ? T.down : T.text2,
                     s_scroll < max_scroll() || s_exp_to ? T.text2 : T.text3 };
  int p = hold_progress(&s_hold, BUTTON_ID_SELECT);
  if (p > 0) icons[1] = -1;
  draw_edge_icons(ctx, b, icons, cols);
  if (p > 0) draw_hold_ring(ctx, b, 1, IC_THUMB_DOWN, T.down, p);
}

// ---- the photo growing and moving aside (eased, about 30 frames a second) ----
static int ease(int t) {  // smoothstep on 0..1000
  return (int)((int32_t)t * t / 1000 * (3000 - 2 * t) / 1000);
}

static void exp_step(void *d) {
  s_exp_timer = NULL;
  uint32_t t = now_ms(), dt = t - s_exp_last;
  s_exp_last = t;
  if (dt > 100) dt = 33;
  if (s_exp_t < s_exp_to) s_exp_t = min_i(s_exp_to, s_exp_t + max_i(1, dt * 1000 / 350));
  else if (s_exp_t > s_exp_to) s_exp_t = max_i(s_exp_to, s_exp_t - max_i(1, dt * 1000 / 300));
  s_exp = ease(s_exp_t);
  place_body();
  s_scroll = min_i(s_scroll, max_scroll());
  s_scroll_to = min_i(s_scroll_to, max_scroll());
  place_mid();
  layer_mark_dirty(s_layer);
  layer_mark_dirty(s_over);
  if (s_exp_t != s_exp_to) s_exp_timer = app_timer_register(33, exp_step, NULL);
}

static void expand(bool big) {
  int to = big ? 1000 : 0;
  if (s_exp_to == to) return;
  s_exp_to = to;
  if (!s_exp_timer) { s_exp_last = now_ms(); s_exp_timer = app_timer_register(16, exp_step, NULL); }
}

// ---- smooth scrolling ----
static void scroll_step(void *d) {
  s_scroll_timer = NULL;
  int left = s_scroll_to - s_scroll, st = left / 4;  // eased: quick at first, slowing to a stop
  if (abs(st) < 3) st = left > 0 ? min_i(left, 3) : max_i(left, -3);
  s_scroll += st;
  place_mid();
  layer_mark_dirty(s_over);
  if (s_scroll != s_scroll_to) s_scroll_timer = app_timer_register(30, scroll_step, NULL);
}

static void scroll_to(int y) {
  s_scroll_to = max_i(0, min_i(max_scroll(), y));
  if (!s_scroll_timer && s_scroll_to != s_scroll) s_scroll_timer = app_timer_register(16, scroll_step, NULL);
}

static void up_click(ClickRecognizerRef rec, void *ctx) {
  input_seen();
  if (s_scroll_to <= 0) { expand(true); return; }  // at the top: the photo grows again
  int to = s_scroll_to - 2 * s_pitch;
  scroll_to(to);
  if (to <= 0) expand(true);
}

static void down_click(ClickRecognizerRef rec, void *ctx) {
  input_seen();
  if (s_exp_to && P.photo) { expand(false); return; }  // scrolling down first moves the photo aside
  scroll_to(s_scroll_to + 2 * s_pitch);
}

static void on_click(Hold *h, ButtonId b) {
  if (b == BUTTON_ID_SELECT && P.photo) expand(!s_exp_to);  // a press: the photo grows or moves aside
}

static void on_hold(Hold *h, ButtonId b) {
  if (b == BUTTON_ID_SELECT) thumbs_ask_push();  // thumbs down needs a full hold (user, 2026-11-10)
}

static void click_config(void *ctx) {
  hold_click_config_select(ctx);
  window_single_repeating_click_subscribe(BUTTON_ID_UP, 120, up_click);
  window_single_repeating_click_subscribe(BUTTON_ID_DOWN, 120, down_click);
}

#if defined(PBL_TOUCH)
static void touch(const TouchEvent *e, void *ctx) {
  input_seen();
  if (edge_back(e)) { s_tracking = false; window_stack_pop(true); return; }
  if (edge_tracking()) { s_tracking = false; return; }
  GPoint p = GPoint(e->x, e->y);
  if (e->type == TouchEvent_Touchdown) {
    if (pull_busy(&s_pull)) return;
    s_t0 = p; s_tracking = true; s_mode = 0; s_scroll0 = s_scroll;
    if (s_scroll_timer) { app_timer_cancel(s_scroll_timer); s_scroll_timer = NULL; s_scroll_to = s_scroll; }  // a touch stops a glide
    s_last_y = p.y; s_last_ms = now_ms(); s_vel = 0;
    return;
  }
  if (!s_tracking) return;
  int dx = p.x - s_t0.x, dy = p.y - s_t0.y;
  if (e->type == TouchEvent_PositionUpdate) {
    uint32_t t = now_ms();
    int dt = (int)(t - s_last_ms);
    if (dt > 0) {
      int v = (p.y - s_last_y) * 1000 / dt;
      s_vel = (s_vel + v * 2) / 3;  // mostly the latest movement
      s_last_y = p.y; s_last_ms = t;
    }
    if (!s_mode && abs(dy) > 8 && abs(dy) > abs(dx)) {
      // at the top with the photo big, a pull down goes back; anything else scrolls
      if (dy > 0 && s_scroll == 0 && (s_exp_to || !P.photo)) s_mode = 2;
      else {
        s_mode = 1;
        if (dy < 0 && s_exp_to && P.photo) expand(false);
        if (s_scroll_timer) { app_timer_cancel(s_scroll_timer); s_scroll_timer = NULL; }
      }
    }
    if (s_mode == 2) pull_move(&s_pull, dy);
    else if (s_mode == 1) {
      s_scroll = s_scroll_to = max_i(0, min_i(max_scroll(), s_scroll0 - dy));  // the text follows the finger
      place_mid();
      layer_mark_dirty(s_over);
    }
    return;
  }
  if (e->type != TouchEvent_Liftoff) return;
  s_tracking = false;
  if (s_mode == 2) { pull_release(&s_pull); return; }
  if (s_mode == 1) {
    if (dy > 0 && s_scroll == 0) { expand(true); return; }
    // a flick keeps the text going and it slows to a stop; a finger held still before lifting doesn't
    if (now_ms() - s_last_ms > 120) s_vel = 0;
    if (abs(s_vel) > 150) scroll_to(s_scroll - s_vel * 35 / 100);
    return;
  }
  if (abs(dx) < 10 && abs(dy) < 10 && P.photo) {
    GRect b = layer_get_bounds(window_get_root_layer(s_win));
    int sz = lerp(PS, PB, s_exp);
    GRect r = GRect(lerp(4, (b.size.w - edge_w() - 2 - PB) / 2, s_exp), lerp(4, is_large() ? 22 : 18, s_exp), sz, sz);
    if (grect_contains_point(&r, &p)) expand(!s_exp_to);  // tap the photo: aside, or big again
  }
}
#endif

bool bio_is_open(void) { return s_win != NULL; }

void bio_drop_small(void) {
  if (s_photo_small) { gbitmap_destroy(s_photo_small); s_photo_small = NULL; }
}

static void make_small(void) {
  if (s_photo_small) { gbitmap_destroy(s_photo_small); s_photo_small = NULL; }
  if (P.photo) s_photo_small = shrink_bitmap(P.photo, PS);
}

static void load(Window *w) {
  Layer *root = window_get_root_layer(w);
  GRect b = layer_get_bounds(root);
  // Now Playing's shrunk cover isn't on screen here: its memory goes to this photo (Now Playing makes it again)
  if (P.cover_small) { gbitmap_destroy(P.cover_small); P.cover_small = NULL; }
  s_wrap = layer_create(b);
  layer_add_child(root, s_wrap);
  s_layer = layer_create(b);
  layer_set_update_proc(s_layer, draw);
  layer_add_child(s_wrap, s_layer);
  s_body = layer_create(GRect(4, 84, b.size.w - edge_w() - 6, 40));
  layer_set_update_proc(s_body, draw_body);
  layer_add_child(s_wrap, s_body);
  s_mid = layer_create(layer_get_bounds(s_body));
  layer_set_update_proc(s_mid, draw_mid);
  layer_add_child(s_body, s_mid);
  s_over = layer_create(b);
  layer_set_update_proc(s_over, draw_over);
  layer_add_child(s_wrap, s_over);
  memset(&s_hold, 0, sizeof s_hold);
  s_hold.layer = s_over;
  s_hold.on_click = on_click;
  s_hold.on_hold = on_hold;
  s_hold.hold_ms[BUTTON_ID_SELECT] = HOLD_MS;
  window_set_click_config_provider_with_context(w, click_config, &s_hold);
#if defined(PBL_TOUCH)
  window_set_touch_bridge_disabled(w, true);
  pull_init(&s_pull, w, s_wrap, 1);
#endif
  s_scroll = s_scroll_to = 0;
  s_exp = s_exp_t = s_exp_to = 1000;  // the photo starts big
  measure();
  make_small();
  place_body();
  place_mid();
}

static void appear(Window *w) {
#if defined(PBL_TOUCH)
  if (touch_service_is_enabled()) touch_service_subscribe(touch, NULL);
#endif
}

static void disappear(Window *w) {
#if defined(PBL_TOUCH)
  touch_service_unsubscribe();
#endif
  hold_cancel(&s_hold);
}

static void unload(Window *w) {
  if (s_scroll_timer) { app_timer_cancel(s_scroll_timer); s_scroll_timer = NULL; }
  if (s_exp_timer) { app_timer_cancel(s_exp_timer); s_exp_timer = NULL; }
#if defined(PBL_TOUCH)
  pull_deinit(&s_pull);
#endif
  // the photo's memory goes back to Now Playing; the phone sends it again with the next bio
  photo_free();
  send_cmd(CMD_GET_BIO, 1, 0, NULL);  // 1: the bio closed, so the phone stops sending its updates (phone 195+)
  layer_destroy(s_mid); s_mid = NULL;
  layer_destroy(s_body); s_body = NULL;
  layer_destroy(s_layer); s_layer = NULL;
  layer_destroy(s_over); s_over = NULL;
  layer_destroy(s_wrap); s_wrap = NULL;
  window_destroy(w);
  s_win = NULL;
}

static bool s_quiet;
// from Now Playing's pull: the card already slid in, so no second slide
void bio_push_quiet(void) {
  s_quiet = true;
  bio_push();
  s_quiet = false;
}

void bio_push(void) {
  if (s_win) return;
  s_win = window_create();
  window_set_background_color(s_win, T.bg);
  window_set_window_handlers(s_win, (WindowHandlers){ .load = load, .unload = unload, .appear = appear, .disappear = disappear });
  window_stack_push(s_win, !s_quiet);
  send_cmd(CMD_GET_BIO, 0, 0, NULL);  // once per opening (coming back from thumbs down must not reset the verdict)
}

void bio_refresh(uint32_t what) {
  if (!s_win || !s_body) return;
  if (what & (CH_BIO | CH_THEME | CH_ART)) {
    if (what & (CH_ART | CH_BIO)) make_small();
    measure();
    place_body();
    s_scroll = min_i(s_scroll, max_scroll());
    s_scroll_to = min_i(s_scroll_to, max_scroll());
    place_mid();
    window_set_background_color(s_win, T.bg);
    layer_mark_dirty(window_get_root_layer(s_win));
  }
}
