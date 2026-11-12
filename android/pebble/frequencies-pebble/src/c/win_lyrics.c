// Lyrics: previous / current (accent) / next line, "song · artist" at the bottom.
// Up/Down earlier/later; hold Select sticks to the live line (band-aid lights up), hold again follows live.
// While stuck, Up/Down find the sung line and Select syncs to it. Lines come only while this screen is open.
#include "freq.h"

static Window *s_win;
static Layer *s_layer;
static Hold s_hold;
static AppTimer *s_marquee;
static int s_mx;          // marquee offset of the bottom line
static int s_mwait;       // pause at each end

static bool stuck(void) { return P.lyric_state == 2; }
static int edge_w(void) { return is_large() ? 24 : 20; }

static const char *line(int i) { return P.lyric[i]; }

static void bottom_text(char *buf, int n) {
  snprintf(buf, n, "%s%s%s", P.song, P.song[0] && P.artist[0] ? " · " : "", P.artist);
}

static void draw(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  bool big = is_large();
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  draw_clock(ctx, GRect(0, big ? -5 : -4, b.size.w, 30), GTextAlignmentCenter, T.text2, true);
  int w = b.size.w - edge_w() - 8;
  int x = 4;
  GFont fcur = fonts_get_system_font(big ? FONT_KEY_GOTHIC_28_BOLD : FONT_KEY_GOTHIC_24_BOLD);
  GFont fside = fonts_get_system_font(big ? FONT_KEY_GOTHIC_24 : FONT_KEY_GOTHIC_18);
  GFont fsmall = fonts_get_system_font(FONT_KEY_GOTHIC_14);
  GFont fbot = fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD);
  int top = big ? 24 : 20, bottom = b.size.h - 18;

  // song · artist on one line at the bottom (scrolls when too long)
  char bt[124];
  bottom_text(bt, sizeof bt);
  int bw = graphics_text_layout_get_content_size(bt, fbot, GRect(0, 0, 1000, 20), GTextOverflowModeWordWrap, GTextAlignmentLeft).w;
  int room = b.size.w - 8;
  if (bw <= room) draw_text(ctx, bt, fbot, GRect(4, bottom, room, 18), GTextAlignmentCenter, T.text3);
  else draw_text(ctx, bt, fbot, GRect(4 - s_mx, bottom, bw + 8, 18), GTextAlignmentLeft, T.text3);

  if (!P.lyric_state && !line(1)[0]) {
    draw_text(ctx, P.song[0] ? "No lyrics found for this song" : "Nothing playing", fside,
              GRect(x, b.size.h / 2 - 30, w, 60), GTextAlignmentCenter, T.text2);
  } else if (stuck()) {
    // current line on top, a line, then the note (earlier and next lines hidden to make room)
    const char *note = "Scrolling is paused. Select which line is correct, and press Select to sync it up.";
    int hn = text_h(note, fsmall, w);
    int ny = bottom - hn - 4;
    int hc = min_i(text_h(line(1), fcur, w), ny - 6 - top);
    draw_text(ctx, line(1), fcur, GRect(x, top + (ny - 6 - top - hc) / 2 - 4, w, hc + 4), GTextAlignmentCenter, T.accent);
    graphics_context_set_fill_color(ctx, T.stripe);
    graphics_fill_rect(ctx, GRect(0, ny - 4, b.size.w, 1), 0, GCornerNone);
    draw_text(ctx, note, fsmall, GRect(x, ny - 2, w, hn + 4), GTextAlignmentCenter, T.text2);
  } else {
    int avail = bottom - top - 4;
    int hc = min_i(text_h(line(1), fcur, w), avail);
    int hn = line(2)[0] ? text_h(line(2), fside, w) : 0;
    int hp = line(0)[0] ? text_h(line(0), fside, w) : 0;
    int gap = 4;
    if (hc + hn + hp + gap * 2 > avail && big) {
      // a long current line drops to Gothic 24 Bold so the earlier line still fits
      GFont f2 = fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD);
      int h2 = text_h(line(1), f2, w);
      if (h2 + hn + hp + gap * 2 <= avail) { fcur = f2; hc = h2; }
    }
    if (hc + hn + gap > avail) hn = max_i(0, avail - hc - gap);
    if (hc + hn + hp + gap * 2 > avail) hp = 0;   // no room: the earlier line goes first
    int total = hc + (hn ? hn + gap : 0) + (hp ? hp + gap : 0);
    int y = top + (avail - total) / 2;
    if (hp) { draw_text(ctx, line(0), fside, GRect(x, y - 3, w, hp + 3), GTextAlignmentCenter, T.text3); y += hp + gap; }
    draw_text(ctx, line(1), fcur, GRect(x, y - 4, w, hc + 4), GTextAlignmentCenter, T.accent);
    y += hc + gap;
    if (hn) draw_text(ctx, line(2), fside, GRect(x, y - 3, w, hn + 3), GTextAlignmentCenter, T.text2);
  }

  int icons[3] = { IC_UP, IC_BANDAID, IC_DOWN };
  GColor cols[3] = { T.text2, stuck() ? T.accent : T.text2, T.text2 };
  int p = hold_progress(&s_hold, BUTTON_ID_SELECT);
  if (p > 0) icons[1] = -1;
  draw_edge_icons(ctx, b, icons, cols);
  if (p > 0) draw_hold_ring(ctx, b, 1, IC_BANDAID, T.accent, p);
}

static void on_click(Hold *h, ButtonId b) {
  if (b == BUTTON_ID_UP) send_cmd(CMD_LYRIC_STEP, -1, 0, NULL);
  else if (b == BUTTON_ID_DOWN) send_cmd(CMD_LYRIC_STEP, 1, 0, NULL);
  else if (b == BUTTON_ID_SELECT && stuck()) {
    send_cmd(CMD_LYRIC_SYNC, 0, 0, NULL);
    P.lyric_state = 1;
  }
  layer_mark_dirty(s_layer);
}

static void on_hold(Hold *h, ButtonId b) {
  if (b != BUTTON_ID_SELECT) return;
  P.lyric_state = stuck() ? 1 : 2;
  send_cmd(CMD_LYRIC_HOLD, P.lyric_state == 2, 0, NULL);
  layer_mark_dirty(s_layer);
}

#if defined(PBL_TOUCH)
// pull up to go back to Now Playing (user, 2026-11-10): the lyrics follow the thumb, more than half wins
static Pull s_pull;
static GPoint s_t0;
static bool s_tracking, s_pulling;

static void touch(const TouchEvent *e, void *ctx) {
  input_seen();
  if (edge_back(e)) { s_tracking = false; window_stack_pop(true); return; }
  if (edge_tracking()) { s_tracking = false; return; }
  GPoint p = GPoint(e->x, e->y);
  if (e->type == TouchEvent_Touchdown) {
    if (pull_busy(&s_pull)) return;
    s_t0 = p; s_tracking = true; s_pulling = false;
    return;
  }
  if (!s_tracking) return;
  int dx = p.x - s_t0.x, dy = p.y - s_t0.y;
  if (e->type == TouchEvent_PositionUpdate) {
    if (!s_pulling && dy < -8 && -dy > abs(dx)) s_pulling = true;
    if (s_pulling) pull_move(&s_pull, dy);
    return;
  }
  if (e->type == TouchEvent_Liftoff) {
    s_tracking = false;
    if (s_pulling) pull_release(&s_pull);
  }
}
#endif

static void marquee(void *d) {
  s_marquee = NULL;
  if (!s_layer) return;
  char bt[124];
  bottom_text(bt, sizeof bt);
  GRect b = layer_get_bounds(s_layer);
  int bw = graphics_text_layout_get_content_size(bt, fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD), GRect(0, 0, 1000, 20),
                                                 GTextOverflowModeWordWrap, GTextAlignmentLeft).w;
  int over = bw - (b.size.w - 8);
  if (over <= 0) { s_mx = 0; s_marquee = app_timer_register(2000, marquee, NULL); return; }
  if (s_mwait > 0) s_mwait--;
  else if (s_mx >= over) { s_mx = 0; s_mwait = 8; }
  else { s_mx = min_i(over, s_mx + 3); if (s_mx >= over) s_mwait = 8; }
  layer_mark_dirty(s_layer);
  s_marquee = app_timer_register(150, marquee, NULL);
}

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
  pull_init(&s_pull, w, s_layer, -1);
#endif
}

static void appear(Window *w) {
  send_cmd(CMD_LYRICS, 1, 0, NULL);  // start sending lines
  s_mx = 0; s_mwait = 8;
  if (!s_marquee) s_marquee = app_timer_register(1000, marquee, NULL);
#if defined(PBL_TOUCH)
  if (touch_service_is_enabled()) touch_service_subscribe(touch, NULL);
#endif
}

static void disappear(Window *w) {
  send_cmd(CMD_LYRICS, 0, 0, NULL);  // stop
  touch_off();
  hold_cancel(&s_hold);
  if (s_marquee) { app_timer_cancel(s_marquee); s_marquee = NULL; }
}

static void unload(Window *w) {
#if defined(PBL_TOUCH)
  pull_deinit(&s_pull);
#endif
  layer_destroy(s_layer); s_layer = NULL;
  window_destroy(w);
  s_win = NULL;
}

static bool s_quiet;
// from Now Playing's pull: the card already slid in, so no second slide
void lyrics_push_quiet(void) {
  s_quiet = true;
  lyrics_push();
  s_quiet = false;
}

void lyrics_push(void) {
  if (s_win) return;
  s_win = window_create();
  window_set_background_color(s_win, T.bg);
  window_set_window_handlers(s_win, (WindowHandlers){ .load = load, .unload = unload, .appear = appear, .disappear = disappear });
  window_stack_push(s_win, !s_quiet);
}

void lyrics_refresh(uint32_t what) {
  if (s_layer && (what & (CH_LYRICS | CH_NOW | CH_THEME))) layer_mark_dirty(s_layer);
}
