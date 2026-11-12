// Talking to the Frequencies phone app: AppMessage in and out, list rows, pictures in chunks.
#include "freq.h"

Phone P;
List L;
// the featured row needs 8 rows, not 32: about 2.9 KB less (the 64 KB watches ran out of room)
#define F_BYTES (offsetof(List, rows) + F_ROWS * sizeof(Row))
List *Fp;   // made in comm_init
void featured_list_clear(void) { memset(Fp, 0, F_BYTES); }

// ---- outbox: one message at a time, a few waiting ----
typedef struct { uint8_t cmd; int32_t arg, arg2; char text[64]; bool has_text; } Out;
static Out s_q[8];
static int s_qn;
static bool s_busy;
static AppTimer *s_hello_timer;

static void pump(void);

static void lost(void) {
  if (!P.lost) {
    P.lost = true;
    notify(CH_CONN);
  }
}

static void send_now(const Out *o) {
  DictionaryIterator *it;
  if (app_message_outbox_begin(&it) != APP_MSG_OK) { s_busy = false; return; }
  dict_write_uint8(it, MESSAGE_KEY_CMD, o->cmd);
  dict_write_int32(it, MESSAGE_KEY_ARG, o->arg);
  if (o->arg2) dict_write_int32(it, MESSAGE_KEY_ARG2, o->arg2);
  if (o->has_text) dict_write_cstring(it, MESSAGE_KEY_TEXT, o->text);
  if (o->cmd == CMD_HELLO) {
    uint8_t caps = 0;
    if (PBL_IF_COLOR_ELSE(true, false)) caps |= 1;
    if (PBL_IF_ROUND_ELSE(true, false)) caps |= 2;
    GRect b = layer_get_bounds(window_get_root_layer(window_stack_get_top_window()));
    if (b.size.w >= 180) caps |= 4;
#if defined(PBL_TOUCH)
    caps |= 8;
#endif
#if defined(PBL_MICROPHONE)
    caps |= 16;
#endif
    dict_write_uint8(it, MESSAGE_KEY_WATCH, caps);
    dict_write_uint8(it, MESSAGE_KEY_PROTOCOL, PROTOCOL_VERSION);
  }
  s_busy = app_message_outbox_send() == APP_MSG_OK;
}

static void pump(void) {
  if (s_busy || s_qn == 0) return;
  Out o = s_q[0];
  memmove(&s_q[0], &s_q[1], sizeof(Out) * (s_qn - 1));
  s_qn--;
  s_busy = true;
  send_now(&o);
}

void send_cmd(uint8_t cmd, int32_t arg, int32_t arg2, const char *text) {
  if (s_qn >= 8) { memmove(&s_q[0], &s_q[1], sizeof(Out) * 7); s_qn = 7; }
  Out *o = &s_q[s_qn++];
  memset(o, 0, sizeof *o);
  o->cmd = cmd; o->arg = arg; o->arg2 = arg2;
  if (text) { strncpy(o->text, text, sizeof o->text - 1); o->has_text = true; }
  pump();
}

static void hello_timeout(void *d) {
  s_hello_timer = NULL;
  if (!P.connected) lost();
}

void comm_hello(void) {
  P.connected = false;
  send_cmd(CMD_HELLO, PROTOCOL_VERSION, 0, NULL);
  if (s_hello_timer) app_timer_cancel(s_hello_timer);
  s_hello_timer = app_timer_register(6000, hello_timeout, NULL);
}

static void out_sent(DictionaryIterator *it, void *ctx) { s_busy = false; pump(); }
static void out_failed(DictionaryIterator *it, AppMessageResult reason, void *ctx) {
  s_busy = false;
  APP_LOG(APP_LOG_LEVEL_WARNING, "send failed %d", (int)reason);
  if (reason == APP_MSG_NOT_CONNECTED || reason == APP_MSG_SEND_TIMEOUT || reason == APP_MSG_SEND_REJECTED) {
    s_qn = 0;
    lost();
    return;
  }
  pump();
}

// ---- pictures ----
static GBitmap *s_art;      // the picture coming in: chunks go straight into it (no second copy, since 191b)
static uint32_t s_art_total, s_art_id;
static uint8_t s_art_kind, s_art_slot;
static uint16_t s_art_w, s_art_h;

static GBitmap *make_bitmap(void) {
  bool mask = s_art_kind == ART_LOGO || !PBL_IF_COLOR_ELSE(true, false);
  if (!mask) return gbitmap_create_blank(GSize(s_art_w, s_art_h), GBitmapFormat8Bit);
  GColor *pal = malloc(sizeof(GColor) * 2);
  if (!pal) return NULL;
  pal[0] = s_art_kind == ART_LOGO ? GColorClear : GColorBlack;
  pal[1] = GColorWhite;
  GBitmap *bm = gbitmap_create_blank_with_palette(GSize(s_art_w, s_art_h), GBitmapFormat1BitPalette, pal, true);
  if (!bm) free(pal);
  return bm;
}

// one chunk of rows, packed tight by the phone, into the bitmap's own (maybe padded) rows
static void art_copy(uint32_t off, const uint8_t *src, uint32_t len) {
  bool mask = gbitmap_get_format(s_art) == GBitmapFormat1BitPalette;
  uint32_t stride = mask ? (s_art_w + 7) / 8 : s_art_w;
  uint8_t *data = gbitmap_get_data(s_art);
  uint32_t bpr = gbitmap_get_bytes_per_row(s_art);
  for (uint32_t pos = off, end = off + len; pos < end;) {
    uint32_t y = pos / stride, x = pos % stride, n = min_i(stride - x, end - pos);
    if (y >= s_art_h) break;
    if (x < bpr) memcpy(data + y * bpr + x, src + (pos - off), min_i(n, bpr - x));
    pos += n;
  }
}

static void art_done(void) {
  GBitmap *bm = s_art;
  s_art = NULL;
  if (!bm) {
    APP_LOG(APP_LOG_LEVEL_ERROR, "no memory for picture %d", s_art_kind);
    if (s_art_kind == ART_COVER) notify(CH_ART);
    return;
  }
  switch (s_art_kind) {
    case ART_COVER:
      if (P.cover) gbitmap_destroy(P.cover);
      if (P.cover_small) { gbitmap_destroy(P.cover_small); P.cover_small = NULL; }
      P.cover = bm; P.cover_id = s_art_id; P.cover_at = time(NULL);
      P.cover_small = shrink_bitmap(bm, is_large() ? 100 : 56);
      APP_LOG(APP_LOG_LEVEL_DEBUG, "cover in, %d bytes free", (int)heap_bytes_free());
      break;
    case ART_LOGO:
      if (P.logo) gbitmap_destroy(P.logo);
      P.logo = bm; P.logo_id = s_art_id;
      break;
    case ART_PHOTO:
      if (P.photo) gbitmap_destroy(P.photo);
      P.photo = bm; P.photo_id = s_art_id;
      break;
    case ART_FEATURED:
      if (s_art_slot < 8) {
        if (P.featured[s_art_slot]) gbitmap_destroy(P.featured[s_art_slot]);
        P.featured[s_art_slot] = bm; P.featured_id[s_art_slot] = s_art_id;
        notify(CH_FEATURED);
        return;
      }
      gbitmap_destroy(bm);
      return;
    default: gbitmap_destroy(bm); return;
  }
  notify(CH_ART);
}

static void art_tuple(DictionaryIterator *it) {
  Tuple *t;
  uint32_t off = 0;
  if ((t = dict_find(it, MESSAGE_KEY_ART_OFFSET))) off = t->value->uint32;
  if (off == 0) {
    if (s_art) { gbitmap_destroy(s_art); s_art = NULL; }
    s_art_id = (t = dict_find(it, MESSAGE_KEY_ART_ID)) ? t->value->uint32 : 0;
    s_art_kind = (t = dict_find(it, MESSAGE_KEY_ART_KIND)) ? t->value->uint8 : 0;
    s_art_slot = (t = dict_find(it, MESSAGE_KEY_ART_SLOT)) ? t->value->uint8 : 0;
    s_art_w = (t = dict_find(it, MESSAGE_KEY_ART_W)) ? t->value->uint16 : 0;
    s_art_h = (t = dict_find(it, MESSAGE_KEY_ART_H)) ? t->value->uint16 : 0;
    s_art_total = (t = dict_find(it, MESSAGE_KEY_ART_TOTAL)) ? t->value->uint32 : 0;
    if (s_art_total == 0) {  // "no picture" for this kind
      if (s_art_kind == ART_COVER && P.cover) {
        gbitmap_destroy(P.cover); P.cover = NULL; P.cover_id = 0;
        if (P.cover_small) { gbitmap_destroy(P.cover_small); P.cover_small = NULL; }
        notify(CH_ART);
      }
      if (s_art_kind == ART_LOGO && P.logo) { gbitmap_destroy(P.logo); P.logo = NULL; P.logo_id = 0; notify(CH_ART); }
      if (s_art_kind == ART_PHOTO && P.photo) { gbitmap_destroy(P.photo); P.photo = NULL; P.photo_id = 0; notify(CH_BIO); }
      if (s_art_kind == ART_FEATURED && s_art_slot < 8 && P.featured[s_art_slot]) {
        gbitmap_destroy(P.featured[s_art_slot]); P.featured[s_art_slot] = NULL; notify(CH_FEATURED);
      }
      return;
    }
    if (s_art_total > 24000 || !s_art_w || !s_art_h) return;
    // the picture already on screen: nothing to rebuild
    if (s_art_kind == ART_COVER && P.cover && P.cover_id == s_art_id) return;
    if (s_art_kind == ART_LOGO && P.logo && P.logo_id == s_art_id) return;
    if (s_art_kind == ART_PHOTO && P.photo && P.photo_id == s_art_id) return;
    // A new artist photo: the old one (and the bio's small copy) goes first. Both together didn't fit, so the new
    // photo was dropped and the watch kept the old one or none (user, 2026-11-10: photos not always carried over).
    if (s_art_kind == ART_PHOTO) {
      if (!bio_is_open()) return;  // only the bio shows it; a late one (the bio already closed) isn't kept
      photo_free();
    }
    // the heap is small (about 70 KB on the Time 2): the old cover's shrunk copy goes now (the old cover, drawn
    // smaller, stays on screen until the new one is in)
    if (s_art_kind == ART_COVER && P.cover_small) { gbitmap_destroy(P.cover_small); P.cover_small = NULL; }
    s_art = make_bitmap();
    // short of memory: the Add screen's featured pictures go first (it asks again when it shows), then the old cover
    if (!s_art && s_art_kind != ART_FEATURED) { featured_free(); s_art = make_bitmap(); }
    if (!s_art && s_art_kind == ART_COVER && P.cover) {
      gbitmap_destroy(P.cover); P.cover = NULL; P.cover_id = 0;
      s_art = make_bitmap();
    }
    if (!s_art) {
      APP_LOG(APP_LOG_LEVEL_ERROR, "no memory for %d bytes", (int)s_art_total);
      if (s_art_kind == ART_COVER) notify(CH_ART);
      return;
    }
  }
  if (!s_art) return;
  // chunks of another picture (the phone moved on mid-way) are dropped; since 191 every chunk carries ART_ID
  if (off && (t = dict_find(it, MESSAGE_KEY_ART_ID)) && t->value->uint32 != s_art_id) return;
  if ((t = dict_find(it, MESSAGE_KEY_ART_DATA))) {
    if (off + t->length > s_art_total) return;
    art_copy(off, t->value->data, t->length);
    if (off + t->length == s_art_total) art_done();
  }
}

// ---- list rows: packed records, little-endian ----
static uint16_t rd16(const uint8_t *p) { return p[0] | (p[1] << 8); }
static uint32_t rd32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }

static void put_row(List *l, const Row *r) {
  if (l->have > 0 && r->index >= l->base && r->index < l->base + l->have) { l->rows[r->index - l->base] = *r; return; }
  if (l->have == 0 || r->index != l->base + l->have || l->have >= (l == Fp ? F_ROWS : 32)) { l->base = r->index; l->have = 0; }
  l->rows[l->have++] = *r;
}

static void rows_tuple(List *l, const uint8_t *p, int n) {
  int i = 0;
  while (i + 13 <= n) {
    Row r;
    memset(&r, 0, sizeof r);
    r.index = rd16(p + i); r.id = rd32(p + i + 2); r.flags = p[i + 6]; r.color = p[i + 7]; r.time = (int32_t)rd32(p + i + 8);
    r.ntags = p[i + 12];
    i += 13;
    for (int k = 0; k < r.ntags && i < n; k++, i++) if (k < 4) r.tags[k] = p[i];
    if (r.ntags > 4) r.ntags = 4;
    if (i >= n) break;
    int ln = p[i++];
    if (i + ln > n) break;
    memcpy(r.name, p + i, min_i(ln, (int)sizeof r.name - 1)); i += ln;
    if (i >= n) break;
    int ls = p[i++];
    if (i + ls > n) break;
    memcpy(r.sub, p + i, min_i(ls, (int)sizeof r.sub - 1)); i += ls;
    put_row(l, &r);
  }
}

static void tags_tuple(const uint8_t *p, int n) {
  int i = 1, k = 0;
  int count = n > 0 ? p[0] : 0;
  while (k < count && k < MAX_TAGS && i + 5 <= n) {
    Tag *t = &P.tags[k];
    t->color = p[i]; t->state = p[i + 1]; t->count = rd16(p + i + 2);
    int ln = p[i + 4];
    i += 5;
    if (i + ln > n) break;
    memset(t->name, 0, sizeof t->name);
    memcpy(t->name, p + i, min_i(ln, (int)sizeof t->name - 1));
    i += ln;
    k++;
  }
  P.ntags = k;
}

void list_request(List *l, uint8_t kind, uint16_t start) {
  if (l->kind != kind) { memset(l, 0, sizeof *l); l->kind = kind; }
  l->asked = true; l->asked_at = start; l->asked_ms = now_ms();
  send_cmd(CMD_GET_LIST, kind, start, NULL);
}

const Row *list_peek(const List *l, uint16_t index) {
  if (l->have && index >= l->base && index < l->base + l->have) return &l->rows[index - l->base];
  return NULL;
}

// One request at a time: with hundreds of rows, asking on every draw flooded the phone and the list never filled.
const Row *list_row(List *l, uint16_t index) {
  const Row *r = list_peek(l, index);
  if (r || (l->loaded && index >= l->total)) return r;
  if (!l->asked || now_ms() - l->asked_ms > 2000) list_request(l, l->kind, index > 8 ? index - 8 : 0);
  return NULL;
}

static void copy_str(char *dst, int n, Tuple *t) {
  strncpy(dst, t->value->cstring, n - 1);
  dst[n - 1] = 0;
}

static void in_received(DictionaryIterator *it, void *ctx) {
  uint32_t what = 0;
  Tuple *t;
  if (!P.connected || P.lost) {
    P.connected = true;
    if (P.lost) { P.lost = false; what |= CH_CONN; }
  }
  if ((t = dict_find(it, MESSAGE_KEY_STATION))) { copy_str(P.station, sizeof P.station, t); what |= CH_NOW; }
  if ((t = dict_find(it, MESSAGE_KEY_SONG))) { copy_str(P.song, sizeof P.song, t); what |= CH_NOW; }
  if ((t = dict_find(it, MESSAGE_KEY_ARTIST))) { copy_str(P.artist, sizeof P.artist, t); what |= CH_NOW; }
  if ((t = dict_find(it, MESSAGE_KEY_STATE))) { P.state = t->value->uint8; what |= CH_NOW; }
  if ((t = dict_find(it, MESSAGE_KEY_FLAGS))) { P.flags = t->length >= 2 ? t->value->uint16 : t->value->uint8; what |= CH_NOW; }
  if ((t = dict_find(it, MESSAGE_KEY_SLEEP_LEFT))) {
    int32_t s = t->value->int32;
    P.sleep_end = s > 0 ? time(NULL) + s : 0;
    what |= CH_NOW;
  }
  if ((t = dict_find(it, MESSAGE_KEY_SLEEP_ACTION))) { P.sleep_action = t->value->uint8; what |= CH_NOW; }
  if ((t = dict_find(it, MESSAGE_KEY_AD_LEFT))) {
    int s = t->value->uint16;
    P.ad_end = s > 0 ? time(NULL) + s : 0;
    what |= CH_NOW;
  }
  if ((t = dict_find(it, MESSAGE_KEY_POS))) { P.pos = t->value->uint16; what |= CH_NOW; }
  if ((t = dict_find(it, MESSAGE_KEY_COUNT))) { P.count = t->value->uint16; what |= CH_NOW; }
  if ((t = dict_find(it, MESSAGE_KEY_THEME))) {
    theme_apply_bytes(t->value->data, t->length);
    persist_write_data(PK_THEME, t->value->data, min_i(t->length, 9));
    what |= CH_THEME;
  }
  if ((t = dict_find(it, MESSAGE_KEY_PREFS))) {
    memcpy(P.prefs, t->value->data, min_i(t->length, 4));
    persist_write_data(PK_PREFS, P.prefs, 4);
    if (t->length >= 6) {  // since 192: the expanded view's name line, set in the phone's Settings › Pebble too
      const uint8_t *d = (const uint8_t *)t->value->data;
      P.name_mode = d[4] <= 2 ? d[4] : 0;
      P.name_secs = d[5] >= 2 && d[5] <= 60 ? d[5] : 7;
      uint8_t nm[2] = { P.name_mode, P.name_secs };
      persist_write_data(PK_NAMES, nm, 2);
    }
    what |= CH_THEME;
  }
  if ((t = dict_find(it, MESSAGE_KEY_THEME_NAME))) { copy_str(P.theme_name, sizeof P.theme_name, t); what |= CH_THEME; }
  if ((t = dict_find(it, MESSAGE_KEY_SORT))) { P.sort_mode = t->value->uint8 & 0x7F; P.sort_desc = t->value->uint8 >> 7; what |= CH_LIST; }
  if ((t = dict_find(it, MESSAGE_KEY_TAGS))) { tags_tuple(t->value->data, t->length); what |= CH_TAGS; }
  if ((t = dict_find(it, MESSAGE_KEY_VOLUME)) && t->length >= 2) {   // [step, steps]
    const uint8_t *v = t->value->data;
    if (!P.vol_max || now_ms() - P.vol_ms > 800) { P.vol = v[0]; P.vol_max = v[1]; what |= CH_VOLUME; }
  }
  if ((t = dict_find(it, MESSAGE_KEY_STATION_SET)) && t->length >= (int)sizeof P.st) {
    if (!P.st_have || now_ms() - P.st_ms > 800) { memcpy(P.st, t->value->data, sizeof P.st); P.st_have = true; what |= CH_STATION; }
  }
  if ((t = dict_find(it, MESSAGE_KEY_STATION_COVER))) { copy_str(P.st_cover, sizeof P.st_cover, t); what |= CH_STATION; }
  if ((t = dict_find(it, MESSAGE_KEY_LIST_KIND))) {
    uint8_t kind = t->value->uint8;
    if (kind == L.kind) {
      Tuple *tt;
      if ((tt = dict_find(it, MESSAGE_KEY_LIST_TOTAL))) {
        uint16_t total = tt->value->uint16;
        if (total != L.total || !L.loaded) { L.total = total; }
        L.loaded = true;
      }
      if ((tt = dict_find(it, MESSAGE_KEY_LIST_ROWS))) rows_tuple(&L, tt->value->data, tt->length);
      L.asked = false;
      what |= CH_LIST;
    } else if (kind == LIST_FEATURED) {
      // the Add screen's featured row lives in its own list
      Tuple *tt;
      if (F.kind != LIST_FEATURED) { featured_list_clear(); F.kind = LIST_FEATURED; }
      if ((tt = dict_find(it, MESSAGE_KEY_LIST_TOTAL))) { F.total = tt->value->uint16; F.loaded = true; }
      if ((tt = dict_find(it, MESSAGE_KEY_LIST_ROWS))) rows_tuple(&F, tt->value->data, tt->length);
      F.asked = false;
      what |= CH_FEATURED;
    }
  }
  if ((t = dict_find(it, MESSAGE_KEY_BIO_NAME))) { copy_str(P.bio_name, sizeof P.bio_name, t); what |= CH_BIO; }
  if ((t = dict_find(it, MESSAGE_KEY_BIO_FROM))) { copy_str(P.bio_from, sizeof P.bio_from, t); what |= CH_BIO; }
  if ((t = dict_find(it, MESSAGE_KEY_BIO_TEXT))) {
    free(P.bio_text);
    P.bio_text = malloc(t->length + 1);
    if (P.bio_text) { memcpy(P.bio_text, t->value->cstring, t->length); P.bio_text[t->length] = 0; }
    what |= CH_BIO;
  }
  if ((t = dict_find(it, MESSAGE_KEY_VERDICT))) { P.verdict = t->value->uint8; what |= CH_BIO | CH_THUMBS; }
  if ((t = dict_find(it, MESSAGE_KEY_THUMBS_LEFT))) { P.thumbs_left = t->value->uint8; what |= CH_THUMBS; }
  if ((t = dict_find(it, MESSAGE_KEY_THUMBS_WHAT))) { P.thumbs_what = t->value->uint8; what |= CH_THUMBS; }
  if ((t = dict_find(it, MESSAGE_KEY_LYRIC_PREV))) { copy_str(P.lyric[0], sizeof P.lyric[0], t); what |= CH_LYRICS; }
  if ((t = dict_find(it, MESSAGE_KEY_LYRIC_CUR))) { copy_str(P.lyric[1], sizeof P.lyric[1], t); what |= CH_LYRICS; }
  if ((t = dict_find(it, MESSAGE_KEY_LYRIC_NEXT))) { copy_str(P.lyric[2], sizeof P.lyric[2], t); what |= CH_LYRICS; }
  if ((t = dict_find(it, MESSAGE_KEY_LYRIC_STATE))) { P.lyric_state = t->value->uint8; what |= CH_LYRICS; }
  if (dict_find(it, MESSAGE_KEY_ART_ID) || dict_find(it, MESSAGE_KEY_ART_DATA)) art_tuple(it);
  if (what & CH_NOW) {
    char last[120];
    snprintf(last, sizeof last, "%s\n%s\n%s", P.song, P.artist, P.station);
    persist_write_string(PK_LAST, last);
  }
  if (what) notify(what);
}

static void in_dropped(AppMessageResult reason, void *ctx) {
  APP_LOG(APP_LOG_LEVEL_WARNING, "dropped %d", (int)reason);
}

void comm_init(void) {
  void *fb = calloc(1, F_BYTES);  /* through void*: the list is used only up to rows[F_ROWS] */
  Fp = fb;
  app_message_register_inbox_received(in_received);
  app_message_register_inbox_dropped(in_dropped);
  app_message_register_outbox_sent(out_sent);
  app_message_register_outbox_failed(out_failed);
  uint32_t inbox = app_message_inbox_size_maximum();
  if (inbox > 4096) inbox = 4096;
  app_message_open(inbox, 256);
  if (persist_exists(PK_PREFS)) persist_read_data(PK_PREFS, P.prefs, 4);
  else { P.prefs[PREF_FOLLOW] = 1; P.prefs[PREF_GROW] = 1; }
  uint8_t nm[2] = { 0, 7 };
  if (persist_exists(PK_NAMES)) persist_read_data(PK_NAMES, nm, 2);
  P.name_mode = nm[0] <= 2 ? nm[0] : 0;
  P.name_secs = nm[1] >= 2 && nm[1] <= 60 ? nm[1] : 7;
}

void photo_free(void) {
  bio_drop_small();
  if (P.photo) { gbitmap_destroy(P.photo); P.photo = NULL; P.photo_id = 0; }
}

void featured_free(void) {
  for (int i = 0; i < 8; i++) if (P.featured[i]) { gbitmap_destroy(P.featured[i]); P.featured[i] = NULL; P.featured_id[i] = 0; }
  featured_list_clear();
}

void comm_deinit(void) {
  if (s_art) { gbitmap_destroy(s_art); s_art = NULL; }
  if (P.cover) gbitmap_destroy(P.cover);
  if (P.cover_small) gbitmap_destroy(P.cover_small);
  if (P.logo) gbitmap_destroy(P.logo);
  if (P.photo) gbitmap_destroy(P.photo);
  for (int i = 0; i < 8; i++) if (P.featured[i]) gbitmap_destroy(P.featured[i]);
  free(P.bio_text);
  memset(&P, 0, sizeof P);
}
