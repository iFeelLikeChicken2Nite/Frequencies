// One press/hold engine: a short press acts on release, a hold acts once its ring is full,
// Up+Down together is a combo (undo a thumbs down, or "got it" on the first-time hint).
#include "freq.h"

#define TICK_MS 40
// A press on a button that also holds: let go within this long and it's a press. Longer, the ring has started
// filling and letting go early cancels the hold: nothing happens (user, 2026-11-10).
#define PRESS_MS 200


static bool any_ring(Hold *h) {
  for (int b = 1; b < 4; b++)
    if ((h->down & (1 << b)) && h->hold_ms[b] && !h->fired[b]) return true;
  return h->on_combo && (h->down & 0x0A) == 0x0A && !h->combo_fired;
}

static void tick(void *data) {
  Hold *h = data;
  h->timer = NULL;
  uint32_t t = now_ms();
  bool both = (h->down & 0x0A) == 0x0A;  // Up (1) and Down (3)
  if (both && h->on_combo && !h->combo_fired) {
    uint32_t start = h->t0[BUTTON_ID_UP] > h->t0[BUTTON_ID_DOWN] ? h->t0[BUTTON_ID_UP] : h->t0[BUTTON_ID_DOWN];
    if (t - start >= h->combo_ms) {
      h->combo_fired = true;
      h->fired[BUTTON_ID_UP] = h->fired[BUTTON_ID_DOWN] = true;
      if (h->layer) layer_mark_dirty(h->layer);
      h->on_combo(h);
      return;
    }
  } else {
    for (int b = 1; b < 4; b++) {
      if (!(h->down & (1 << b)) || !h->hold_ms[b] || h->fired[b]) continue;
      if (h->on_combo && h->combo_ms && (b == BUTTON_ID_UP || b == BUTTON_ID_DOWN)) continue;
      if (t - h->t0[b] >= h->hold_ms[b]) {
        h->fired[b] = true;
        if (h->layer) layer_mark_dirty(h->layer);
        if (h->on_hold) h->on_hold(h, (ButtonId)b);
        return;  // the handler may have pushed a window
      }
    }
  }
  if (h->layer) layer_mark_dirty(h->layer);
  if (any_ring(h)) h->timer = app_timer_register(TICK_MS, tick, h);
}

static void raw_down(ClickRecognizerRef rec, void *ctx) {
  Hold *h = ctx;
  ButtonId b = click_recognizer_get_button_id(rec);
  input_seen();
  h->down |= 1 << b;
  h->fired[b] = false;
  h->t0[b] = now_ms();
  if ((h->down & 0x0A) == 0x0A && h->on_combo) {
    h->combo_fired = false;
    if (h->combo_ms == 0) {
      h->combo_fired = true;
      h->fired[BUTTON_ID_UP] = h->fired[BUTTON_ID_DOWN] = true;
      h->on_combo(h);
      return;
    }
  }
  if (h->timer) app_timer_cancel(h->timer);
  h->timer = NULL;
  if (any_ring(h)) h->timer = app_timer_register(TICK_MS, tick, h);
  if (h->layer) layer_mark_dirty(h->layer);
}

static void raw_up(ClickRecognizerRef rec, void *ctx) {
  Hold *h = ctx;
  ButtonId b = click_recognizer_get_button_id(rec);
  bool was = h->down & (1 << b);
  h->down &= ~(1 << b);
  if (!h->down && h->timer) { app_timer_cancel(h->timer); h->timer = NULL; }
  if (h->layer) layer_mark_dirty(h->layer);
  if (!was || h->fired[b]) return;
  if (h->ignore_up) { h->ignore_up = false; return; }
  // A press that was part of an Up+Down combo in progress doesn't count as a click.
  if (h->on_combo && (b == BUTTON_ID_UP || b == BUTTON_ID_DOWN) && (h->down & 0x0A)) {
    h->fired[BUTTON_ID_UP] = h->fired[BUTTON_ID_DOWN] = true;
    return;
  }
  if (h->hold_ms[b] && now_ms() - h->t0[b] >= PRESS_MS) return;  // a hold let go early: not a press
  if (h->on_click) h->on_click(h, b);
}

void hold_click_config(void *context) {
  for (int b = 1; b < 4; b++) window_raw_click_subscribe((ButtonId)b, raw_down, raw_up, context);
}

// Select only: the screen handles Up and Down itself (the bio scrolls with them)
void hold_click_config_select(void *context) {
  window_raw_click_subscribe(BUTTON_ID_SELECT, raw_down, raw_up, context);
}

int hold_progress(Hold *h, ButtonId b) {
  if (!(h->down & (1 << b)) || !h->hold_ms[b] || h->fired[b]) return 0;
  if (h->on_combo && h->combo_ms && (h->down & 0x0A) == 0x0A) return 0;
  uint32_t el = now_ms() - h->t0[b];
  // nothing shows while it could still be a press, so a quick press doesn't flash a ring
  if (el < PRESS_MS) return 0;
  return min_i(1000, (int)(el * 1000 / h->hold_ms[b]));
}

int combo_progress(Hold *h) {
  if (!h->on_combo || (h->down & 0x0A) != 0x0A || h->combo_fired || !h->combo_ms) return 0;
  uint32_t start = h->t0[BUTTON_ID_UP] > h->t0[BUTTON_ID_DOWN] ? h->t0[BUTTON_ID_UP] : h->t0[BUTTON_ID_DOWN];
  return min_i(1000, (int)((now_ms() - start) * 1000 / h->combo_ms));
}

void hold_cancel(Hold *h) {
  if (h->timer) app_timer_cancel(h->timer);
  h->timer = NULL;
  h->down = 0;
}

// ---- swipe from the left edge, around the middle, to the center of the screen = Back (user, round 2) ----
#if defined(PBL_TOUCH)
static bool s_edge;
static int16_t s_edge_x;

bool edge_back(const TouchEvent *e) {
  Window *top = window_stack_get_top_window();
  if (!top) return false;
  GRect b = layer_get_bounds(window_get_root_layer(top));
  switch (e->type) {
    case TouchEvent_Touchdown:
      s_edge = e->x < 30 && e->y > b.size.h / 4 && e->y < b.size.h * 3 / 4;
      s_edge_x = e->x;
      return false;
    case TouchEvent_PositionUpdate:
      if (s_edge) s_edge_x = e->x;
      return false;
    case TouchEvent_Liftoff: {
      if (!s_edge) return false;
      s_edge = false;
      int x = e->x ? e->x : s_edge_x;
      return max_i(x, s_edge_x) >= b.size.w / 2;
    }
  }
  return false;
}

bool edge_tracking(void) { return s_edge; }

static void plain_touch(const TouchEvent *e, void *ctx) {
  input_seen();
  if (edge_back(e)) window_stack_pop(true);
}

void edge_back_subscribe(void) {
  if (touch_service_is_enabled()) touch_service_subscribe(plain_touch, NULL);
}
#else
void edge_back_subscribe(void) {}
#endif

void touch_off(void) {
#if defined(PBL_TOUCH)
  touch_service_unsubscribe();
#endif
}
