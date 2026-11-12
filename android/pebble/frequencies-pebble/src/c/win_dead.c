// Can't reach Frequencies: one screen for Bluetooth off or away AND the app not answering.
// Select tries again; Back leaves the app.
#include "freq.h"

static Window *s_win;
static Layer *s_layer;
static bool s_trying;
static AppTimer *s_try_timer;

bool dead_showing(void) { return s_win != NULL; }

static void draw(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  bool big = is_large();
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  draw_clock(ctx, GRect(b.size.w / 2, big ? -5 : -4, b.size.w / 2 - 4, 30), GTextAlignmentRight, T.text2, false);
  int is = big ? 50 : 40;
  draw_icon(ctx, IC_DEADPHONE, GRect((b.size.w - is) / 2, big ? 14 : 10, is, is), T.text2);
  int y = big ? 70 : 54;
  int w = b.size.w - 12;
  GFont ft = fonts_get_system_font(big ? FONT_KEY_GOTHIC_28_BOLD : FONT_KEY_GOTHIC_24_BOLD);
  const char *t = s_trying ? "Trying again…" : "Can't reach Frequencies";
  int ht = text_h(t, ft, w);
  draw_text(ctx, t, ft, GRect(6, y, w, ht + 4), GTextAlignmentCenter, T.text);
  y += ht + 2;
  GFont fb = fonts_get_system_font(big ? FONT_KEY_GOTHIC_24 : FONT_KEY_GOTHIC_18);
  draw_text(ctx, "If this keeps happening, turn off battery optimization in Frequencies Android settings on your phone.",
            fb, GRect(6, y - 4, w, b.size.h - y + 4), GTextAlignmentCenter, T.text2);
}

static void tried(void *d) { s_try_timer = NULL; s_trying = false; if (s_layer) layer_mark_dirty(s_layer); }

static void select_click(ClickRecognizerRef r, void *ctx) {
  s_trying = true;
  layer_mark_dirty(s_layer);
  comm_hello();
  if (s_try_timer) app_timer_cancel(s_try_timer);
  s_try_timer = app_timer_register(6500, tried, NULL);
}

static void back_click(ClickRecognizerRef r, void *ctx) { window_stack_pop_all(true); }

static void click_config(void *ctx) {
  window_single_click_subscribe(BUTTON_ID_SELECT, select_click);
  window_single_click_subscribe(BUTTON_ID_BACK, back_click);
}

static void load(Window *w) {
  Layer *root = window_get_root_layer(w);
  s_layer = layer_create(layer_get_bounds(root));
  layer_set_update_proc(s_layer, draw);
  layer_add_child(root, s_layer);
  window_set_click_config_provider(w, click_config);
}

static void unload(Window *w) {
  if (s_try_timer) { app_timer_cancel(s_try_timer); s_try_timer = NULL; }
  s_trying = false;
  layer_destroy(s_layer); s_layer = NULL;
  window_destroy(w);
  s_win = NULL;
}

void dead_push(void) {
  if (s_win) { s_trying = false; layer_mark_dirty(s_layer); return; }
  s_win = window_create();
  window_set_background_color(s_win, T.bg);
  window_set_window_handlers(s_win, (WindowHandlers){ .load = load, .unload = unload });
  window_stack_push(s_win, true);
}

void dead_hide(void) {
  if (s_win) window_stack_remove(s_win, true);
}
