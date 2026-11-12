// The lists the phone fills row by row: Saved Frequencies, Tags (a filter), History and favorites,
// Search with the Discover strip, Previously featured, watch themes, picking a tag for the sleep timer.
#include "freq.h"

static Window *s_win;
static MenuLayer *s_ml;
static Layer *s_head, *s_over;
static Hold s_hold;
static uint8_t s_kind;
static AppTimer *s_bars;
static int s_phase;
// search
static bool s_strip;        // the chip strip has focus
static int s_chip = 1;      // 0 microphone, 1 Most listened (default) .. 5 New
#if defined(PBL_MICROPHONE)
static DictationSession *s_dict;
#endif
static const char *CHIPS[6] = { "", "Most listened", "Trending", "Random", "Most voted", "New" };
static int16_t s_chip_x[6], s_chip_w[6];   // where each chip was drawn, for taps
static int s_strip_drag;    // how far a finger has scrolled the strip
// Big lists come in pages of PAGE rows: a MenuLayer can't be taller than 32767 px, and Saved can hold
// hundreds of frequencies (the user has 800+). s_off is the list index at the page's first row.
#define PAGE 200
#define EDGE (PAGE / 10)   // rows from a page's end where the next page comes in
static uint16_t s_off;
static AppTimer *s_page_timer;
static int s_page_keep = -1;

// one-line lists to pick from, with a first row that isn't from the list: the theme list ("Match phone theme") and
// Station settings' "Cover it with…" ("Any frequency without ads")
static bool pick_list(void) { return s_kind == LIST_THEMES || s_kind == LIST_COVERS; }
static bool has_top_button(void) { return s_kind == LIST_FAVORITES || s_kind == LIST_HISTORY || pick_list(); }
static int top_rows(void) { return has_top_button() && s_off == 0 ? 1 : 0; }
static int lidx(int row) { return s_off + row - top_rows(); }   // menu row -> list index
static bool tag_kind(void) { return s_kind == LIST_TAGS || s_kind == LIST_PICK_TAG; }
static int strip_h(void) { return s_kind == LIST_SEARCH ? (is_large() ? 40 : 32) : 0; }

static const char *title(void) {
  switch (s_kind) {
    case LIST_SAVED: return "Saved Frequencies";
    case LIST_TAGS: return "Tags";
    case LIST_PICK_TAG: return "Switch to tagged";
    case LIST_FAVORITES: case LIST_HISTORY: return "History";
    case LIST_PREV_FEATURED: return "Previously featured";
    case LIST_THEMES: return "Watch theme";
    case LIST_COVERS: return "Cover it with";
    default: return "Search";
  }
}

static uint16_t rows(MenuLayer *ml, uint16_t sec, void *ctx) {
  if (tag_kind()) return P.ntags ? P.ntags : 1;
  if (!L.loaded || !L.total) return top_rows() + 1;
  if (s_off >= L.total) s_off = 0;
  return top_rows() + min_i(PAGE, L.total - s_off);
}

static int16_t cell_h(MenuLayer *ml, MenuIndex *ix, void *ctx) {
  bool big = layer_get_bounds(menu_layer_get_layer(ml)).size.w >= 180;
  if (tag_kind()) return big ? 44 : 34;
  if (ix->row < top_rows()) return pick_list() ? (big ? 34 : 28) : (big ? 62 : 48);
  if (!L.loaded || !L.total) return big ? 44 : 36;
  const Row *r = list_peek(&L, lidx(ix->row));   // never ask the phone from here: it's called for every row
  if (r && (r->flags & RF_HEADING)) return big ? 22 : 18;
  if (pick_list()) return big ? 34 : 28;
  return big ? 56 : 44;
}

static void row_bg(GContext *ctx, GRect b, bool hi, int row) {
  (void)row;   // no alternating rows (user, round 2)
  graphics_context_set_fill_color(ctx, hi ? T.accent : T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
}

static void draw_tag_row(GContext *ctx, const Layer *cell, int i, bool hi) {
  GRect b = layer_get_bounds(cell);
  bool big = b.size.w >= 180;
  // The tag's own color stays on the plain background, so every pill reads the same chosen or not; the chosen
  // row gets an accent frame and a bar on the left instead of a filled highlight (user, 2026-11-10).
  row_bg(ctx, b, false, i);
  if (hi) {
    graphics_context_set_stroke_color(ctx, T.accent);
    graphics_context_set_stroke_width(ctx, 2);
    graphics_draw_round_rect(ctx, GRect(2, 2, b.size.w - 26, b.size.h - 4), 8);
  }
  if (!P.ntags) {
    draw_text(ctx, "No tags yet", fonts_get_system_font(FONT_KEY_GOTHIC_18), GRect(8, 2, b.size.w - 30, 24), GTextAlignmentLeft, T.text2);
    return;
  }
  Tag *t = &P.tags[i];
  draw_pill(ctx, 8, (b.size.h - 22) / 2, t, b.size.w - 72, false);
  char n[8];
  snprintf(n, sizeof n, "%d", t->count);
  draw_text(ctx, n, fonts_get_system_font(big ? FONT_KEY_GOTHIC_24 : FONT_KEY_GOTHIC_18), GRect(b.size.w - 64, (b.size.h - (big ? 30 : 24)) / 2, 36, 30),
            GTextAlignmentRight, hi ? T.accent : T.text);
}

static void draw_row(GContext *ctx, const Layer *cell, MenuIndex *ix, void *vctx) {
  GRect b = layer_get_bounds(cell);
  bool big = b.size.w >= 180;
  bool hi = menu_cell_layer_is_highlighted(cell) && !s_strip;
  int ew = big ? 24 : 20;  // room for the slim edge icons
  if (tag_kind()) { draw_tag_row(ctx, cell, ix->row, hi); return; }
  graphics_context_set_fill_color(ctx, T.bg);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  if (ix->row < top_rows() && pick_list()) {
    // the theme list starts with following the phone (user's sketch, 2026-11-10); the cover list with "any"
    int right = b.size.w - 6;
    row_bg(ctx, b, hi, 0);
    bool on = s_kind == LIST_THEMES ? P.prefs[PREF_FOLLOW] : !(P.st[ST_FLAGS] & SF_AD_CHOSEN);
    if (on) draw_icon(ctx, IC_CHECK, GRect(right - 16, 8, 16, 16), hi ? GColorBlack : T.text);
    draw_text(ctx, s_kind == LIST_THEMES ? "Match phone theme" : "Any frequency without ads",
              fonts_get_system_font(big && s_kind == LIST_THEMES ? FONT_KEY_GOTHIC_24_BOLD : FONT_KEY_GOTHIC_18_BOLD),
              GRect(6, big ? (s_kind == LIST_THEMES ? -2 : 3) : 0, right - 26, 30), GTextAlignmentLeft, hi ? GColorBlack : T.text);
    return;
  }
  if (ix->row < top_rows()) {
    // Full history (or back to favorites), a line, then the Favorites subtitle (user)
    row_bg(ctx, GRect(0, 0, b.size.w, big ? 34 : 26), hi, 0);
    GColor ink = hi ? GColorBlack : T.text;
    draw_icon(ctx, IC_CLOCK, GRect(6, big ? 7 : 5, big ? 20 : 16, big ? 20 : 16), hi ? GColorBlack : T.accent);
    draw_text(ctx, s_kind == LIST_FAVORITES ? "Full history" : "Favorites only", fonts_get_system_font(big ? FONT_KEY_GOTHIC_24_BOLD : FONT_KEY_GOTHIC_18_BOLD),
              GRect(big ? 34 : 28, big ? -2 : 0, b.size.w - 40, 30), GTextAlignmentLeft, ink);
    graphics_context_set_fill_color(ctx, T.text3);
    graphics_fill_rect(ctx, GRect(6, big ? 37 : 28, b.size.w - 12, 1), 0, GCornerNone);
    draw_text(ctx, s_kind == LIST_FAVORITES ? "Favorites" : "Every song heard", fonts_get_system_font(big ? FONT_KEY_GOTHIC_18_BOLD : FONT_KEY_GOTHIC_14_BOLD),
              GRect(6, big ? 36 : 27, b.size.w - 12, 22), GTextAlignmentLeft, T.accent);
    return;
  }
  if (!L.loaded || !L.total) {
    const char *msg = !L.loaded ? "Loading…" : s_kind == LIST_SAVED ? "Nothing saved yet" :
                      s_kind == LIST_FAVORITES ? "No favorites yet" : s_kind == LIST_SEARCH ? "Nothing found" : "Nothing here yet";
    draw_text(ctx, msg, fonts_get_system_font(FONT_KEY_GOTHIC_18), GRect(8, 6, b.size.w - 16, 24), GTextAlignmentLeft, T.text2);
    return;
  }
  const Row *r = list_row(&L, lidx(ix->row));
  if (!r) {
    draw_text(ctx, "…", fonts_get_system_font(FONT_KEY_GOTHIC_18), GRect(8, 6, b.size.w - 16, 24), GTextAlignmentLeft, T.text3);
    return;
  }
  if (r->flags & RF_HEADING) {
    draw_text(ctx, r->name, fonts_get_system_font(FONT_KEY_GOTHIC_14_BOLD), GRect(6, big ? 1 : 0, b.size.w - 12, 18), GTextAlignmentLeft, T.accent);
    return;
  }
  row_bg(ctx, GRect(0, 0, b.size.w - ew, b.size.h), hi, ix->row);
  bool down = r->flags & RF_DOWN;
  GColor ink = hi ? GColorBlack : (down ? T.text3 : T.text);
  GColor ink2 = hi ? GColorBlack : (down ? T.down : T.text2);
  int right = b.size.w - ew - 4;
  // right side marks: bars on the one playing, ✓ saved, ★ favorite
  if (r->flags & RF_PLAYING) {
    draw_bars(ctx, GRect(right - 18, big ? 10 : 8, 18, big ? 18 : 14), hi ? GColorBlack : T.playing, s_phase);
    right -= 24;
  }
  if ((s_kind == LIST_SEARCH || s_kind == LIST_PREV_FEATURED) && (r->flags & RF_SAVED)) {
    draw_icon(ctx, IC_CHECK, GRect(right - 16, big ? 10 : 8, 16, 16), hi ? GColorBlack : T.accent);
    right -= 20;
  }
  if (s_kind == LIST_FAVORITES || s_kind == LIST_HISTORY) {
    bool fav = r->flags & RF_FAVORITE;
    draw_icon(ctx, fav ? IC_STAR : IC_STAR_O, GRect(right - 18, big ? 18 : 14, 18, 18),
              hi ? GColorBlack : (fav ? T.accent : T.text3));
    right -= 22;
  }
  if (pick_list()) {
    bool mine = s_kind == LIST_THEMES ? !P.prefs[PREF_FOLLOW] : (P.st[ST_FLAGS] & SF_AD_CHOSEN);
    if ((r->flags & RF_CURRENT) && mine) draw_icon(ctx, IC_CHECK, GRect(right - 16, 8, 16, 16), hi ? GColorBlack : T.text);
    draw_text(ctx, r->name, fonts_get_system_font(big ? FONT_KEY_GOTHIC_24_BOLD : FONT_KEY_GOTHIC_18_BOLD),
              GRect(6, big ? -2 : 0, right - 26, 30), GTextAlignmentLeft, ink);
    return;
  }
  draw_text(ctx, r->name, fonts_get_system_font(big ? FONT_KEY_GOTHIC_24_BOLD : FONT_KEY_GOTHIC_18_BOLD),
            GRect(6, big ? -3 : -1, right - 6, 30), GTextAlignmentLeft, ink);
  int y2 = big ? 26 : 20;
  if (s_kind == LIST_SAVED && !down) {
    // the tags as small desktop-style pills under the name (+N if no room)
    int x = 6;
    for (int k = 0; k < r->ntags; k++) {
      if (r->tags[k] >= P.ntags) continue;
      int room = right - x - (k + 1 < r->ntags ? 30 : 0);
      if (room < 30) {
        char more[6];
        snprintf(more, sizeof more, "+%d", r->ntags - k);
        draw_text(ctx, more, fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD), GRect(x, y2 - 2, 30, 22), GTextAlignmentLeft, ink2);
        break;
      }
      Tag t = P.tags[r->tags[k]];
      t.state = 1;
      x += draw_pill(ctx, x, y2 + 1, &t, room, !big) + 4;
    }
    return;
  }
  char sub[64];
  if (down) {
    char tm[8];
    snprintf(sub, sizeof sub, "Down since %s", r->time ? fmt_time(r->time, tm, sizeof tm) : "a while");
    if (!clock_is_24h_style() && r->time) {
      struct tm *lt = localtime((time_t *)&r->time);
      strncat(sub, lt->tm_hour >= 12 ? " PM" : " AM", sizeof sub - strlen(sub) - 1);
    }
  } else if ((s_kind == LIST_FAVORITES || s_kind == LIST_HISTORY) && r->time) {
    char tm[8];
    fmt_time(r->time, tm, sizeof tm);
    struct tm *lt = localtime((time_t *)&r->time);
    snprintf(sub, sizeof sub, "%s%s · %s", tm, clock_is_24h_style() ? "" : (lt->tm_hour >= 12 ? " PM" : " AM"), r->sub);
  } else {
    strncpy(sub, r->sub, sizeof sub - 1);
    sub[sizeof sub - 1] = 0;
  }
  if (sub[0]) draw_text(ctx, sub, fonts_get_system_font(big ? FONT_KEY_GOTHIC_18 : FONT_KEY_GOTHIC_14), GRect(6, y2, right - 6, 22), GTextAlignmentLeft, ink2);
}

static void draw_head(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(window_get_root_layer(s_win));
  char extra[24] = "";
  if (s_kind == LIST_TAGS) {
    int on = 0, hid = 0;
    for (int i = 0; i < P.ntags; i++) { if (P.tags[i].state == 1) on++; else if (P.tags[i].state == 2) hid++; }
    if (on || hid) snprintf(extra, sizeof extra, hid ? "%d on · %d hidden" : "%d on", on, hid);
  }
  if (s_kind == LIST_SEARCH) {
    // the Discover strip: microphone chip (Time 2), then the phone's browse lists
    // bigger chips you can tap, and drag sideways to scroll (user, round 2)
    bool big = is_large();
    int sh = strip_h(), ch = big ? 32 : 26, cy = (sh - ch) / 2;
    graphics_context_set_fill_color(ctx, T.bg);
    graphics_fill_rect(ctx, GRect(0, 0, b.size.w, sh), 0, GCornerNone);
    GFont f = fonts_get_system_font(big ? FONT_KEY_GOTHIC_24 : FONT_KEY_GOTHIC_18);
    int first = PBL_IF_MICROPHONE_ELSE(0, 1);
    int widths[6], total = 4;
    for (int i = first; i < 6; i++) {
      widths[i] = i == 0 ? ch + 8 : graphics_text_layout_get_content_size(CHIPS[i], f, GRect(0, 0, 200, 30), GTextOverflowModeWordWrap, GTextAlignmentLeft).w + 18;
      total += widths[i] + 6;
    }
    // keep the chosen chip in view, then add the finger's scroll; never scroll past either end
    int x = 4, before = 0;
    for (int i = first; i < s_chip; i++) before += widths[i] + 6;
    if (before + widths[s_chip] > b.size.w - 8) x -= before + widths[s_chip] - (b.size.w - 8);
    x += s_strip_drag;
    int min_x = min_i(4, b.size.w - total);
    if (x > 4) { s_strip_drag -= x - 4; x = 4; }
    if (x < min_x) { s_strip_drag += min_x - x; x = min_x; }
    for (int i = first; i < 6; i++) {
      GRect r = GRect(x, cy, widths[i], ch);
      s_chip_x[i] = x; s_chip_w[i] = widths[i];
      bool chosen = i == s_chip;
      if (chosen) {
        graphics_context_set_fill_color(ctx, s_strip ? T.accent : T.text2);
        graphics_fill_rect(ctx, r, ch / 2, GCornersAll);
      } else {
        graphics_context_set_stroke_color(ctx, T.text3);
        graphics_context_set_stroke_width(ctx, 1);
        graphics_draw_round_rect(ctx, r, ch / 2);
      }
      int is = big ? 20 : 16;
      if (i == 0) draw_icon(ctx, IC_MIC, GRect(x + (widths[i] - is) / 2, cy + (ch - is) / 2, is, is), chosen ? GColorBlack : T.text2);
      else draw_text(ctx, CHIPS[i], f, GRect(x + 9, cy + (big ? -3 : -1), widths[i] - 12, ch), GTextAlignmentLeft, chosen ? GColorBlack : T.text2);
      x += widths[i] + 6;
    }
    return;
  }
  draw_header(ctx, b, title(), extra);
}

// slim edge icons and the hold ring, over the list
static void draw_over(Layer *l, GContext *ctx) {
  GRect b = layer_get_bounds(window_get_root_layer(s_win));
  if (pick_list() || s_kind == LIST_PICK_TAG) return;
  int icons[3] = { IC_UP, IC_PLAY, IC_DOWN };
  int hold_icon = IC_SORT;
  GColor hc = T.accent;
  if (tag_kind()) {
    MenuIndex ix = menu_layer_get_selected_index(s_ml);
    bool on = P.ntags && P.tags[ix.row].state == 1;
    icons[1] = on ? IC_NO : IC_CHECK;
    hold_icon = IC_MINUS;
  } else if (s_kind == LIST_FAVORITES || s_kind == LIST_HISTORY) {
    MenuIndex ix = menu_layer_get_selected_index(s_ml);
    const Row *r = ix.row >= top_rows() && L.loaded ? list_peek(&L, lidx(ix.row)) : NULL;
    icons[1] = r && (r->flags & RF_FAVORITE) ? IC_STAR : IC_STAR_O;
    hold_icon = IC_PHONE;
  } else if (s_kind == LIST_SEARCH || s_kind == LIST_PREV_FEATURED) {
    hold_icon = IC_PLUS;
  }
  int p = hold_progress(&s_hold, BUTTON_ID_SELECT);
  if (p > 0) icons[1] = -1;
  // a dark backdrop column so the icons read over the rows
  // only behind the icons: the strip doesn't run to the top and bottom edges (user, round 2)
  graphics_context_set_fill_color(ctx, T.bg);
  int ew = b.size.w >= 180 ? 24 : 20;
  GRect r0 = edge_icon_rect(b, 0), r2 = edge_icon_rect(b, 2);
  graphics_fill_rect(ctx, GRect(b.size.w - ew, r0.origin.y - 6, ew, r2.origin.y + r2.size.h + 6 - (r0.origin.y - 6)), 6, GCornersLeft);
  draw_edge_icons(ctx, b, icons, NULL);
  if (p > 0) draw_hold_ring(ctx, b, 1, hold_icon, hc, p);
}

static uint8_t list_cmd_kind(void) { return s_kind; }

static void act(int row, bool hold) {
  if (tag_kind()) {
    if (!P.ntags) return;
    Tag *t = &P.tags[row];
    if (s_kind == LIST_PICK_TAG) {
      P.sleep_action = 2;
      send_cmd(CMD_SLEEP_ACTION, 2, row, NULL);
      window_stack_pop(false);
      window_stack_pop(true);
      return;
    }
    if (hold) t->state = t->state == 2 ? 0 : 2;
    else t->state = t->state == 1 ? 0 : 1;
    send_cmd(CMD_SET_TAG, row, t->state, NULL);
    menu_layer_reload_data(s_ml);
    layer_mark_dirty(s_head);
    return;
  }
  if (row < top_rows() && s_kind == LIST_THEMES) {
    if (!P.prefs[PREF_FOLLOW]) { P.prefs[PREF_FOLLOW] = 1; send_cmd(CMD_PREF, PREF_FOLLOW, 1, NULL); }
    window_stack_pop(true);
    return;
  }
  if (row < top_rows() && s_kind == LIST_COVERS) {
    P.st[ST_FLAGS] &= ~SF_AD_CHOSEN;
    station_set(STF_AD_ANY, 0);
    window_stack_pop(true);
    return;
  }
  if (row < top_rows()) {
    if (hold) return;
    uint8_t k = s_kind == LIST_FAVORITES ? LIST_HISTORY : LIST_FAVORITES;
    s_kind = k;
    s_off = 0;
    list_request(&L, k, 0);
    menu_layer_set_selected_index(s_ml, (MenuIndex){ 0, 0 }, MenuRowAlignTop, false);
    menu_layer_reload_data(s_ml);
    return;
  }
  if (!L.loaded || !L.total) return;
  const Row *r = list_row(&L, lidx(row));
  if (!r || (r->flags & RF_HEADING)) return;
  switch (s_kind) {
    case LIST_SAVED:
      if (hold) { sort_push(); return; }
      send_cmd(CMD_PLAY_ITEM, r->id, LIST_SAVED, NULL);
      strncpy(P.station, r->name, sizeof P.station - 1);
      P.state = ST_TUNING;
      now_push();
      return;
    case LIST_FAVORITES: case LIST_HISTORY:
      if (hold) { services_push(r->id, r->name); return; }
      send_cmd(CMD_FAVORITE, r->id, s_kind, NULL);
      ((Row *)r)->flags ^= RF_FAVORITE;
      menu_layer_reload_data(s_ml);
      return;
    case LIST_SEARCH: case LIST_PREV_FEATURED:
      if (hold) { send_cmd(CMD_SAVE, r->id, list_cmd_kind(), NULL); ((Row *)r)->flags |= RF_SAVED; }
      else send_cmd(CMD_LISTEN, r->id, list_cmd_kind(), NULL);
      menu_layer_reload_data(s_ml);
      return;
    case LIST_COVERS:
      P.st[ST_FLAGS] |= SF_AD_CHOSEN;
      strncpy(P.st_cover, r->name, sizeof P.st_cover - 1);
      station_set(STF_AD_COVER, (int32_t)r->id);
      window_stack_pop(true);
      return;
    case LIST_THEMES:
      if (P.prefs[PREF_FOLLOW]) { P.prefs[PREF_FOLLOW] = 0; send_cmd(CMD_PREF, PREF_FOLLOW, 0, NULL); }
      send_cmd(CMD_PREF, PREF_THEME, r->id, NULL);
      strncpy(P.theme_name, r->name, sizeof P.theme_name - 1);
      window_stack_pop(true);
      return;
  }
}

static void select_cb(MenuLayer *ml, MenuIndex *ix, void *ctx) { act(ix->row, false); }  // touch taps

static char s_said[96];   // the last thing said to the microphone chip (96: room on the 64 KB watches)

static void search(int chip, const char *text) {
  s_chip = chip;
  s_off = 0;
  if (chip == 0 && text && text != s_said) strncpy(s_said, text, sizeof s_said - 1);
  memset(&L, 0, sizeof L);
  L.kind = LIST_SEARCH;
  // marked as asked so the screen coming back from the microphone doesn't ask again: that second, empty ask
  // came back as Most listened and replaced the spoken results (user, 2026-11-10)
  L.asked = true; L.asked_ms = now_ms();
  send_cmd(CMD_SEARCH, chip, 0, chip == 0 ? s_said : text);
  if (s_ml) menu_layer_reload_data(s_ml);
}

#if defined(PBL_MICROPHONE)
static void dictated(DictationSession *s, DictationSessionStatus st, char *text, void *ctx) {
  if (st == DictationSessionStatusSuccess && text && text[0]) search(0, text);
}
#endif

// day headings and "Last week" are labels: the selection steps over them
static void skip_heading(bool up) {
  if (tag_kind() || !L.loaded || !L.total) return;
  MenuIndex ix = menu_layer_get_selected_index(s_ml);
  if (ix.row < top_rows()) return;
  const Row *r = list_peek(&L, lidx(ix.row));
  if (!r || !(r->flags & RF_HEADING)) return;
  if (up && ix.row == 0) up = false;
  if (!up && lidx(ix.row) + 1 >= L.total) return;
  menu_layer_set_selected_next(s_ml, up, MenuRowAlignCenter, true);
}

static void on_click(Hold *h, ButtonId b) {
  if (s_kind == LIST_SEARCH && s_strip) {
    int first = PBL_IF_MICROPHONE_ELSE(0, 1);
    if (b == BUTTON_ID_UP && s_chip > first) s_chip--;
    else if (b == BUTTON_ID_DOWN && s_chip < 5) s_chip++;
    else if (b == BUTTON_ID_SELECT) {
#if defined(PBL_MICROPHONE)
      if (s_chip == 0) {
        if (!s_dict) s_dict = dictation_session_create(128, dictated, NULL);
        if (s_dict) dictation_session_start(s_dict);
        return;
      }
#endif
      s_strip = false;
      menu_layer_reload_data(s_ml);
      layer_mark_dirty(s_head);
      return;
    }
    s_strip_drag = 0;
    if (s_chip > 0) search(s_chip, NULL);
    layer_mark_dirty(s_head);
    return;
  }
  MenuIndex ix = menu_layer_get_selected_index(s_ml);
  if (b == BUTTON_ID_UP) {
    if (ix.row == 0 && s_kind == LIST_SEARCH) { s_strip = true; menu_layer_reload_data(s_ml); layer_mark_dirty(s_head); return; }
    menu_layer_set_selected_next(s_ml, true, MenuRowAlignCenter, true);
    skip_heading(true);
  } else if (b == BUTTON_ID_DOWN) {
    menu_layer_set_selected_next(s_ml, false, MenuRowAlignCenter, true);
    skip_heading(false);
  } else if (b == BUTTON_ID_SELECT) act(ix.row, false);
  layer_mark_dirty(s_over);
}

static void on_hold(Hold *h, ButtonId b) {
  if (b != BUTTON_ID_SELECT) return;
  MenuIndex ix = menu_layer_get_selected_index(s_ml);
  act(ix.row, true);
  if (s_over) layer_mark_dirty(s_over);
}

static void click_config(void *ctx) {
  hold_click_config(ctx);
}

// the little playing bars: redraw only when a row on screen is playing
static void bars_tick(void *d) {
  s_bars = NULL;
  s_phase++;
  if (s_ml && !tag_kind() && L.loaded) {
    bool any = false;
    for (int i = 0; i < L.have; i++) if (L.rows[i].flags & RF_PLAYING) { any = true; break; }
    if (any) layer_mark_dirty(menu_layer_get_layer(s_ml));
  }
  s_bars = app_timer_register(bars_ms(), bars_tick, NULL);
}

// move the page so the selection stays well inside it
static void page_apply(void *d) {
  s_page_timer = NULL;
  if (!s_ml || s_page_keep < 0) return;
  s_page_keep = -1;
  MenuIndex ix = menu_layer_get_selected_index(s_ml);
  if (ix.row < top_rows()) return;
  int keep = lidx(ix.row);
  // the selection lands in the middle of the new page
  int off = max_i(0, min_i(keep - PAGE / 2, L.total - PAGE));
  if (off == s_off || keep < off || keep >= off + PAGE) return;
  s_off = off;
  menu_layer_reload_data(s_ml);
  menu_layer_set_selected_index(s_ml, (MenuIndex){ 0, keep - s_off + top_rows() }, MenuRowAlignCenter, false);
  layer_mark_dirty(s_over);
}

static void selection_changed(MenuLayer *ml, MenuIndex new_index, MenuIndex old_index, void *ctx) {
  if (s_over) layer_mark_dirty(s_over);
  if (tag_kind() || !L.loaded || L.total <= PAGE || new_index.row < top_rows()) return;
  int i = lidx(new_index.row);
  if ((i - s_off >= PAGE - EDGE && s_off + PAGE < L.total) || (i - s_off < EDGE && s_off > 0)) {
    s_page_keep = i;
    // after the scroll animation: moving the page during it put the selection back on the old row number
    if (!s_page_timer) s_page_timer = app_timer_register(350, page_apply, NULL);
  }
}

// ---- touch: Search's chip strip (tap a chip, drag to scroll) and the edge swipe back ----
#if defined(PBL_TOUCH)
static bool s_on_strip;
static GPoint s_t0, s_tl;
static void touch(const TouchEvent *e, void *ctx) {
  input_seen();
  if (edge_back(e)) { window_stack_pop(true); return; }
  if (s_kind != LIST_SEARCH) return;
  GPoint p = GPoint(e->x, e->y);
  switch (e->type) {
    case TouchEvent_Touchdown:
      s_on_strip = p.y < strip_h();
      s_t0 = s_tl = p;
      break;
    case TouchEvent_PositionUpdate:
      if (!s_on_strip) break;
      s_strip_drag += p.x - s_tl.x;
      s_tl = p;
      layer_mark_dirty(s_head);
      break;
    case TouchEvent_Liftoff: {
      if (!s_on_strip) break;
      s_on_strip = false;
      if (abs(p.x - s_t0.x) > 8 || abs(p.y - s_t0.y) > 8) break;
      int first = PBL_IF_MICROPHONE_ELSE(0, 1);
      for (int i = first; i < 6; i++) {
        if (p.x < s_chip_x[i] || p.x >= s_chip_x[i] + s_chip_w[i]) continue;
#if defined(PBL_MICROPHONE)
        if (i == 0) {
          s_chip = 0;
          if (!s_dict) s_dict = dictation_session_create(128, dictated, NULL);
          if (s_dict) dictation_session_start(s_dict);
          break;
        }
#endif
        s_strip = false;
        search(i, NULL);
        layer_mark_dirty(s_head);
        break;
      }
      break;
    }
  }
}
#endif

static void load(Window *w) {
  Layer *root = window_get_root_layer(w);
  GRect b = layer_get_bounds(root);
  int top = s_kind == LIST_SEARCH ? strip_h() : HEADER_H;
  s_head = layer_create(GRect(0, 0, b.size.w, top));
  layer_set_update_proc(s_head, draw_head);
  s_ml = menu_layer_create(GRect(0, top, b.size.w, b.size.h - top));
  menu_layer_set_callbacks(s_ml, NULL, (MenuLayerCallbacks){
    .get_num_rows = rows, .get_cell_height = cell_h, .draw_row = draw_row, .select_click = select_cb,
    .selection_changed = selection_changed,
  });
  menu_layer_set_normal_colors(s_ml, T.bg, T.text);
  menu_layer_set_highlight_colors(s_ml, T.accent, GColorBlack);
  menu_layer_pad_bottom_enable(s_ml, false);
  layer_add_child(root, menu_layer_get_layer(s_ml));
  layer_add_child(root, s_head);
  s_over = layer_create(b);
  layer_set_update_proc(s_over, draw_over);
  layer_add_child(root, s_over);
  memset(&s_hold, 0, sizeof s_hold);
  s_hold.layer = s_over;
  s_hold.on_click = on_click;
  s_hold.on_hold = on_hold;
  bool holds = !pick_list() && s_kind != LIST_PICK_TAG;
  s_hold.hold_ms[BUTTON_ID_SELECT] = holds ? HOLD_MS : 0;
  window_set_click_config_provider_with_context(w, click_config, &s_hold);
}

static void appear(Window *w) {
  if (!tag_kind() && s_kind != LIST_SEARCH && (L.kind != s_kind || (!L.loaded && !L.asked))) list_request(&L, s_kind, 0);
  if (tag_kind()) send_cmd(CMD_GET_LIST, LIST_TAGS, 0, NULL);
  if (s_kind == LIST_SEARCH && !L.loaded && !L.asked) search(s_chip, NULL);
  if (!s_bars) s_bars = app_timer_register(bars_ms(), bars_tick, NULL);
#if defined(PBL_TOUCH)
  if (touch_service_is_enabled()) touch_service_subscribe(touch, NULL);
#endif
}

static void disappear(Window *w) {
#if defined(PBL_TOUCH)
  touch_service_unsubscribe();
#endif
  if (s_page_timer) { app_timer_cancel(s_page_timer); s_page_timer = NULL; }
  hold_cancel(&s_hold);
  if (s_bars) { app_timer_cancel(s_bars); s_bars = NULL; }
}

static void unload(Window *w) {
  menu_layer_destroy(s_ml); s_ml = NULL;
  layer_destroy(s_head); s_head = NULL;
  layer_destroy(s_over); s_over = NULL;
#if defined(PBL_MICROPHONE)
  if (s_dict) { dictation_session_destroy(s_dict); s_dict = NULL; }
#endif
  window_destroy(w);
  s_win = NULL;
}

void list_push(uint8_t kind) {
  if (s_win) {
    // one data list at a time: replace the one open
    window_stack_remove(s_win, false);
  }
  s_kind = kind;
  s_strip = false;
  s_off = 0;
  s_strip_drag = 0;
  if (kind == LIST_SEARCH) { s_chip = 1; s_said[0] = 0; }
  if (!tag_kind()) { memset(&L, 0, sizeof L); L.kind = kind; }
  s_win = window_create();
  window_set_background_color(s_win, T.bg);
  window_set_window_handlers(s_win, (WindowHandlers){ .load = load, .unload = unload, .appear = appear, .disappear = disappear });
  window_stack_push(s_win, true);
}

void list_refresh(uint32_t what) {
  if (!s_win || !s_ml) return;
  if (what & CH_THEME) {
    window_set_background_color(s_win, T.bg);
    menu_layer_set_highlight_colors(s_ml, T.accent, GColorBlack);
  }
  if (what & (CH_LIST | CH_TAGS | CH_THEME | CH_NOW)) {
    if (L.loaded && s_off && s_off >= L.total) {   // the list got shorter (a tag filter): back to its top
      s_off = 0;
      menu_layer_set_selected_index(s_ml, (MenuIndex){ 0, 0 }, MenuRowAlignTop, false);
    }
    menu_layer_reload_data(s_ml);
    if (what & CH_LIST) skip_heading(false);
    layer_mark_dirty(s_head);
    layer_mark_dirty(s_over);
  }
}
