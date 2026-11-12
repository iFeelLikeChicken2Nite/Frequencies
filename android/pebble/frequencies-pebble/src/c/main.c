// Frequencies for Pebble: app start, the first screen, the clock, the launcher glance.
#include "freq.h"

static Window *s_splash;
static Layer *s_splash_layer;
static bool s_started;
static AppTimer *s_exit_timer;
static bool s_fade_ended;

static void splash_draw(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(l);
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  int s = b.size.w / 3;
  draw_icon(ctx, IC_FREQ, GRect((b.size.w - s) / 2, b.size.h / 2 - s, s, s), T.text);
  draw_text(ctx, "Frequencies", fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD), GRect(0, b.size.h / 2 + 4, b.size.w, 30),
            GTextAlignmentCenter, T.accent);
}

static void splash_load(Window *w) {
  Layer *root = window_get_root_layer(w);
  s_splash_layer = layer_create(layer_get_bounds(root));
  layer_set_update_proc(s_splash_layer, splash_draw);
  layer_add_child(root, s_splash_layer);
}

static void splash_unload(Window *w) {
  layer_destroy(s_splash_layer);
  s_splash_layer = NULL;
  window_destroy(w);
  s_splash = NULL;
}

static bool playing_something(void) {
  return P.station[0] && (P.state == ST_PLAYING || P.state == ST_TUNING || P.state == ST_BUFFERING);
}

// Opens on Now playing if a frequency is playing, else on Saved Frequencies (user, 2026-11-09).
static void first_screen(void) {
  if (s_started) return;
  s_started = true;
  if (playing_something()) now_push(); else list_push(LIST_SAVED);
  if (s_splash) window_stack_remove(s_splash, false);
}

static void exit_app(void *d) {
  s_exit_timer = NULL;
  window_stack_pop_all(true);
}

// With "Fade to silence", the watchapp leaves for the watchface 2 minutes after the music stops (user, 2026-11-09).
static void watch_fade(void) {
  if (P.sleep_end && P.sleep_action == 0 && time(NULL) >= P.sleep_end - 1) s_fade_ended = true;
  if (P.sleep_end && time(NULL) < P.sleep_end - 1) s_fade_ended = false;
  bool quiet = P.state == ST_STOPPED || P.state == ST_PAUSED;
  if (s_fade_ended && quiet && !s_exit_timer) s_exit_timer = app_timer_register(120000, exit_app, NULL);
  if ((!quiet || !s_fade_ended) && s_exit_timer) { app_timer_cancel(s_exit_timer); s_exit_timer = NULL; }
}

void notify(uint32_t what) {
  if (what & CH_CONN) {
    if (P.lost) {
      dead_push();
    } else {
      dead_hide();
    }
  }
  if (!s_started && P.connected && (what & CH_NOW)) first_screen();
  if (what & CH_NOW) {
    if (P.sleep_end && P.sleep_action == 0 && P.state != ST_PLAYING) s_fade_ended = true;
    watch_fade();
  }
  now_refresh(what);
  menus_refresh(what);
  list_refresh(what);
  bio_refresh(what);
  lyrics_refresh(what);
  thumbs_refresh(what);
  add_refresh(what);
}

static void tick(struct tm *t, TimeUnits changed) {
  Window *top = window_stack_get_top_window();
  if (top) layer_mark_dirty(window_get_root_layer(top));
  watch_fade();
}

// The launcher's subtitle: song · artist · station (user, 2026-11-09).
static void glance_cb(AppGlanceReloadSession *session, size_t limit, void *ctx) {
  if (limit < 1) return;
  char sub[120] = "";
  const char *parts[3] = { P.song, P.artist, P.station };
  for (int i = 0; i < 3; i++) {
    if (!parts[i][0]) continue;
    if (sub[0]) strncat(sub, " · ", sizeof sub - strlen(sub) - 1);
    strncat(sub, parts[i], sizeof sub - strlen(sub) - 1);
  }
  if (!sub[0]) return;
  AppGlanceSlice slice = {
    .layout = { .icon = APP_GLANCE_SLICE_DEFAULT_ICON, .subtitle_template_string = sub },
    .expiration_time = APP_GLANCE_SLICE_NO_EXPIRATION,
  };
  app_glance_add_slice(session, slice);
}

void glance_update(void) {
  if (!P.station[0]) {
    char last[120];
    if (persist_read_string(PK_LAST, last, sizeof last) > 0) {
      char *a = strtok(last, "\n"), *b = strtok(NULL, "\n"), *c = strtok(NULL, "\n");
      if (a) strncpy(P.song, a, sizeof P.song - 1);
      if (b) strncpy(P.artist, b, sizeof P.artist - 1);
      if (c) strncpy(P.station, c, sizeof P.station - 1);
    }
  }
  app_glance_reload(glance_cb, NULL);
}

static void init(void) {
  theme_load();
  comm_init();
#if defined(PBL_TOUCH)
  app_touch_navigation_enable(true);
#endif
  main_menu_push();
  s_splash = window_create();
  window_set_background_color(s_splash, T.bg);
  window_set_window_handlers(s_splash, (WindowHandlers){ .load = splash_load, .unload = splash_unload });
  window_stack_push(s_splash, false);
  tick_timer_service_subscribe(MINUTE_UNIT, tick);
  comm_hello();
}

static void deinit(void) {
  send_cmd(CMD_BYE, 0, 0, NULL);
  glance_update();
  tick_timer_service_unsubscribe();
  comm_deinit();
}

int main(void) {
  init();
  app_event_loop();
  deinit();
}
