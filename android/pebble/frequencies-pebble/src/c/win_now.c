// Now playing: normal, picture grown, ad covered, nothing playing, not saved yet; hold rings; touch.
#include "freq.h"

static Window *s_win;
static Layer *s_layer;
static Hold s_hold;
static GBitmap *s_shrug;
static AppTimer *s_anim, *s_idle, *s_second, *s_swap;
static AppTimer *s_shuf_timer;
static int s_shuf = -1;      // the shuffle arrows, 0 parallel (off) .. 1000 crossed (on); -1 = not drawn yet
static bool s_flip;          // expanded view, both names: showing the station right now
static int s_grow;          // 0..1000, how far the picture has grown (eased)
static int s_t;             // 0..1000, how far the animation has run (linear)
static int s_grow_to;
static uint32_t s_anim_last;
static time_t s_touched;    // last press or touch
static int s_bars;
// touch
static GRect s_star_r, s_shuffle_r, s_pic_r, s_save_r;
#if defined(PBL_TOUCH)
static GPoint s_t0, s_t1;
static bool s_tracking;
#endif
// pulling the bio up / the lyrics down (user, 2026-11-10): the card follows the thumb; on lift, whichever has more
// than half the screen wins (a tie is a coin toss)
static Layer *s_card;
static int s_drag;            // px: < 0 the bio card is coming up from below, > 0 the lyrics card down from above
static int s_drag_to;         // where the settle animation is heading (0, -h or +h)
static bool s_dragging;
static AppTimer *s_settle;
// geometry, both views (big screens / small screens)
#define A0 (big ? 100 : 56)     // picture, normal
#define A1 (big ? 144 : 96)     // picture, grown (as big as fits between the button icons)
#define AY0 (big ? 47 : 36)
#define AY1 (big ? 26 : 20)

static bool nothing(void) { return !P.station[0] || P.state == ST_STOPPED; }
static bool ad_on(void) { return P.ad_end && time(NULL) < P.ad_end; }
static int lerp(int a, int b, int g) { return a + (b - a) * g / 1000; }
static bool unsaved(void) { return P.station[0] && !(P.flags & FL_SAVED); }

// Saving (user, 2026-11-10): the + turns into a green check and "Save" into "Saved!"; 10 s later the row goes away.
static uint32_t s_saved_at;      // when the check started (0: not showing)
static AppTimer *s_saved_timer;
#define SAVED_MORPH 450
#define SAVED_SHOW 10000
static int saved_t(void) {       // -1 not showing, else 0..1000 through the change
  if (!s_saved_at) return -1;
  int32_t d = (int32_t)(now_ms() - s_saved_at);
  if (d < 0) return 0;
  if (d >= SAVED_SHOW) return -1;
  return min_i(1000, d * 1000 / SAVED_MORPH);
}
static void saved_tick(void *x) {
  s_saved_timer = NULL;
  int t = saved_t();
  if (t < 0) s_saved_at = 0;
  else {
    int32_t d = (int32_t)(now_ms() - s_saved_at);
    s_saved_timer = app_timer_register(t < 1000 ? 33 : (uint32_t)max_i(50, SAVED_SHOW - d), saved_tick, NULL);
  }
  if (s_layer) layer_mark_dirty(s_layer);
}
// delay_ms lets the change play after a menu closes
void now_saved(int delay_ms) {
  P.flags |= FL_SAVED;
  s_saved_at = now_ms() + delay_ms;
  if (!s_saved_at) s_saved_at = 1;
  if (s_saved_timer) app_timer_cancel(s_saved_timer);
  s_saved_timer = app_timer_register(max_i(16, delay_ms), saved_tick, NULL);
}
static GColor saved_green(void) {
#if defined(PBL_COLOR)
  uint8_t a = T.bg.argb;
  int light = ((a >> 4) & 3) + ((a >> 2) & 3) + (a & 3);
  return light >= 6 ? GColorIslamicGreen : GColorGreen;
#else
  return T.text;
#endif
}
static GPoint pt_lerp(GPoint a, GPoint b, int t) { return GPoint(lerp(a.x, b.x, t), lerp(a.y, b.y, t)); }
static void draw_save_mark(GContext *ctx, GPoint c, int r, int t) {
  GColor col = t >= 500 ? saved_green() : T.accent;
  graphics_context_set_stroke_color(ctx, col);
  graphics_context_set_stroke_width(ctx, 2);
  graphics_draw_circle(ctx, c, r);
  if (t < 500) {
    // the + draws in to nothing
    int h = (r - 4) * (500 - t) / 500;
    if (h > 0) {
      graphics_draw_line(ctx, GPoint(c.x - h, c.y), GPoint(c.x + h, c.y));
      graphics_draw_line(ctx, GPoint(c.x, c.y - h), GPoint(c.x, c.y + h));
    }
    return;
  }
  // then the check draws itself: the short stroke, then the long one
  int p = (t - 500) * 2;
  GPoint a = GPoint(c.x - r * 45 / 100, c.y + r * 2 / 100), m = GPoint(c.x - r * 12 / 100, c.y + r * 38 / 100),
         e = GPoint(c.x + r * 50 / 100, c.y - r * 36 / 100);
  if (p < 350) { graphics_draw_line(ctx, a, pt_lerp(a, m, p * 1000 / 350)); return; }
  graphics_draw_line(ctx, a, m);
  graphics_draw_line(ctx, m, pt_lerp(m, e, (p - 350) * 1000 / 650));
}

static void schedule(void);

// Smooth grow and shrink (user, round 2): about 30 frames a second, timed by the clock, eased at both ends.
// Growing takes 0.9 s ("slowly"), shrinking 0.45 s so a press answers quickly.
static int ease(int t) { return t * t / 1000 * (3000 - 2 * t) / 1000; }

static void anim_step(void *d) {
  s_anim = NULL;
  uint32_t now = now_ms();
  int dt = max_i(1, (int)(now - s_anim_last));
  s_anim_last = now;
  if (s_t < s_grow_to) s_t = min_i(s_grow_to, s_t + max_i(1, dt * 1000 / 900));
  else if (s_t > s_grow_to) s_t = max_i(s_grow_to, s_t - max_i(1, dt * 1000 / 450));
  s_grow = ease(s_t);
  if (s_layer) layer_mark_dirty(s_layer);
  if (s_t != s_grow_to) s_anim = app_timer_register(33, anim_step, NULL);
}

static void grow_to(int v) {
  s_grow_to = v;
  if (!s_anim && s_t != s_grow_to) {
    s_anim_last = now_ms();
    s_anim = app_timer_register(33, anim_step, NULL);
  }
}

static bool may_grow(void) {
  return P.prefs[PREF_GROW] && P.cover && !ad_on() && !unsaved() && saved_t() < 0 && !nothing();
}

static void idle_check(void *d) {
  s_idle = NULL;
  if (!may_grow()) return;
  time_t since = max_i((int)s_touched, (int)P.cover_at);
  if (time(NULL) - since >= 5) grow_to(1000);
  else s_idle = app_timer_register(1000 * (5 - (time(NULL) - since)) + 50, idle_check, NULL);
}

static void touched(void) {
  s_touched = time(NULL);
  grow_to(0);
  schedule();
}

static void shuf_tick(void *d) {
  s_shuf_timer = NULL;
  int to = (P.flags & FL_SHUFFLE) ? 1000 : 0;
  s_shuf = to > s_shuf ? min_i(to, s_shuf + 200) : max_i(to, s_shuf - 200);  // about 170 ms, like the desktop
  if (s_shuf != to) s_shuf_timer = app_timer_register(33, shuf_tick, NULL);
  if (s_layer) layer_mark_dirty(s_layer);
}

static void swap_tick(void *d) {
  s_swap = NULL;
  s_flip = !s_flip;
  if (s_layer) layer_mark_dirty(s_layer);
}

static void second_tick(void *d) {
  s_second = NULL;
  s_bars++;
  if (s_layer) layer_mark_dirty(s_layer);
  if (ad_on()) s_second = app_timer_register(1000, second_tick, NULL);
}

static void schedule(void) {
  if (!s_win) return;
  if (s_idle) { app_timer_cancel(s_idle); s_idle = NULL; }
  if (may_grow()) s_idle = app_timer_register(500, idle_check, NULL);
  else grow_to(0);
  if (ad_on() && !s_second) s_second = app_timer_register(1000, second_tick, NULL);
}

// ---- drawing ----
static void sleep_label(char *buf, int n) {
  if (!P.sleep_end) { buf[0] = 0; return; }
  int left = (int)(P.sleep_end - time(NULL));
  if (left < 0) left = 0;
  int m = (left + 59) / 60;
  if (m >= 60) snprintf(buf, n, "%dh%02d", m / 60, m % 60);
  else snprintf(buf, n, "%dm", m);
}

static void draw_nothing(GContext *ctx, GRect b, int cw) {
  bool big = b.size.w >= 180;
  draw_text(ctx, "Nothing playing", fonts_get_system_font(big ? FONT_KEY_GOTHIC_28_BOLD : FONT_KEY_GOTHIC_24_BOLD),
            GRect(4, big ? 24 : 18, cw - 6, 34), GTextAlignmentCenter, T.text);
  if (s_shrug) {
    GColor *pal = gbitmap_get_palette(s_shrug);
    if (pal) { pal[0] = GColorClear; pal[1] = T.accent; }
    GRect sb = gbitmap_get_bounds(s_shrug);
    int w = min_i(sb.size.w, cw - 8);
    graphics_context_set_compositing_mode(ctx, GCompOpSet);
    graphics_draw_bitmap_in_rect(ctx, s_shrug, GRect((cw + 2 - w) / 2, b.size.h * 40 / 100 - sb.size.h / 2, w, sb.size.h));
    graphics_context_set_compositing_mode(ctx, GCompOpAssign);
  }
  draw_text(ctx, "Select plays a random frequency", fonts_get_system_font(big ? FONT_KEY_GOTHIC_18 : FONT_KEY_GOTHIC_14),
            GRect(8, b.size.h * (big ? 61 : 60) / 100, cw - 14, 44), GTextAlignmentCenter, T.text2);
}

static void draw_song_block(GContext *ctx, GRect area, bool grown) {
  bool big = is_large();
  bool ad = ad_on();
  const char *song = ad ? "Covering an ad" : (P.song[0] ? P.song : P.station);
  GFont sf = fonts_get_system_font(grown ? (big ? FONT_KEY_GOTHIC_24_BOLD : FONT_KEY_GOTHIC_14_BOLD)
                                         : (big ? FONT_KEY_GOTHIC_24_BOLD : FONT_KEY_GOTHIC_18_BOLD));
  int lh = grown ? (big ? 24 : 16) : (big ? 26 : 20);
  int lines = grown ? 1 : (text_h(song, sf, area.size.w) > lh + 4 ? 2 : 1);
  if (lines == 2 && area.size.h < lh * 2 + 20) lines = 1;
  draw_text(ctx, song, sf, GRect(area.origin.x, area.origin.y - 3, area.size.w, lh * lines + 2), GTextAlignmentCenter,
            ad ? T.accent : T.text);
  int y = area.origin.y + lh * lines + 1;
  if (ad) {
    char buf[32];
    int left = max_i(0, (int)(P.ad_end - time(NULL)));
    snprintf(buf, sizeof buf, "Music's back in %ds", left);
    draw_text(ctx, buf, fonts_get_system_font(FONT_KEY_GOTHIC_18), GRect(area.origin.x, y - 2, area.size.w, 22), GTextAlignmentCenter, T.text2);
  } else if (!grown && P.logo && (P.flags & FL_LOGO)) {
    // band logos never get small: the logo only in the normal view, the artist's name when grown (user, 2026-11-10)
    draw_logo(ctx, P.logo, GRect(area.origin.x + 4, y + 1, area.size.w - 8, big ? 28 : 18), T.text2);
  } else if (grown) {
    // expanded: the artist and the station share one line and swap every few seconds (user, 2026-11-10);
    // Settings can pick just one of them, which then stays put. The station is in the accent, like at the top.
    bool station = P.name_mode == 2 || (P.name_mode == 0 && (s_flip || !P.artist[0]));
    const char *name = station ? P.station : P.artist;
    draw_text(ctx, name, fonts_get_system_font(big ? FONT_KEY_GOTHIC_18 : FONT_KEY_GOTHIC_14),
              GRect(area.origin.x, y - 2, area.size.w, big ? 22 : 18), GTextAlignmentCenter, station ? T.accent : T.text2);
  } else if (P.artist[0]) {
    draw_text(ctx, P.artist, fonts_get_system_font(FONT_KEY_GOTHIC_18), GRect(area.origin.x, y - 2, area.size.w, 22),
              GTextAlignmentCenter, T.text2);
  }
}



static void draw_picture(GContext *ctx, GRect r) {
  if (!P.cover) {
    graphics_context_set_fill_color(ctx, T.stripe);
    graphics_fill_rect(ctx, r, 0, GCornerNone);
    draw_icon(ctx, IC_RADIO, GRect(r.origin.x + r.size.w / 4, r.origin.y + r.size.h / 4, r.size.w / 2, r.size.h / 2), T.text3);
    return;
  }
  // the shrunk copy at the normal size (averaged, sharper), the phone's own pixels when grown
  GBitmap *bm = P.cover;
  if (P.cover_small && gbitmap_get_bounds(P.cover_small).size.w == r.size.w) bm = P.cover_small;
  draw_bitmap_scaled(ctx, bm, r);
}

static void update(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  bool big = b.size.w >= 180;
  int ew = big ? 26 : 20, cw = b.size.w - ew;
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  s_star_r = s_shuffle_r = s_pic_r = s_save_r = GRectZero;
  // status bar: the clock (the same size as every other clock), a dotted line under it
  int line_y = big ? 22 : 17;
  draw_clock(ctx, GRect(0, big ? -5 : -4, b.size.w, 30), GTextAlignmentCenter, T.text2, false);
  draw_dotted_hline(ctx, 2, b.size.w - 2, line_y, T.text3);

  int icons[3];
  GColor cols[3] = { T.text2, T.text2, T.text2 };
  if (nothing()) {
    draw_nothing(ctx, b, cw);
    icons[0] = IC_LIST; icons[1] = IC_DICE; icons[2] = IC_TAG;
    draw_edge_icons(ctx, b, icons, cols);
    return;
  }
  bool ad = ad_on();
  int g = s_grow;
  char sl[12];
  sleep_label(sl, sizeof sl);
  GFont sf = fonts_get_system_font(big ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_14_BOLD);
  int st_y = big ? 21 : 16;
  // station name over the picture's column in the normal view; when grown it moves down beside the artist's name
  if (g < 500) draw_text(ctx, P.station, sf, GRect(lerp(4, ew, g), st_y, lerp(cw - 6, b.size.w - 2 * ew, g), 22),
                         GTextAlignmentCenter, T.accent);
  int A = lerp(A0, A1, g), ay = lerp(AY0, AY1, g);
  s_pic_r = GRect(lerp(4, (b.size.w - A1) / 2, g), ay, A, A);
  draw_picture(ctx, s_pic_r);
  // the song under the picture moves with it
  GRect song0 = GRect(4, AY0 + A0 + (big ? 3 : 2), cw - 6, b.size.h - AY0 - A0 - 6);
  GRect song1 = GRect(ew, AY1 + A1 + 1, b.size.w - 2 * ew, b.size.h - AY1 - A1 - 3);
  draw_song_block(ctx, GRect(lerp(song0.origin.x, song1.origin.x, g), lerp(song0.origin.y, song1.origin.y, g),
                             lerp(song0.size.w, song1.size.w, g), lerp(song0.size.h, song1.size.h, g)), g >= 500);

  if (g >= 1000 && P.name_mode == 0 && P.artist[0] && !ad) {
    if (!s_swap) s_swap = app_timer_register(P.name_secs * 1000, swap_tick, NULL);
  } else {
    if (s_swap) { app_timer_cancel(s_swap); s_swap = NULL; }
    if (g < 1000) s_flip = false;  // a new grow starts on the artist
  }
  if (g >= 1000) {
    // grown: no star or shuffle (user, round 2); the sleep timer moves up beside the clock
    if (sl[0]) {
      int is = big ? 14 : 11;
      draw_icon(ctx, IC_MOON, GRect(4, big ? 4 : 2, is, is), T.text2);
      draw_text(ctx, sl, clock_font(), GRect(4 + is + 3, big ? -5 : -4, 60, 30), GTextAlignmentLeft, T.text2);
    }
  } else if (g == 0) {
    // normal: beside the picture, a big star over a big shuffle (user, round 2); the ad, the sleep timer and Save under them
    int cx = 4 + A0 + (big ? 8 : 5), colw = cw - cx;
    int rowh = big ? 26 : 19, small_is = big ? 20 : 14;
    if (unsaved()) s_saved_at = 0;  // another station, or the save didn't go through
    int st = saved_t();
    bool save_row = (unsaved() || st >= 0) && !ad;
    int extras = (ad ? 1 : 0) + (sl[0] ? 1 : 0) + (save_row ? 1 : 0);
    int gap = big ? 6 : 4;
    int is = min_i(big ? 36 : 24, min_i(colw - 4, (A0 - extras * rowh - gap) / 2));
    int ix = cx + (colw - is) / 2, ry = AY0;
    s_star_r = GRect(0, 0, 0, 0);
    if (!(P.flags & FL_NO_STAR)) {
      s_star_r = GRect(ix - 4, ry - 2, is + 8, is + 4);
      draw_icon(ctx, (P.flags & FL_STARRED) ? IC_STAR : IC_STAR_O, GRect(ix, ry, is, is), T.accent);
      ry += is + gap;
    }
    s_shuffle_r = GRect(ix - 4, ry - 2, is + 8, is + 4);
    int to = (P.flags & FL_SHUFFLE) ? 1000 : 0;
    if (s_shuf < 0) s_shuf = to;  // first sight: no animation
    if (s_shuf != to && !s_shuf_timer) s_shuf_timer = app_timer_register(33, shuf_tick, NULL);
    draw_shuffle(ctx, GRect(ix, ry, is, is), s_shuf, s_shuf >= 500 ? T.playing : T.text3);
    ry += is + gap;
    GFont nf = fonts_get_system_font(big ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_14_BOLD);
    int tx = cx + small_is + 4, tw = cw - tx;
    if (ad) {
      draw_icon(ctx, IC_GLASS, GRect(cx, ry + 2, small_is, small_is), T.text2);
      char buf[8];
      snprintf(buf, sizeof buf, "%ds", max_i(0, (int)(P.ad_end - time(NULL))));
      draw_text(ctx, buf, nf, GRect(tx, ry - (big ? 2 : 1), tw, 24), GTextAlignmentLeft, T.accent);
      ry += rowh;
    }
    if (sl[0]) {
      draw_icon(ctx, IC_MOON, GRect(cx, ry + 2, small_is, small_is), T.text2);
      draw_text(ctx, sl, nf, GRect(tx, ry - (big ? 2 : 1), tw, 24), GTextAlignmentLeft, T.text2);
      ry += rowh;
    }
    if (save_row) {
      // not saved yet: a + with "Save" in the accent; once saved, a green check with "Saved!" for 10 s
      GPoint c = GPoint(cx + small_is / 2, ry + 2 + small_is / 2);
      int t = st < 0 ? 0 : st;
      draw_save_mark(ctx, c, small_is / 2, t);
      const char *word = t >= 500 ? "Saved!" : "Save";
      GFont wf = nf;
      // "Saved!" is a little wider than "Save": it may run into the gap between the side icons (this row sits
      // between them), and drops a size only if even that is too narrow
      int ww = b.size.w - tx - 2;
      if (graphics_text_layout_get_content_size(word, wf, GRect(0, 0, 200, 30), GTextOverflowModeWordWrap,
                                                GTextAlignmentLeft).w > ww)
        wf = fonts_get_system_font(big ? FONT_KEY_GOTHIC_14_BOLD : FONT_KEY_GOTHIC_09);
      int dy = wf == nf ? 0 : (big ? 3 : 3);
      draw_text(ctx, word, wf, GRect(tx, ry - (big ? 2 : 1) + dy, ww, 24), GTextAlignmentLeft,
                t >= 500 ? saved_green() : T.accent);
      if (unsaved()) s_save_r = GRect(cx - 4, ry - 4, colw + 4, small_is + 8);
    }
  }
  // no scanner along the bottom any more (user, 2026-11-10)

  bool playing = P.state == ST_PLAYING || P.state == ST_TUNING || P.state == ST_BUFFERING;
  icons[0] = IC_PREV; icons[1] = playing ? IC_PAUSE : IC_PLAY; icons[2] = IC_NEXT;
  int held = -1, prog = 0;
  for (int i = 0; i < 3; i++) {
    int p = hold_progress(&s_hold, (ButtonId)(i + 1));
    if (p > 0) { held = i; prog = p; icons[i] = -1; }
  }
  if (held == 0 && (P.flags & FL_NO_STAR)) { icons[0] = IC_PREV; held = -1; }   // no star to hold for
  draw_edge_icons(ctx, b, icons, cols);
  if (held >= 0) {
    static const int HI[3] = { IC_STAR, IC_DOTS, IC_SHUFFLE };
    GColor hc[3] = { T.accent, T.text, T.playing };
    draw_hold_ring(ctx, b, held, HI[held], hc[held], prog);
  }
}

// ---- buttons ----
static void on_click(Hold *h, ButtonId b) {
  bool was_grown = s_grow > 0;
  touched();
  if (nothing()) {
    if (b == BUTTON_ID_UP) list_push(LIST_SAVED);
    else if (b == BUTTON_ID_SELECT) send_cmd(CMD_PLAY_RANDOM, 0, 0, NULL);
    else if (b == BUTTON_ID_DOWN) list_push(LIST_TAGS);
    return;
  }
  (void)was_grown;
  if (b == BUTTON_ID_UP) send_cmd(CMD_PREV, 0, 0, NULL);
  else if (b == BUTTON_ID_SELECT) send_cmd(CMD_PLAY_PAUSE, 0, 0, NULL);
  else if (b == BUTTON_ID_DOWN) send_cmd(CMD_NEXT, 0, 0, NULL);
}

static void on_hold(Hold *h, ButtonId b) {
  touched();
  if (nothing()) return;
  if (b == BUTTON_ID_UP) { if (!(P.flags & FL_NO_STAR)) { P.flags ^= FL_STARRED; send_cmd(CMD_STAR, 0, 0, NULL); } }
  else if (b == BUTTON_ID_DOWN) { P.flags ^= FL_SHUFFLE; send_cmd(CMD_SHUFFLE, 0, 0, NULL); }
  else if (b == BUTTON_ID_SELECT) actions_push();
  layer_mark_dirty(s_layer);
}

static void back_click(ClickRecognizerRef r, void *ctx) {
  back_to_main();
}

static void click_config(void *ctx) {
  hold_click_config(ctx);
  window_single_click_subscribe(BUTTON_ID_BACK, back_click);
}

// ---- the cards that follow the thumb ----
static void place(void) {
  if (!s_win) return;
  GRect b = layer_get_bounds(window_get_root_layer(s_win));
  int h = b.size.h;
  layer_set_frame(s_layer, GRect(0, s_drag, b.size.w, h));
  layer_set_hidden(s_card, s_drag == 0);
  layer_set_frame(s_card, GRect(0, s_drag < 0 ? h + s_drag : s_drag - h, b.size.w, h));
  layer_mark_dirty(s_card);
}

static void draw_card(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  bool big = is_large();
  bool bio = s_drag < 0 || s_drag_to < 0;
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  // the edge that meets Now Playing, lit in the accent
  graphics_context_set_fill_color(ctx, T.accent);
  graphics_fill_rect(ctx, GRect(0, bio ? 0 : b.size.h - 2, b.size.w, 2), 0, GCornerNone);
  int w = b.size.w - 16, is = big ? 28 : 22;
  int y = bio ? (big ? 30 : 24) : b.size.h - (big ? 130 : 104);  // what shows first as the card slides in
  draw_icon(ctx, bio ? IC_PERSON : IC_LYRICS, GRect((b.size.w - is) / 2, y, is, is), T.accent);
  y += is + 4;
  draw_text(ctx, bio ? "Artist bio" : "Lyrics", fonts_get_system_font(big ? FONT_KEY_GOTHIC_18 : FONT_KEY_GOTHIC_14),
            GRect(8, y - 3, w, 22), GTextAlignmentCenter, T.text3);
  y += big ? 20 : 16;
  const char *name = bio ? (P.artist[0] ? P.artist : P.station) : (P.song[0] ? P.song : P.station);
  GFont f = fonts_get_system_font(big ? FONT_KEY_GOTHIC_28_BOLD : FONT_KEY_GOTHIC_24_BOLD);
  draw_text(ctx, name, f, GRect(8, y - 4, w, big ? 64 : 52), GTextAlignmentCenter, bio ? T.text : T.accent);
}

#if defined(PBL_TOUCH)
static void settle_step(void *d) {
  s_settle = NULL;
  int left = s_drag_to - s_drag;
  int step = left / 3;
  if (abs(step) < 6) step = left > 0 ? min_i(left, 6) : max_i(left, -6);  // eased, quick at the end
  s_drag += step;
  if (s_drag != s_drag_to) {
    place();
    s_settle = app_timer_register(30, settle_step, NULL);
    return;
  }
  int to = s_drag_to;
  s_drag = s_drag_to = 0;
  place();
  // the card has the whole screen: the real screen takes its place without a second slide
  if (to < 0) bio_push_quiet();
  else if (to > 0) lyrics_push_quiet();
}

static void settle(int to) {
  s_drag_to = to;
  if (!s_settle) s_settle = app_timer_register(16, settle_step, NULL);
}
#endif

static void drag_reset(void) {
  if (s_settle) { app_timer_cancel(s_settle); s_settle = NULL; }
  s_drag = s_drag_to = 0;
  s_dragging = false;
  if (s_card) place();
}

// ---- touch (Time 2): tap star/shuffle/picture/+, pull up the bio, pull down the lyrics ----
#if defined(PBL_TOUCH)
static bool in(GRect r, GPoint p) { return r.size.w && grect_contains_point(&r, &p); }


static void touch(const TouchEvent *e, void *ctx) {
  GPoint p = GPoint(e->x, e->y);
  input_seen();
  if (edge_back(e)) { s_tracking = false; back_to_main(); return; }
  if (edge_tracking()) { s_tracking = false; return; }
  switch (e->type) {
    case TouchEvent_Touchdown:
      if (s_settle) { s_tracking = false; break; }  // still settling from the last pull
      s_t0 = s_t1 = p; s_tracking = true; s_dragging = false;
      break;
    case TouchEvent_PositionUpdate: {
      if (!s_tracking) break;
      s_t1 = p;
      int dx = s_t1.x - s_t0.x, dy = s_t1.y - s_t0.y;
      if (!s_dragging && !nothing() && abs(dy) > 8 && abs(dy) > abs(dx)) s_dragging = true;
      if (s_dragging) {
        int h = layer_get_bounds(window_get_root_layer(s_win)).size.h;
        s_drag = max_i(-h, min_i(h, dy));
        place();
      }
      break;
    }
    case TouchEvent_Liftoff: {
      if (!s_tracking) break;
      s_tracking = false;
      s_t1 = p;
      int dx = s_t1.x - s_t0.x, dy = s_t1.y - s_t0.y;
      touched();
      if (s_dragging) {
        s_dragging = false;
        int h = layer_get_bounds(window_get_root_layer(s_win)).size.h;
        int a = abs(s_drag), sign = s_drag < 0 ? -1 : 1;
        bool wins = a * 2 > h || (a * 2 == h && (rand() & 1));
        settle(wins && a ? sign * h : 0);
        break;
      }
      if (abs(dx) < 10 && abs(dy) < 10) {
        if (nothing()) break;
        if (in(s_save_r, p)) { send_cmd(CMD_SAVE, 0, 0, NULL); now_saved(0); }
        else if (in(s_star_r, p)) { P.flags ^= FL_STARRED; send_cmd(CMD_STAR, 0, 0, NULL); }
        else if (in(s_shuffle_r, p)) { P.flags ^= FL_SHUFFLE; send_cmd(CMD_SHUFFLE, 0, 0, NULL); }
        else if (in(s_pic_r, p)) { if (s_grow == 0 && may_grow()) { s_touched = 0; grow_to(1000); } }
        layer_mark_dirty(s_layer);
      }
      break;
    }
  }
}
#endif

#if defined(PBL_TOUCH)
static void hint_later(void *d) {
  // once per run: Back on the hint leaves it for next time instead of bringing it straight back
  static bool shown;
  if (shown || swipe_hint_seen() || !s_win || window_stack_get_top_window() != s_win || nothing()) return;
  shown = true;
  swipe_hint_push();
}
#endif

static void appear(Window *w) {
  // the bio borrows the shrunk cover's memory for its photo: make it again
  if (P.cover && !P.cover_small) P.cover_small = shrink_bitmap(P.cover, is_large() ? 100 : 56);
#if defined(PBL_TOUCH)
  if (touch_service_is_enabled()) {
    touch_service_subscribe(touch, NULL);
    // the first time, what the swipes do (user, 2026-11-10); a moment after the screen shows
    if (!swipe_hint_seen()) app_timer_register(600, hint_later, NULL);
  }
#endif
  s_touched = time(NULL);
  schedule();
#ifdef DRAG_TEST
  s_drag = DRAG_TEST; place();
#endif
}

static void disappear(Window *w) {
#if defined(PBL_TOUCH)
  touch_service_unsubscribe();
#endif
  hold_cancel(&s_hold);
  if (s_idle) { app_timer_cancel(s_idle); s_idle = NULL; }
  if (s_anim) { app_timer_cancel(s_anim); s_anim = NULL; }
  if (s_swap) { app_timer_cancel(s_swap); s_swap = NULL; }
  if (s_shuf_timer) { app_timer_cancel(s_shuf_timer); s_shuf_timer = NULL; }
  if (s_settle && s_drag_to == 0) drag_reset();
  s_shuf = -1;
  s_flip = false;
  s_grow = s_grow_to = s_t = 0;
}

static void load(Window *w) {
  Layer *root = window_get_root_layer(w);
  s_layer = layer_create(layer_get_bounds(root));
  layer_set_update_proc(s_layer, update);
  layer_add_child(root, s_layer);
  s_card = layer_create(layer_get_bounds(root));
  layer_set_update_proc(s_card, draw_card);
  layer_set_hidden(s_card, true);
  layer_add_child(root, s_card);
  s_shrug = gbitmap_create_with_resource(RESOURCE_ID_SHRUG);
  memset(&s_hold, 0, sizeof s_hold);
  s_hold.layer = s_layer;
  s_hold.hold_ms[BUTTON_ID_UP] = s_hold.hold_ms[BUTTON_ID_SELECT] = s_hold.hold_ms[BUTTON_ID_DOWN] = HOLD_MS;
  s_hold.on_click = on_click;
  s_hold.on_hold = on_hold;
}

static void unload(Window *w) {
  hold_cancel(&s_hold);
  if (s_anim) { app_timer_cancel(s_anim); s_anim = NULL; }
  if (s_idle) { app_timer_cancel(s_idle); s_idle = NULL; }
  if (s_second) { app_timer_cancel(s_second); s_second = NULL; }
  if (s_saved_timer) { app_timer_cancel(s_saved_timer); s_saved_timer = NULL; }
  s_saved_at = 0;
  gbitmap_destroy(s_shrug); s_shrug = NULL;
  drag_reset();
  layer_destroy(s_card); s_card = NULL;
  layer_destroy(s_layer); s_layer = NULL;
  window_destroy(w);
  s_win = NULL;
}

void now_push(void) {
  if (s_win) {
    // already in the stack: go back to it
    while (window_stack_get_top_window() != s_win && window_stack_get_top_window()) window_stack_pop(false);
    return;
  }
  s_win = window_create();
  window_set_background_color(s_win, T.bg);
  window_set_window_handlers(s_win, (WindowHandlers){ .load = load, .unload = unload, .appear = appear, .disappear = disappear });
  window_set_click_config_provider_with_context(s_win, click_config, &s_hold);
#if defined(PBL_TOUCH)
  window_set_touch_bridge_disabled(s_win, true);
#endif
  window_stack_push(s_win, true);
}

void now_refresh(uint32_t what) {
  if (!s_win) return;
  if (what & (CH_NOW | CH_ART | CH_THEME)) {
    window_set_background_color(s_win, T.bg);
    if (what & CH_ART) schedule();
    if (what & CH_NOW) { if (ad_on()) grow_to(0); schedule(); }
    layer_mark_dirty(s_layer);
  }
}
