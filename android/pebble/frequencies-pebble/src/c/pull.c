// Pulling a screen away with the thumb (user, 2026-11-10): the artist bio goes back down, the lyrics back up.
// The screen follows the finger and a Now Playing card shows where it's going; on lift, whichever has more than
// half the screen wins (a tie is a coin toss). Now Playing's own pull (bio up, lyrics down) is in win_now.c.
#include "freq.h"

#if defined(PBL_TOUCH)
static int screen_h(Pull *p) { return layer_get_bounds(window_get_root_layer(p->win)).size.h; }

static void place(Pull *p) {
  GRect b = layer_get_bounds(window_get_root_layer(p->win));
  layer_set_frame(p->content, GRect(0, p->drag, b.size.w, b.size.h));
  layer_set_hidden(p->card, p->drag == 0);
  // the card comes in from the side the screen is leaving
  layer_set_frame(p->card, GRect(0, p->drag > 0 ? p->drag - b.size.h : b.size.h + p->drag, b.size.w, b.size.h));
}

static void draw_card(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  bool big = is_large();
  bool from_top = layer_get_frame(l).origin.y < 0;
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  graphics_context_set_fill_color(ctx, T.accent);
  graphics_fill_rect(ctx, GRect(0, from_top ? b.size.h - 2 : 0, b.size.w, 2), 0, GCornerNone);
  int w = b.size.w - 16, is = big ? 28 : 22;
  // what shows first as the card slides in
  int y = from_top ? b.size.h - (big ? 130 : 104) : (big ? 30 : 24);
  draw_icon(ctx, IC_PLAY, GRect((b.size.w - is) / 2, y, is, is), T.accent);
  y += is + 4;
  draw_text(ctx, "Now playing", fonts_get_system_font(big ? FONT_KEY_GOTHIC_18 : FONT_KEY_GOTHIC_14), GRect(8, y - 3, w, 22),
            GTextAlignmentCenter, T.text3);
  y += big ? 20 : 16;
  const char *name = P.song[0] ? P.song : P.station;
  draw_text(ctx, name, fonts_get_system_font(big ? FONT_KEY_GOTHIC_28_BOLD : FONT_KEY_GOTHIC_24_BOLD), GRect(8, y - 4, w, big ? 64 : 52),
            GTextAlignmentCenter, T.text);
}

static void step(void *d) {
  Pull *p = d;
  p->timer = NULL;
  int left = p->to - p->drag, s = left / 3;
  if (abs(s) < 6) s = left > 0 ? min_i(left, 6) : max_i(left, -6);
  p->drag += s;
  place(p);
  if (p->drag != p->to) { p->timer = app_timer_register(30, step, p); return; }
  if (p->to) {
    p->drag = p->to = 0;
    window_stack_pop(false);  // Now Playing is underneath, already where the card showed it
    return;
  }
}

void pull_init(Pull *p, Window *w, Layer *content, int dir) {
  memset(p, 0, sizeof *p);
  p->win = w; p->content = content; p->dir = dir;
  Layer *root = window_get_root_layer(w);
  p->card = layer_create(layer_get_bounds(root));
  layer_set_update_proc(p->card, draw_card);
  layer_set_hidden(p->card, true);
  layer_add_child(root, p->card);
}

void pull_deinit(Pull *p) {
  if (p->timer) { app_timer_cancel(p->timer); p->timer = NULL; }
  if (p->card) { layer_destroy(p->card); p->card = NULL; }
}

bool pull_busy(Pull *p) { return p->timer != NULL; }

void pull_move(Pull *p, int dy) {
  int h = screen_h(p);
  // only the way back counts: down for the bio (dir 1), up for the lyrics (dir -1)
  p->drag = p->dir > 0 ? max_i(0, min_i(h, dy)) : min_i(0, max_i(-h, dy));
  place(p);
}

void pull_release(Pull *p) {
  int h = screen_h(p), a = abs(p->drag);
  bool wins = a * 2 > h || (a && a * 2 == h && (rand() & 1));
  p->to = wins ? (p->drag > 0 ? h : -h) : 0;
  if (!p->timer) p->timer = app_timer_register(16, step, p);
}
#endif
