// Every plain menu: main menu, Actions, Sleep timer and When it ends, Thumbs down's question,
// Open on your phone, Sort, Settings. One themed MenuLayer window, filled per kind.
#include "freq.h"

enum { M_MAIN, M_ACTIONS, M_SLEEP, M_SLEEP_END, M_THUMBS, M_SERVICES, M_SORT, M_SETTINGS, M_STATION, M_ADCOVER, M_EQ };

#define MI_CHECK 0x01
#define MI_SEP 0x02
#define MI_BARS 0x04
#define MI_DIM 0x08
#define MI_GOOD 0x10
#define MI_SLIDER 0x20   // Volume: a slider under the title
#define MI_TWO 0x40      // a long title that may take two lines (the user's wording, kept whole)
#define MI_NOTE 0x80     // a small wrapped note under the list (EQ_NOTE), never highlighted

// The user's wording (2026-11-10), shown under the three bands.
static const char EQ_NOTE[] = "Looking for the classic 10 band equalizer? Toggle it in the app's settings!";

typedef struct {
  int8_t icon;
  char title[36];
  char sub[40];
  char value[10];
  uint8_t flags;
  int32_t data;
} Item;

typedef struct Menu {
  Window *win;
  MenuLayer *ml;
  Layer *head;
  int kind;
  char title[40], extra[24];
  Item items[12];
  int n;
  bool small;     // titles drop to Gothic 18 Bold when any won't fit at 24
  uint32_t data;
  char song[48];
  bool adjust;    // Select went into the Volume slider: Up/Down move it until Select
  int32_t adj;    // the item being changed with Up/Down (its data; Volume is 10)
  Layer *eq;      // Equalizer with ten bands: one vertical slider per band (user, 2026-11-10)
  int col;        // its selected column, 10 = Reset EQ
  bool down, drag;        // a finger on a slider
  GPoint t0;
  int sent_v;
  uint32_t sent_ms;
} Menu;

static Menu *s_menus[6];
static Window *s_main;
static AppTimer *s_bars_timer;
static int s_bars_phase;

static const char *SERVICES[5] = { "Apple Music", "YouTube Music", "Spotify", "Deezer", "Tidal" };
static const char *SORT_NAMES[5] = { "Manual", "Alphabetical", "Recently added", "Recently played", "Most played" };
static const char *ENDS[3] = { "Fade to silence", "Switch to another frequency", "Switch to a frequency tagged…" };
static const uint8_t SLEEP_MIN[7] = { 0, 15, 30, 45, 60, 90, 120 };
static const char *SLEEP_NAMES[7] = { "Off", "15 min", "30 min", "45 min", "1 hour", "1½ hours", "2 hours" };

static Item *add(Menu *m, int icon, const char *title, const char *sub, uint8_t flags, int32_t data) {
  if (m->n >= 12) return &m->items[11];
  Item *it = &m->items[m->n++];
  memset(it, 0, sizeof *it);
  it->icon = icon;
  strncpy(it->title, title, sizeof it->title - 1);
  if (sub) strncpy(it->sub, sub, sizeof it->sub - 1);
  it->flags = flags;
  it->data = data;
  return it;
}

static bool playing(void) { return P.station[0] && (P.state == ST_PLAYING || P.state == ST_TUNING || P.state == ST_BUFFERING); }

static void sleep_value(char *buf, int n) {
  if (!P.sleep_end) { snprintf(buf, n, "Off"); return; }
  int m = max_i(0, (int)(P.sleep_end - time(NULL)) + 59) / 60;
  if (m >= 60) snprintf(buf, n, "%dh%02d", m / 60, m % 60); else snprintf(buf, n, "%dm", m);
}

// ---- station settings (user's sketch, 2026-11-10): the playing station's own settings, kept on the phone ----
static const char *BANDS3[3] = { "Bass", "Mid", "Treble" };
static const char *BANDS10[10] = { "31 Hz", "62 Hz", "125 Hz", "250 Hz", "500 Hz", "1 kHz", "2 kHz", "4 kHz", "8 kHz", "16 kHz" };
#define D_EQ 101
#define D_ADCOVER 100
#define D_COVER_WITH 200
#define D_SAVE_FIRST 99

static void halves(char *b, int n, int h, const char *unit, bool sign) {
  int a = abs(h);
  snprintf(b, n, "%s%d%s%s", h < 0 ? "-" : (sign && h > 0 ? "+" : ""), a / 2, (a & 1) ? ".5" : "", unit);
}

void station_set(int field, int value) {
  P.st_ms = now_ms();
  send_cmd(CMD_STATION, field, value, NULL);
}

static int st_index(int32_t f) {
  if (f >= STF_EQ10 && f < STF_EQ10 + 10) return ST_EQ10 + f - STF_EQ10;
  if (f >= STF_EQ3 && f < STF_EQ3 + 3) return ST_EQ3 + f - STF_EQ3;
  return -1;
}

// A slider's top value and where it is: Volume (Actions) or an equalizer band (0..24, 12 = flat)
static bool slider_get(Menu *m, int32_t d, int *top, int *v) {
  if (m->kind == M_ACTIONS) {
    if (d != 10 || !P.vol_max) return false;
    *top = P.vol_max; *v = P.vol;
    return true;
  }
  if (d == STF_GAIN) { *top = 80; *v = P.st[ST_GAIN]; return true; }   // half dB, 40 = as it comes
  int i = st_index(d);
  if (i < 0) return false;
  *top = 24; *v = P.st[i];
  return true;
}

static bool eq_grid(Menu *m) { return m->kind == M_EQ && P.st_have && (P.st[ST_FLAGS] & SF_EQ_ADV); }
#if defined(PBL_TOUCH)
static bool on_slider(Menu *m) { int t, v; return m->adjust && slider_get(m, m->adj, &t, &v); }
#endif

// The system's touch navigation turns swipes into Up/Down; it steps aside while a slider takes the finger.
static void bridge_sync(Menu *m) {
#if defined(PBL_TOUCH)
  if (m->win) window_set_touch_bridge_disabled(m->win, m->kind == M_EQ || on_slider(m));
#endif
}

// while an item is being changed, its second line says how
static void adjusting(Menu *m, Item *it, const char *value) {
#if defined(PBL_TOUCH)
  const char *how = (it->flags & MI_SLIDER) ? "Up/Down or drag" : "Up/Down to change";
#else
  const char *how = "Up/Down to change";
#endif
  if (m->adjust && m->adj == it->data) snprintf(it->sub, sizeof it->sub, "%s%s%s", value ? value : "", value ? "  " : "", how);
}

static void fill(Menu *m) {
  m->n = 0;
  m->extra[0] = 0;
  char buf[24];
  switch (m->kind) {
    case M_MAIN:
      strcpy(m->title, "Frequencies");
      add(m, IC_PLAY, "Now playing", NULL, playing() ? MI_BARS : 0, 0);
      add(m, IC_LIST, "Saved Frequencies", NULL, 0, 1);
      add(m, IC_PLUS, "Add Frequencies", NULL, 0, 2);
      add(m, IC_TAG, "Tags", NULL, 0, 3);
      add(m, IC_CLOCK, "History", NULL, 0, 4);
      add(m, IC_GEAR, "Settings", NULL, 0, 5);
      break;
    case M_ACTIONS:
      strncpy(m->title, P.station[0] ? P.station : "Actions", sizeof m->title - 1);
      // Volume first, with a slider under it (user, round 2)
      add(m, IC_VOLUME, "Volume", NULL, MI_SLIDER, 10);
      if (P.vol_max) snprintf(m->items[m->n - 1].value, sizeof m->items[0].value, "%d%%", P.vol * 100 / P.vol_max);
      if (P.station[0] && !(P.flags & FL_SAVED)) add(m, IC_PLUS, "Save this frequency", NULL, 0, 0);
      add(m, IC_PERSON, "Artist bio", NULL, 0, 1);
      add(m, IC_LYRICS, "Lyrics", NULL, 0, 2);
      strcpy(add(m, IC_SONGID, "Song ID", NULL, 0, 3)->value, (P.flags & FL_SONG_ID) ? "On" : "Off");
      sleep_value(buf, sizeof buf);
      strcpy(add(m, IC_MOON, "Sleep timer", NULL, 0, 4)->value, buf);
      if (P.station[0]) add(m, IC_GEAR, "Station settings", NULL, 0, 5);
      break;
    case M_STATION: {
      strcpy(m->title, "Station settings");
      if (!P.st_have) { add(m, -1, "Asking your phone…", NULL, MI_DIM, -1); break; }
      bool ok = P.st[ST_FLAGS] & SF_SAVED;
      uint8_t dim = ok ? 0 : MI_DIM;
      if (!ok) add(m, IC_PLUS, "Save this frequency first", "Its settings are kept with it", 0, D_SAVE_FIRST);
      char v[24], sub[40];
      halves(v, sizeof v, P.st[ST_GAIN] - 40, " dB", true);
      if (P.st[ST_GAIN] == 40) strcpy(v, "0 dB, as it comes");
      adjusting(m, add(m, IC_VOLUME, "Main volume adjust", v, dim | MI_TWO | MI_SLIDER, STF_GAIN), v);   // a slider (user, 2026-11-11)
      if (P.st[ST_FLAGS] & SF_EQ_ADV) strcpy(sub, "10 bands");
      else snprintf(sub, sizeof sub, "Bass %+d · Mid %+d · Treble %+d", P.st[ST_EQ3] - 12, P.st[ST_EQ3 + 1] - 12, P.st[ST_EQ3 + 2] - 12);
      add(m, IC_EQ, "Equalizer", sub, dim, D_EQ);
      if (P.st[ST_FLAGS] & SF_AD_OFFERED) {
        if (P.st[ST_FLAGS] & SF_AD_ON) snprintf(sub, sizeof sub, "On, %d seconds", P.st[ST_AD_SECS]); else strcpy(sub, "Off");
        add(m, IC_GLASS, "Cover forced opening ad", sub, dim | MI_TWO, D_ADCOVER);
      }
      bool buf_on = P.st[ST_FLAGS] & SF_BUF_ON;
      char w[12];
      halves(w, sizeof w, P.st[ST_WORST], " s", false);
      if (!buf_on) strcpy(sub, "Off");
      else if (!P.st[ST_BUF]) snprintf(sub, sizeof sub, "On, auto (%s)", w);
      else { halves(v, sizeof v, P.st[ST_BUF], " s", false); snprintf(sub, sizeof sub, "On, %s", v); }
      add(m, IC_CLOCK, "Prebuffering", sub, dim, STF_BUF_ON);
      if (buf_on) {
        if (P.st[ST_BUF]) halves(v, sizeof v, P.st[ST_BUF], " seconds", false); else snprintf(v, sizeof v, "Auto (%s)", w);
        adjusting(m, add(m, -1, "Seconds ahead", v, dim, STF_BUF_SECS), v);
      }
      add(m, IC_MIC, "Talk radio", (P.st[ST_FLAGS] & SF_TALK) ? "On" : "Off", dim, STF_TALK);
      break;
    }
    case M_ADCOVER: {
      strcpy(m->title, "Opening ad");
      bool on = P.st[ST_FLAGS] & SF_AD_ON;
      add(m, IC_GLASS, "Cover ad", on ? "On" : "Off", 0, STF_AD_ON);
      char v[16];
      snprintf(v, sizeof v, "%d seconds", P.st[ST_AD_SECS]);
      adjusting(m, add(m, IC_CLOCK, "Ad break lasts…", v, on ? 0 : MI_DIM, STF_AD_SECS), v);
      add(m, IC_RADIO, "Cover it with…", (P.st[ST_FLAGS] & SF_AD_CHOSEN) && P.st_cover[0] ? P.st_cover : "Any frequency without ads",
          on ? 0 : MI_DIM, D_COVER_WITH);
      break;
    }
    case M_EQ: {
      strcpy(m->title, "Equalizer");
      bool adv = P.st[ST_FLAGS] & SF_EQ_ADV;
      for (int i = 0; i < (adv ? 10 : 3); i++) {
        int db = P.st[(adv ? ST_EQ10 : ST_EQ3) + i] - 12;
        Item *it = add(m, -1, adv ? BANDS10[i] : BANDS3[i], NULL, adv ? 0 : MI_SLIDER, (adv ? STF_EQ10 : STF_EQ3) + i);
        snprintf(it->value, sizeof it->value, db ? "%+d dB" : "0 dB", db);
        adjusting(m, it, NULL);
      }
      add(m, -1, "Reset EQ", NULL, MI_SEP, STF_EQ_RESET);
      if (!adv) add(m, -1, "", NULL, MI_NOTE, -1);
      if (m->adjust && st_index(m->adj) >= 0 && (adv != (m->adj >= STF_EQ10))) m->adjust = false;   // the phone switched modes
      if (m->eq) {
        bool g = eq_grid(m);
        layer_set_hidden(m->eq, !g);
        layer_set_hidden(menu_layer_get_layer(m->ml), g);
        layer_mark_dirty(m->eq);
      }
      break;
    }
    case M_SLEEP: {
      if (P.sleep_end) {
        int mm = max_i(0, (int)(P.sleep_end - time(NULL)) + 59) / 60;
        snprintf(m->title, sizeof m->title, "Sleeping in %d min", mm);
      } else strcpy(m->title, "Sleep timer");
      for (int i = 0; i < 7; i++) add(m, -1, SLEEP_NAMES[i], NULL, 0, i);
      add(m, IC_MOON, "When it ends", ENDS[min_i(P.sleep_action, 2)], MI_SEP, 100);
      break;
    }
    case M_SLEEP_END:
      strcpy(m->title, "When it ends");
      for (int i = 0; i < 3; i++) add(m, -1, ENDS[i], NULL, P.sleep_action == i ? MI_CHECK : 0, i);
      break;
    case M_THUMBS:
      strcpy(m->title, "What's wrong?");
      add(m, -1, "Bio", "The text is wrong", 0, 1);
      add(m, -1, "Photo", "Wrong person or picture", 0, 2);
      add(m, -1, "Both", "Bio and photo", 0, 3);
      break;
    case M_SERVICES:
      strncpy(m->title, m->song, sizeof m->title - 1);
      for (int i = 0; i < 5; i++) add(m, IC_NOTE, SERVICES[i], NULL, 0, i);
      break;
    case M_SORT: {
      strcpy(m->title, "Sort");
      if (P.sort_mode != 0) {
        bool down = P.sort_desc;
        const char *sub;
        if (P.sort_mode == 1) sub = down ? "Z to A" : "A to Z";
        else if (P.sort_mode == 4) sub = down ? "Most first" : "Least first";
        else sub = down ? "Newest first" : "Oldest first";
        add(m, down ? IC_DOWN : IC_UP, down ? "Descending" : "Ascending", sub, 0, 100);
      }
      for (int i = 0; i < 5; i++)
        add(m, -1, SORT_NAMES[i], i == 0 ? "Your order" : NULL, (P.sort_mode == i ? MI_CHECK : 0) | (i == 0 && P.sort_mode ? MI_SEP : 0), i);
      break;
    }
    case M_SETTINGS: {
      strcpy(m->title, "Settings");
      // laid out from the user's sketch (2026-11-10): the theme list holds "Match phone theme", so no Colors row
      bool follow = P.prefs[PREF_FOLLOW];
      add(m, IC_BRUSH, "Watch theme", follow ? "Match the phone" : (P.theme_name[0] ? P.theme_name : "Outrun"), 0, 1);
      add(m, IC_STAR, "Accent color", P.prefs[PREF_ACCENT] ? "The theme's accent" : "From the artwork", 0, 2);
      add(m, IC_PLAY, "Now playing art auto expand", P.prefs[PREF_GROW] ? "After 5 seconds" : "Never", MI_TWO, 3);
      // the line under the song in the expanded view (user, 2026-11-10)
      static const char *NM[3] = { "Artist and station", "Artist", "Station" };
      add(m, IC_RADIO, "Expanded view shows", NM[P.name_mode], 0, 4);
      char secs[16];
      snprintf(secs, sizeof secs, "%d seconds", P.name_secs);
      add(m, IC_SHUFFLE, "Swap names every", secs, P.name_mode ? MI_DIM : 0, 5);
      break;
    }
  }
  // Never cut off a title: the whole menu drops to Gothic 18 Bold when one won't fit at 24 (user).
  m->small = false;
  GRect b = layer_get_bounds(window_get_root_layer(m->win));
  bridge_sync(m);
  if (b.size.w < 180) { m->small = true; return; }
  GFont big = fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD);
  for (int i = 0; i < m->n; i++) {
    if (m->items[i].flags & (MI_TWO | MI_NOTE)) continue;
    int room = b.size.w - 12 - (m->items[i].icon >= 0 ? 30 : 0) - (m->items[i].value[0] ? 44 : 0) -
               ((m->items[i].flags & (MI_CHECK | MI_BARS)) ? 22 : 0);
    GSize s = graphics_text_layout_get_content_size(m->items[i].title, big, GRect(0, 0, 400, 30), GTextOverflowModeWordWrap,
                                                    GTextAlignmentLeft);
    if (s.w > room) { m->small = true; break; }
  }
}

static uint16_t rows(MenuLayer *ml, uint16_t sec, void *ctx) { return ((Menu *)ctx)->n; }

// The main menu and Actions get taller rows (user, round 2).
static bool tall(Menu *m) { return m->kind == M_MAIN || m->kind == M_ACTIONS; }
static int base_h(Menu *m) { return tall(m) ? (m->small ? 36 : 46) : (m->small ? 28 : 34); }

// MI_TWO: a second line only when the title really doesn't fit on one
static bool two_lines(Menu *m, Item *it, int w) {
  if (!(it->flags & MI_TWO)) return false;
  int x = 6 + (it->icon >= 0 ? (tall(m) ? (m->small ? 18 : 24) : (m->small ? 16 : 20)) + 10 : 0);
  GFont f = fonts_get_system_font(m->small ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_24_BOLD);
  return graphics_text_layout_get_content_size(it->title, f, GRect(0, 0, 400, 30), GTextOverflowModeWordWrap, GTextAlignmentLeft).w >
         w - x - 6;
}

static GFont note_font(Menu *m) { return fonts_get_system_font(m->small ? FONT_KEY_GOTHIC_14 : FONT_KEY_GOTHIC_18); }

static int16_t cell_h(MenuLayer *ml, MenuIndex *ix, void *ctx) {
  Menu *m = ctx;
  Item *it = &m->items[ix->row];
  if (it->flags & MI_NOTE) {
    int w = layer_get_bounds(menu_layer_get_layer(ml)).size.w - 20;
    return graphics_text_layout_get_content_size(EQ_NOTE, note_font(m), GRect(0, 0, w, 200), GTextOverflowModeWordWrap,
                                                 GTextAlignmentCenter).h + 16;
  }
  int h = base_h(m);
  if (it->sub[0]) h += m->small ? 16 : 20;
  if (it->flags & MI_SEP) h += 8;
  if (it->flags & MI_SLIDER) h += m->small ? 14 : 18;
  if (two_lines(m, it, layer_get_bounds(menu_layer_get_layer(ml)).size.w)) h += m->small ? 18 : 24;
  return h;
}

// top 0: nothing known yet, an empty track. center: the fill grows from the middle (equalizer bands).
static void draw_slider(GContext *ctx, GRect r, int v, int top, bool center, bool hi, bool adjust) {
  int h = r.size.h, y = r.origin.y;
  GColor track = hi ? GColorBlack : T.text3, fill = hi ? GColorBlack : T.accent;
  graphics_context_set_stroke_color(ctx, track);
  graphics_context_set_stroke_width(ctx, 1);
  graphics_draw_round_rect(ctx, r, h / 2);
  int fw = top ? (r.size.w - 4) * v / top : 0;
  int from = center ? (r.size.w - 4) / 2 : 0;
  graphics_context_set_fill_color(ctx, fill);
  if (center) graphics_fill_rect(ctx, GRect(r.origin.x + 1 + from, y + 1, 2, h - 2), 0, GCornerNone);   // flat mark
  int a = min_i(from, fw), w = abs(fw - from);
  if (w > 0) graphics_fill_rect(ctx, GRect(r.origin.x + 2 + a, y + 2, w, h - 4), (h - 4) / 2, GCornersAll);
  if (!top) return;
  // the knob: hollow while you're moving it
  GPoint k = GPoint(r.origin.x + 2 + fw, y + h / 2);
  int kr = h / 2 + (adjust ? 3 : 1);
  graphics_context_set_fill_color(ctx, hi ? (adjust ? T.accent : GColorBlack) : T.text);
  graphics_fill_circle(ctx, k, kr);
  if (adjust) {
    graphics_context_set_stroke_color(ctx, GColorBlack);
    graphics_context_set_stroke_width(ctx, 2);
    graphics_draw_circle(ctx, k, kr);
  }
}

static void draw_row(GContext *ctx, const Layer *cell, MenuIndex *ix, void *vctx) {
  Menu *m = vctx;
  Item *it = &m->items[ix->row];
  GRect b = layer_get_bounds(cell);
  bool hi = menu_cell_layer_is_highlighted(cell);
  if (it->flags & MI_NOTE) {
    graphics_context_set_fill_color(ctx, T.bg);
    graphics_fill_rect(ctx, b, 0, GCornerNone);
    draw_text(ctx, EQ_NOTE, note_font(m), GRect(10, 4, b.size.w - 20, b.size.h - 4), GTextAlignmentCenter, T.text2);
    return;
  }
  int top = (it->flags & MI_SEP) ? 8 : 0;
  GColor bg = hi ? T.accent : T.bg;   // no alternating rows (user, round 2)
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  if (top) {
    graphics_context_set_fill_color(ctx, T.text3);
    graphics_fill_rect(ctx, GRect(6, 3, b.size.w - 12, 1), 0, GCornerNone);
  }
  graphics_context_set_fill_color(ctx, bg);
  graphics_fill_rect(ctx, GRect(0, top, b.size.w, b.size.h - top), 0, GCornerNone);
  GColor ink = hi ? GColorBlack : ((it->flags & MI_DIM) ? T.text3 : T.text);
  GColor ink2 = hi ? GColorBlack : T.text2;
  int x = 6;
  int th = m->small ? 22 : 28;
  top += (base_h(m) - (m->small ? 28 : 34)) / 2;   // taller rows: keep the text in the middle
  if (it->icon >= 0) {
    int s = tall(m) ? (m->small ? 18 : 24) : (m->small ? 16 : 20);
    draw_icon(ctx, it->icon, GRect(x, top + (th - s) / 2 + 3, s, s), hi ? GColorBlack : (it->flags & MI_DIM ? T.text3 : T.accent));
    x += s + 10;
  }
  int right = b.size.w - 6;
  if (it->flags & MI_CHECK) {
    draw_icon(ctx, IC_CHECK, GRect(right - 16, top + (th - 16) / 2 + 3, 16, 16), hi ? GColorBlack : T.text);
    right -= 22;
  }
  if (it->flags & MI_BARS) {
    draw_bars(ctx, GRect(right - 18, top + 8, 18, th - 10), hi ? GColorBlack : T.playing, s_bars_phase);
    right -= 24;
  }
  if (it->value[0]) {
    draw_text(ctx, it->value, fonts_get_system_font(m->small ? FONT_KEY_GOTHIC_18 : FONT_KEY_GOTHIC_24),
              GRect(right - 60, top - 1, 60, th + 2), GTextAlignmentRight, ink2);
    right -= 62;
  }
  bool two = two_lines(m, it, b.size.w);
  draw_text(ctx, it->title, fonts_get_system_font(m->small ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_24_BOLD),
            GRect(x, top - (m->small ? 1 : 3), right - x, th + 4 + (two ? (m->small ? 18 : 24) : 0)),
            GTextAlignmentLeft, ink);
  if (two) top += m->small ? 18 : 24;
  if (it->flags & MI_SLIDER) {
    int sh = m->small ? 8 : 10;
    int st = 0, sv = 0;
    slider_get(m, it->data, &st, &sv);
    draw_slider(ctx, GRect(x, top + th + (m->small ? 3 : 4), b.size.w - x - 10, sh), sv, st, m->kind != M_ACTIONS, hi,
                m->adjust && m->adj == it->data);
  }
  if (it->sub[0])
    draw_text(ctx, it->sub, fonts_get_system_font(m->small ? FONT_KEY_GOTHIC_14 : FONT_KEY_GOTHIC_18),
              GRect(x, top + th - (m->small ? 3 : 4) + ((it->flags & MI_SLIDER) ? (m->small ? 14 : 18) : 0), b.size.w - x - 6,
                    m->small ? 18 : 22), GTextAlignmentLeft, ink2);
}

static void draw_head(Layer *l, GContext *ctx) {
  Menu *m = *(Menu **)layer_get_data(l);
  draw_header(ctx, layer_get_bounds(window_get_root_layer(m->win)), m->title, m->extra);
}

// ---- Equalizer with ten bands: a column per band, like a classic graphic equalizer (user, 2026-11-10) ----
static const char *EQ_LABELS[10] = { "31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k" };

typedef struct { int pad, cw, top, bot, btn; } Grid;   // in the layer's own coordinates

static Grid grid_geo(GRect b) {
  Grid g;
  g.pad = PBL_IF_ROUND_ELSE(24, 4);
  g.cw = (b.size.w - 2 * g.pad) / 10;
  g.pad = (b.size.w - 10 * g.cw) / 2;
  g.top = 34;
  g.btn = b.size.h - PBL_IF_ROUND_ELSE(40, 30);
  g.bot = g.btn - 36;   // the labels take two staggered lines under the columns
  return g;
}


static void draw_eq(Layer *l, GContext *ctx) {
  Menu *m = *(Menu **)layer_get_data(l);
  GRect b = layer_get_bounds(l);
  Grid g = grid_geo(b);
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  char s[28];
  if (m->col < 10) {
    int db = P.st[ST_EQ10 + m->col] - 12;
    snprintf(s, sizeof s, db ? "%s  %+d dB" : "%s  0 dB", BANDS10[m->col], db);
  } else strcpy(s, "All bands back to 0");
  draw_text(ctx, s, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD), GRect(0, 2, b.size.w, 24), GTextAlignmentCenter, T.text);
  int mid = g.bot - (g.bot - g.top) / 2;
  graphics_context_set_fill_color(ctx, T.text3);
  graphics_fill_rect(ctx, GRect(g.pad, mid, g.cw * 10, 1), 0, GCornerNone);
  GFont lf = fonts_get_system_font(g.cw >= 18 ? FONT_KEY_GOTHIC_14 : FONT_KEY_GOTHIC_09);
  for (int i = 0; i < 10; i++) {
    int cx = g.pad + g.cw * i + g.cw / 2;
    bool sel = m->col == i, adj = sel && m->adjust;
    if (sel) {
      graphics_context_set_fill_color(ctx, T.accent);
      graphics_fill_rect(ctx, GRect(cx - g.cw / 2, g.top - 8, g.cw, g.bot - g.top + 12 + (i & 1 ? 26 : 16) + 4), 4, GCornersAll);
    }
    GColor ink = sel ? GColorBlack : T.text3;
    graphics_context_set_stroke_color(ctx, ink);
    graphics_context_set_stroke_width(ctx, 1);
    graphics_draw_round_rect(ctx, GRect(cx - 3, g.top, 7, g.bot - g.top), 3);
    int ky = g.bot - (g.bot - g.top) * P.st[ST_EQ10 + i] / 24;
    graphics_context_set_fill_color(ctx, sel ? GColorBlack : T.accent);
    int a = min_i(mid, ky), h = abs(ky - mid);
    if (h > 0) graphics_fill_rect(ctx, GRect(cx - 1, a, 3, h + 1), 0, GCornerNone);
    // the knob: hollow while you're moving it, like Volume
    int kr = adj ? 6 : 4;
    graphics_context_set_fill_color(ctx, sel ? (adj ? T.accent : GColorBlack) : T.text);
    graphics_fill_circle(ctx, GPoint(cx, ky), kr);
    if (adj) {
      graphics_context_set_stroke_color(ctx, GColorBlack);
      graphics_context_set_stroke_width(ctx, 2);
      graphics_draw_circle(ctx, GPoint(cx, ky), kr);
    }
    draw_text(ctx, EQ_LABELS[i], lf, GRect(cx - g.cw / 2 - 6, g.bot + 4 + (i & 1) * 12, g.cw + 12, 18), GTextAlignmentCenter,
              sel ? GColorBlack : T.text2);
  }
  bool bs = m->col == 10;
  GRect br = GRect(b.size.w / 2 - 50, g.btn, 100, 26);
  graphics_context_set_fill_color(ctx, bs ? T.accent : T.bg);
  graphics_fill_rect(ctx, br, 6, GCornersAll);
  if (!bs) {
    graphics_context_set_stroke_color(ctx, T.text3);
    graphics_context_set_stroke_width(ctx, 1);
    graphics_draw_round_rect(ctx, br, 6);
  }
  draw_text(ctx, "Reset EQ", fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD), GRect(br.origin.x, br.origin.y + 1, br.size.w, 22),
            GTextAlignmentCenter, bs ? GColorBlack : T.text);
}

static void menu_push(int kind, uint32_t data, const char *song);
void back_to_main(void);

static void eq_reset(void) {
  for (int i = 0; i < 3; i++) P.st[ST_EQ3 + i] = 12;
  for (int i = 0; i < 10; i++) P.st[ST_EQ10 + i] = 12;
  station_set(STF_EQ_RESET, 0);
}

static void pick(MenuLayer *ml, MenuIndex *ix, void *ctx) {
  Menu *m = ctx;
  Item *it = &m->items[ix->row];
  switch (m->kind) {
    case M_MAIN:
      switch (it->data) {
        case 0: now_push(); break;
        case 1: list_push(LIST_SAVED); break;
        case 2: add_push(); break;
        case 3: list_push(LIST_TAGS); break;
        case 4: list_push(LIST_FAVORITES); break;
        case 5: settings_push(); break;
      }
      break;
    case M_ACTIONS:
      switch (it->data) {
        case 10: m->adjust = !m->adjust; m->adj = 10; fill(m); menu_layer_reload_data(m->ml); break;
        case 0: send_cmd(CMD_SAVE, 0, 0, NULL); now_saved(300); window_stack_pop(true); break;
        case 1: window_stack_pop(false); bio_push(); break;
        case 2: window_stack_pop(false); lyrics_push(); break;
        case 3: P.flags ^= FL_SONG_ID; send_cmd(CMD_SONG_ID, 0, 0, NULL); fill(m); menu_layer_reload_data(m->ml); break;
        case 4: sleep_push(); break;
        case 5: menu_push(M_STATION, 0, NULL); break;
      }
      break;
    case M_STATION:
    case M_ADCOVER:
    case M_EQ: {
      int32_t d = it->data;
      if (d < 0) break;
      if (d == D_SAVE_FIRST) {
        send_cmd(CMD_SAVE, 0, 0, NULL);
        now_saved(0);
        P.st[ST_FLAGS] |= SF_SAVED;
        send_cmd(CMD_STATION, STF_ASK, 0, NULL);
      } else if (m->kind == M_STATION && !(P.st[ST_FLAGS] & SF_SAVED)) {
        break;  // not saved: nothing to keep the settings with
      } else if (d == STF_GAIN || d == STF_AD_SECS || d == STF_BUF_SECS || (d >= STF_EQ3 && d < STF_EQ3 + 3) ||
                 (d >= STF_EQ10 && d < STF_EQ10 + 10)) {
        if (d == STF_AD_SECS && !(P.st[ST_FLAGS] & SF_AD_ON)) break;
        if (m->adjust && m->adj == d) m->adjust = false;
        else { m->adjust = true; m->adj = d; }
      } else if (d == D_EQ) { menu_push(M_EQ, 0, NULL); return; }
      else if (d == D_ADCOVER) { menu_push(M_ADCOVER, 0, NULL); return; }
      else if (d == D_COVER_WITH) { if (P.st[ST_FLAGS] & SF_AD_ON) list_push(LIST_COVERS); return; }
      else if (d == STF_BUF_ON || d == STF_TALK || d == STF_AD_ON) {
        uint8_t bit = d == STF_BUF_ON ? SF_BUF_ON : d == STF_TALK ? SF_TALK : SF_AD_ON;
        P.st[ST_FLAGS] ^= bit;
        station_set(d, (P.st[ST_FLAGS] & bit) ? 1 : 0);
      } else if (d == STF_EQ_RESET) eq_reset();
      fill(m);
      menu_layer_reload_data(m->ml);
      break;
    }
    case M_SLEEP:
      if (it->data == 100) { menu_push(M_SLEEP_END, 0, NULL); break; }
      send_cmd(CMD_SLEEP_SET, SLEEP_MIN[it->data], 0, NULL);
      P.sleep_end = SLEEP_MIN[it->data] ? time(NULL) + SLEEP_MIN[it->data] * 60 : 0;
      window_stack_pop(true);
      break;
    case M_SLEEP_END:
      if (it->data == 2) { list_push(LIST_PICK_TAG); break; }
      P.sleep_action = it->data;
      send_cmd(CMD_SLEEP_ACTION, it->data, 0, NULL);
      window_stack_pop(true);
      break;
    case M_THUMBS:
      P.verdict = 2;
      P.thumbs_what = it->data;
      P.thumbs_left = 15;
      send_cmd(CMD_THUMB_DOWN, it->data, 0, NULL);
      window_stack_pop(false);
      thumbs_send_push();
      break;
    case M_SERVICES:
      send_cmd(CMD_OPEN_SERVICE, (int32_t)m->data, it->data, NULL);
      window_stack_pop(true);
      break;
    case M_SORT:
      if (it->data == 100) { P.sort_desc = !P.sort_desc; send_cmd(CMD_SORT, P.sort_mode, P.sort_desc ? 1 : 0, NULL); }
      else {
        P.sort_mode = it->data;
        P.sort_desc = it->data != 1;  // the mode's own default: A to Z, newest and most played first
        send_cmd(CMD_SORT, P.sort_mode, P.sort_desc ? 1 : 0, NULL);
      }
      window_stack_pop(true);
      break;
    case M_SETTINGS:
      if (it->data == 1) {
        list_push(LIST_THEMES);  // "Match phone theme" or one of the themes; the list sends the choice
        return;
      } else if (it->data == 2) { P.prefs[PREF_ACCENT] = !P.prefs[PREF_ACCENT]; send_cmd(CMD_PREF, PREF_ACCENT, P.prefs[PREF_ACCENT], NULL); }
      else if (it->data == 3) { P.prefs[PREF_GROW] = !P.prefs[PREF_GROW]; send_cmd(CMD_PREF, PREF_GROW, P.prefs[PREF_GROW], NULL); }
      else if (it->data == 4 || it->data == 5) {
        // Select steps through the choices
        static const uint8_t SECS[] = { 3, 5, 7, 10, 15, 20, 30, 60 };
        if (it->data == 4) P.name_mode = (P.name_mode + 1) % 3;
        else {
          int k = 0;
          while (k < (int)ARRAY_LENGTH(SECS) && SECS[k] <= P.name_secs) k++;
          P.name_secs = SECS[k % ARRAY_LENGTH(SECS)];
        }
        uint8_t nm[2] = { P.name_mode, P.name_secs };
        persist_write_data(PK_NAMES, nm, 2);
        // the phone keeps the same choice (Settings › Pebble); an older phone just ignores it
        if (it->data == 4) send_cmd(CMD_PREF, PREF_NAMES, P.name_mode, NULL);
        else send_cmd(CMD_PREF, PREF_NAMES_SECS, P.name_secs, NULL);
      }
      persist_write_data(PK_PREFS, P.prefs, 4);
      fill(m);
      menu_layer_reload_data(m->ml);
      break;
  }
}

static void bars_tick(void *d) {
  s_bars_timer = NULL;
  s_bars_phase++;
  bool any = false;
  for (int i = 0; i < 6; i++)
    if (s_menus[i] && s_menus[i]->kind == M_MAIN && playing()) { layer_mark_dirty(menu_layer_get_layer(s_menus[i]->ml)); any = true; }
  if (any) s_bars_timer = app_timer_register(bars_ms(), bars_tick, NULL);
}

// ---- buttons: the menu's own Up/Down/Select, so Volume can take Up/Down while it's being moved ----
static void volume_step(Menu *m, int d) {
  if (!P.vol_max) { send_cmd(CMD_VOLUME, -1, 0, NULL); return; }
  int v = max_i(0, min_i(P.vol_max, P.vol + d));
  if (v == P.vol) return;
  P.vol = v;
  P.vol_ms = now_ms();
  send_cmd(CMD_VOLUME, v, 0, NULL);
  fill(m);
  menu_layer_reload_data(m->ml);
}

// Up/Down while a station setting is being changed
static void st_step(Menu *m, int d) {
  int f = m->adj, idx, lo = 0, hi = 24, step = 1, off = 12;
  if (f == STF_GAIN) { idx = ST_GAIN; hi = 80; step = 2; off = 40; }   // whole dB
  else if (f == STF_AD_SECS) { idx = ST_AD_SECS; lo = 1; hi = 120; off = 0; }
  else if (f == STF_BUF_SECS) { idx = ST_BUF; hi = 120; off = 0; }   // half seconds, 0 = auto
  else if (f >= STF_EQ10 && f < STF_EQ10 + 10) idx = ST_EQ10 + f - STF_EQ10;
  else if (f >= STF_EQ3 && f < STF_EQ3 + 3) idx = ST_EQ3 + f - STF_EQ3;
  else return;
  int v = max_i(lo, min_i(hi, P.st[idx] + d * step));
  if (v == P.st[idx]) return;
  P.st[idx] = v;
  station_set(f, v - off);
  fill(m);
  menu_layer_reload_data(m->ml);
}

// Ten bands: Up/Down walk the columns (and Reset EQ after the last); Select takes the column, like a row
static void grid_move(Menu *m, int d) {
  m->col = max_i(0, min_i(10, m->col + d));
  layer_mark_dirty(m->eq);
}

static void up_click(ClickRecognizerRef r, void *ctx) {
  Menu *m = ctx;
  input_seen();
  if (eq_grid(m) && !m->adjust) { grid_move(m, -1); return; }
  if (m->adjust) { if (m->kind == M_ACTIONS) volume_step(m, 1); else st_step(m, 1); return; }
  menu_layer_set_selected_next(m->ml, true, MenuRowAlignCenter, !click_recognizer_is_repeating(r));
}

static void down_click(ClickRecognizerRef r, void *ctx) {
  Menu *m = ctx;
  input_seen();
  if (eq_grid(m) && !m->adjust) { grid_move(m, 1); return; }
  if (m->adjust) { if (m->kind == M_ACTIONS) volume_step(m, -1); else st_step(m, -1); return; }
  menu_layer_set_selected_next(m->ml, false, MenuRowAlignCenter, !click_recognizer_is_repeating(r));
}

static void select_click(ClickRecognizerRef r, void *ctx) {
  Menu *m = ctx;
  input_seen();
  if (eq_grid(m)) {
    if (m->col == 10) eq_reset();
    else if (m->adjust) m->adjust = false;
    else { m->adjust = true; m->adj = STF_EQ10 + m->col; }
    fill(m);
    menu_layer_reload_data(m->ml);
    return;
  }
  MenuIndex ix = menu_layer_get_selected_index(m->ml);
  pick(m->ml, &ix, m);
}

static void back_click(ClickRecognizerRef r, void *ctx) {
  Menu *m = ctx;
  if (m->adjust) { m->adjust = false; fill(m); menu_layer_reload_data(m->ml); return; }
  window_stack_pop(true);
}

// ---- fingers on sliders (user, 2026-11-10): once a slider is being changed it follows the finger ----
#if defined(PBL_TOUCH)
// moves a slider; sends at most every 250 ms while the finger moves, and the last value when it lifts
static void slider_put(Menu *m, int32_t d, int v, bool final) {
  int top, old;
  if (!slider_get(m, d, &top, &old)) return;
  v = max_i(0, min_i(top, v));
  if (m->kind == M_ACTIONS) { P.vol = v; P.vol_ms = now_ms(); }
  else if (d == STF_GAIN) P.st[ST_GAIN] = v = (v + 1) & ~1;   // whole dB, like Up/Down
  else P.st[st_index(d)] = v;
  if (v != m->sent_v && (final || now_ms() - m->sent_ms >= 250)) {
    m->sent_v = v;
    m->sent_ms = now_ms();
    if (m->kind == M_ACTIONS) send_cmd(CMD_VOLUME, v, 0, NULL);
    else station_set(d, v - (d == STF_GAIN ? 40 : 12));
  }
  if (v == old) return;
  fill(m);
  menu_layer_reload_data(m->ml);
}

static int row_top(Menu *m, int row) {
  int y = HEADER_H + scroll_layer_get_content_offset(menu_layer_get_scroll_layer(m->ml)).y;
  for (int i = 0; i < row; i++) {
    MenuIndex ix = { 0, i };
    y += cell_h(m->ml, &ix, m);
  }
  return y;
}

// a row's slider (Volume, Bass/Mid/Treble): left and right ends of the knob's travel, as draw_row places it
static int row_slider_value(Menu *m, Item *it, int w, int x) {
  int s = tall(m) ? (m->small ? 18 : 24) : (m->small ? 16 : 20);
  int x0 = 6 + (it->icon >= 0 ? s + 10 : 0) + 2, x1 = w - 12;
  int top, v;
  if (!slider_get(m, it->data, &top, &v)) return 0;
  return (top * (x - x0) + (x1 - x0) / 2) / (x1 - x0);
}

static int row_at(Menu *m, int y) {
  if (y < HEADER_H) return -1;
  int ry = row_top(m, 0);
  for (int r = 0; r < m->n; r++) {
    MenuIndex ri = { 0, r };
    int rh = cell_h(m->ml, &ri, m);
    if (y >= ry && y < ry + rh) return r;
    ry += rh;
  }
  return -1;
}

// Slider rows. On the Equalizer the app takes every touch (a tap picks a band or Reset EQ, a swipe up or down
// scrolls); in Actions the system's touch navigation picks Volume and the finger takes over once it's being moved.
static void row_touch(Menu *m, const TouchEvent *e, GPoint p) {
  int w = layer_get_bounds(window_get_root_layer(m->win)).size.w;
  MenuIndex ix = menu_layer_get_selected_index(m->ml);
  switch (e->type) {
    case TouchEvent_Touchdown:
      m->down = true; m->drag = false; m->t0 = p; m->sent_v = -1;
      break;
    case TouchEvent_PositionUpdate:
      if (!m->down || !on_slider(m)) break;
      if (!m->drag && (abs(p.x - m->t0.x) < 6 || abs(p.y - m->t0.y) > abs(p.x - m->t0.x))) break;
      if (row_at(m, m->t0.y) != ix.row) break;   // the finger has to start on the slider being moved
      m->drag = true;
      slider_put(m, m->adj, row_slider_value(m, &m->items[ix.row], w, p.x), false);
      break;
    case TouchEvent_Liftoff: {
      if (!m->down) break;
      m->down = false;
      if (m->drag) { m->drag = false; slider_put(m, m->adj, row_slider_value(m, &m->items[ix.row], w, p.x), true); break; }
      int dy = p.y - m->t0.y;
      if (abs(dy) > 16 && abs(dy) > abs(p.x - m->t0.x)) {   // a swipe: scroll a row
        if (m->adjust) { m->adjust = false; fill(m); }
        menu_layer_set_selected_next(m->ml, dy > 0, MenuRowAlignCenter, true);
        menu_layer_reload_data(m->ml);
        break;
      }
      int r = row_at(m, p.y);
      if (on_slider(m) && r == ix.row) { slider_put(m, m->adj, row_slider_value(m, &m->items[r], w, p.x), true); break; }
      m->adjust = false;
      if (r >= 0 && !(m->items[r].flags & MI_NOTE)) {
        menu_layer_set_selected_index(m->ml, (MenuIndex){ 0, r }, MenuRowAlignNone, true);
        if (m->items[r].flags & MI_SLIDER) { m->adjust = true; m->adj = m->items[r].data; }
        else if (m->items[r].data == STF_EQ_RESET) eq_reset();
      }
      fill(m);
      menu_layer_reload_data(m->ml);
      break;
    }
  }
}

static int grid_value(Grid g, int y) {
  return max_i(0, min_i(24, (24 * (g.bot - y) + (g.bot - g.top) / 2) / (g.bot - g.top)));
}

static void grid_touch(Menu *m, const TouchEvent *e, GPoint p) {
  Grid g = grid_geo(layer_get_bounds(m->eq));
  int y = p.y - HEADER_H;
  switch (e->type) {
    case TouchEvent_Touchdown:
      m->down = true; m->drag = false; m->t0 = p; m->sent_v = -1;
      if (y >= g.btn - 4) m->col = 10;
      else if (y >= g.top - 12) {
        // touching a column picks it; dragging moves it
        m->col = max_i(0, min_i(9, (p.x - g.pad) / g.cw));
        m->adjust = true;
        m->adj = STF_EQ10 + m->col;
      } else { m->down = false; break; }
      fill(m);
      break;
    case TouchEvent_PositionUpdate:
      if (!m->down || m->col == 10 || (!m->drag && abs(p.y - m->t0.y) < 6)) break;
      m->drag = true;
      slider_put(m, m->adj, grid_value(g, y), false);
      break;
    case TouchEvent_Liftoff:
      if (!m->down) break;
      m->down = false;
      if (m->col == 10) { if (y >= g.btn - 4) { eq_reset(); fill(m); } }
      else if (m->drag) slider_put(m, m->adj, grid_value(g, y), true);
      m->drag = false;
      break;
  }
}

static void menu_touch(const TouchEvent *e, void *ctx) {
  Menu *m = ctx;
  input_seen();
  if (edge_back(e)) { window_stack_pop(true); return; }
  GPoint p = GPoint(e->x, e->y);
  if (eq_grid(m)) grid_touch(m, e, p);
  else if (m->kind == M_EQ || on_slider(m) || (m->down && e->type != TouchEvent_Touchdown)) row_touch(m, e, p);
}
#endif

static void click_config(void *ctx) {
  window_single_repeating_click_subscribe(BUTTON_ID_UP, 100, up_click);
  window_single_repeating_click_subscribe(BUTTON_ID_DOWN, 100, down_click);
  window_single_click_subscribe(BUTTON_ID_SELECT, select_click);
  window_single_click_subscribe(BUTTON_ID_BACK, back_click);
}

static void load(Window *w) {
  Menu *m = window_get_user_data(w);
  Layer *root = window_get_root_layer(w);
  GRect b = layer_get_bounds(root);
  m->head = layer_create_with_data(GRect(0, 0, b.size.w, HEADER_H), sizeof(Menu *));
  *(Menu **)layer_get_data(m->head) = m;
  layer_set_update_proc(m->head, draw_head);
  m->ml = menu_layer_create(GRect(0, HEADER_H, b.size.w, b.size.h - HEADER_H));
  menu_layer_set_callbacks(m->ml, m, (MenuLayerCallbacks){
    .get_num_rows = rows, .get_cell_height = cell_h, .draw_row = draw_row, .select_click = pick,
  });
  menu_layer_set_normal_colors(m->ml, T.bg, T.text);
  menu_layer_set_highlight_colors(m->ml, T.accent, GColorBlack);
  menu_layer_pad_bottom_enable(m->ml, false);
  window_set_click_config_provider_with_context(w, click_config, m);
  layer_add_child(root, menu_layer_get_layer(m->ml));
  if (m->kind == M_EQ) {
    m->eq = layer_create_with_data(GRect(0, HEADER_H, b.size.w, b.size.h - HEADER_H), sizeof(Menu *));
    *(Menu **)layer_get_data(m->eq) = m;
    layer_set_update_proc(m->eq, draw_eq);
    layer_set_hidden(m->eq, true);
    layer_add_child(root, m->eq);
  }
  layer_add_child(root, m->head);
  fill(m);
  menu_layer_reload_data(m->ml);
}

static void appear(Window *w) {
  Menu *m = window_get_user_data(w);
  fill(m);
  menu_layer_reload_data(m->ml);
  if (m->kind == M_MAIN && !s_bars_timer) s_bars_timer = app_timer_register(bars_ms(), bars_tick, NULL);
  if (m->kind == M_ACTIONS) send_cmd(CMD_VOLUME, -1, 0, NULL);
  if (m->kind == M_STATION) send_cmd(CMD_STATION, STF_ASK, 0, NULL);
#if defined(PBL_TOUCH)
  m->down = m->drag = false;
  if (touch_service_is_enabled()) touch_service_subscribe(menu_touch, m);
#endif
}

static void disappear(Window *w) {
  Menu *m = window_get_user_data(w);
  m->adjust = false;
  touch_off();
}

static void unload(Window *w) {
  Menu *m = window_get_user_data(w);
  for (int i = 0; i < 6; i++) if (s_menus[i] == m) s_menus[i] = NULL;
  if (w == s_main) s_main = NULL;
  menu_layer_destroy(m->ml);
  if (m->eq) layer_destroy(m->eq);
  layer_destroy(m->head);
  free(m);
  window_destroy(w);
}

static void menu_push(int kind, uint32_t data, const char *song) {
  Menu *m = calloc(1, sizeof(Menu));
  if (!m) return;
  m->kind = kind;
  m->data = data;
  if (song) strncpy(m->song, song, sizeof m->song - 1);
  m->win = window_create();
  for (int i = 0; i < 6; i++) if (!s_menus[i]) { s_menus[i] = m; break; }
  window_set_user_data(m->win, m);
  window_set_background_color(m->win, T.bg);
  window_set_window_handlers(m->win, (WindowHandlers){ .load = load, .unload = unload, .appear = appear, .disappear = disappear });
  if (kind == M_MAIN) s_main = m->win;
  window_stack_push(m->win, kind != M_MAIN);
}

void main_menu_push(void) { menu_push(M_MAIN, 0, NULL); }
void actions_push(void) { menu_push(M_ACTIONS, 0, NULL); }
void sleep_push(void) { menu_push(M_SLEEP, 0, NULL); }
void thumbs_ask_push(void) { menu_push(M_THUMBS, 0, NULL); }
void services_push(uint32_t id, const char *song) { menu_push(M_SERVICES, id, song); }
void sort_push(void) { menu_push(M_SORT, 0, NULL); }
void settings_push(void) { menu_push(M_SETTINGS, 0, NULL); }

// Back from Now playing (and the other top screens) goes to the main menu.
void back_to_main(void) {
  while (window_stack_get_top_window() && window_stack_get_top_window() != s_main) window_stack_pop(false);
  if (!s_main) main_menu_push();
}

void menus_refresh(uint32_t what) {
  for (int i = 0; i < 6; i++) {
    Menu *m = s_menus[i];
    if (!m || !m->ml) continue;
    if (what & CH_THEME) {
      window_set_background_color(m->win, T.bg);
      menu_layer_set_highlight_colors(m->ml, T.accent, GColorBlack);
    }
    bool st = m->kind == M_STATION || m->kind == M_ADCOVER || m->kind == M_EQ;
    if (what & (CH_NOW | CH_THEME | CH_LIST | CH_BIO | (m->kind == M_ACTIONS ? CH_VOLUME : 0) | (st ? CH_STATION : 0))) {
      fill(m);
      menu_layer_reload_data(m->ml);
      layer_mark_dirty(m->head);
    }
  }
}
