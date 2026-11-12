// Frequencies for Pebble: shared declarations.
// The watch shows what the Frequencies Android app plays and runs it, through
// AppMessage and PebbleKit Android 2 on the phone (no PebbleKit JS).
#pragma once
#include <pebble.h>

// ---- Protocol (keep in step with core/.../Pebble.kt on the phone and notes/pebble-protocol.md) ----
#define PROTOCOL_VERSION 1

enum {  // watch -> phone commands (key CMD)
  CMD_HELLO = 1, CMD_BYE, CMD_PLAY_PAUSE, CMD_NEXT, CMD_PREV, CMD_SCAN_TO, CMD_PLAY_ITEM, CMD_PLAY_RANDOM,
  CMD_STAR, CMD_SHUFFLE, CMD_SONG_ID, CMD_THUMB_UP, CMD_THUMB_DOWN, CMD_THUMB_UNDO, CMD_THUMB_SEND_NOW,
  CMD_SLEEP_SET, CMD_SLEEP_ACTION, CMD_GET_LIST, CMD_SET_TAG, CMD_FAVORITE, CMD_GET_BIO, CMD_LYRICS,
  CMD_LYRIC_HOLD, CMD_LYRIC_STEP, CMD_LYRIC_SYNC, CMD_SEARCH, CMD_LISTEN, CMD_SAVE, CMD_OPEN_SERVICE,
  CMD_SORT, CMD_PREF, CMD_FIND_MORE, CMD_GET_FEATURED, CMD_VOLUME,   // 34: ARG -1 = tell me, else the phone's media volume step
  CMD_STATION,   // 35 (phone 196+): the playing station's settings; ARG a STF_ field (-1 = tell me), ARG2 its value
};

enum {  // list kinds (LIST_KIND)
  LIST_SAVED = 1, LIST_TAGS, LIST_FAVORITES, LIST_HISTORY, LIST_SEARCH, LIST_FEATURED, LIST_PREV_FEATURED,
  LIST_THEMES, LIST_PICK_TAG, LIST_COVERS,   // 10: frequencies that can cover an opening ad
};

enum {  // playback state (STATE)
  ST_STOPPED = 0, ST_PLAYING, ST_PAUSED, ST_TUNING, ST_BUFFERING, ST_DOWN,
};

// FLAGS bits
#define FL_STARRED 0x01
#define FL_SHUFFLE 0x02
#define FL_SONG_ID 0x04
#define FL_LYRICS 0x08
#define FL_BIO 0x10
#define FL_SAVED 0x20
#define FL_HAS_SONG 0x40
#define FL_LOGO 0x80
#define FL_NO_STAR 0x100   // this song can't be a favorite (music folder, iBroadcast): no star (user, 2026-11-11)

// row flags
#define RF_PLAYING 0x01
#define RF_DOWN 0x02
#define RF_FAVORITE 0x04
#define RF_SAVED 0x08
#define RF_TAG_ON 0x10
#define RF_TAG_HIDDEN 0x20
#define RF_HEADING 0x40
#define RF_CURRENT 0x80

enum { ART_COVER = 1, ART_LOGO, ART_PHOTO, ART_FEATURED };
enum { PREF_FOLLOW = 0, PREF_THEME, PREF_ACCENT, PREF_GROW, PREF_NAMES, PREF_NAMES_SECS };  // CMD_PREF numbers

// ---- Theme ----
typedef struct {
  GColor bg, text, text2, text3, accent, playing, down, stripe, good;
} Theme;
extern Theme T;

// ---- What the phone told us ----
#define MAX_TAGS 24
typedef struct {
  char name[24];
  uint8_t color;   // GColor8 argb
  uint8_t state;   // 0 off, 1 shown, 2 hidden
  uint16_t count;
} Tag;

typedef struct {
  bool connected;      // the phone answered since the app opened
  bool lost;           // it stopped answering
  char station[44], song[68], artist[52];
  uint16_t flags;   // FL_*; the phone sends two bytes since 199
  uint8_t state, sleep_action;
  time_t sleep_end;    // 0 = off
  time_t ad_end;       // 0 = no ad covered
  uint16_t pos, count;
  uint8_t prefs[4];
  uint8_t name_mode, name_secs;  // expanded view: 0 artist and station (swapping), 1 artist, 2 station; seconds between swaps
  char theme_name[28];
  uint8_t sort_mode, sort_desc;
  // tags
  Tag tags[MAX_TAGS];
  uint8_t ntags;
  // bio
  char bio_name[52], bio_from[28];
  char *bio_text;
  uint8_t verdict;      // 0 none, 1 up, 2 down
  uint8_t thumbs_left;  // seconds before the report goes
  uint8_t thumbs_what;
  // lyrics
  char lyric[3][96];
  uint8_t lyric_state;  // 0 none, 1 live, 2 stuck
  // pictures
  GBitmap *cover, *logo, *photo;
  uint32_t cover_id, logo_id, photo_id;
  GBitmap *featured[8];
  uint32_t featured_id[8];
  time_t cover_at;      // when the cover arrived (picture grows 5 s later)
  GBitmap *cover_small; // the cover shrunk (averaged, not skipped pixels) for the normal Now playing view
  uint8_t vol, vol_max; // the phone's media volume: step and number of steps (0 = not known yet)
  uint32_t vol_ms;      // when the watch last moved it (the phone's echoes wait a moment)
  // the playing station's settings (STATION_SET, phone 196+): see ST_ and SF_ below
  uint8_t st[18];
  bool st_have;
  char st_cover[40];    // the frequency that covers its opening ad ("" = any frequency without ads)
  uint32_t st_ms;       // when the watch last changed one (the phone's echoes wait a moment)
} Phone;
extern Phone P;

// ---- List rows (Saved, Tags, History, Search...) ----
typedef struct {
  uint16_t index;
  uint32_t id;
  uint8_t flags, color;
  int32_t time;
  uint8_t ntags, tags[4];
  char name[44];
  char sub[52];
} Row;

typedef struct {
  uint8_t kind;
  uint16_t total;
  bool loaded;
  uint16_t base;        // index of rows[0]
  uint8_t have;         // rows filled from base
  bool asked;           // a GET_LIST is on its way: ask again only after it's answered or 2 s pass
  uint16_t asked_at;
  uint32_t asked_ms;
  Row rows[32];         // last: the featured list keeps only 8 (memory on 64 KB watches)
} List;

// ---- comm.c ----
void comm_init(void);
void comm_deinit(void);
void send_cmd(uint8_t cmd, int32_t arg, int32_t arg2, const char *text);
void comm_hello(void);
void list_request(List *l, uint8_t kind, uint16_t start);
void featured_free(void);   // the Add screen's pictures: freed when it closes or memory runs short
void photo_free(void);      // the artist photo and its small copy (comm.c)
void bio_drop_small(void);  // win_bio.c
bool bio_is_open(void);
const Row *list_row(List *l, uint16_t index);   // asks the phone when the row isn't here
const Row *list_peek(const List *l, uint16_t index);   // never asks
extern List L;        // the list on screen
typedef void (*Changed)(uint32_t what);
#define CH_NOW 0x01
#define CH_LIST 0x02
#define CH_BIO 0x04
#define CH_LYRICS 0x08
#define CH_ART 0x10
#define CH_THEME 0x20
#define CH_CONN 0x40
#define CH_THUMBS 0x80
#define CH_TAGS 0x100
#define CH_FEATURED 0x200
#define CH_VOLUME 0x400
#define CH_STATION 0x800

// Station settings (user's sketch, 2026-11-10). STATION_SET bytes:
#define ST_FLAGS 0      // SF_ bits
#define ST_GAIN 1       // level trim, half dB, +40 (0..80 = -20..+20 dB)
#define ST_AD_SECS 2    // how long the opening ad lasts, 1..120 s
#define ST_BUF 3        // pre-buffer, half seconds (0 = auto)
#define ST_WORST 4      // the measured worst start, half seconds (what auto uses)
#define ST_EQ3 5        // bass, mid, treble: dB +12 (0..24)
#define ST_EQ10 8       // ten bands: dB +12
#define SF_SAVED 0x01
#define SF_AD_OFFERED 0x02
#define SF_AD_ON 0x04
#define SF_AD_CHOSEN 0x08
#define SF_BUF_ON 0x10
#define SF_TALK 0x20
#define SF_EQ_ADV 0x40
// CMD_STATION fields (ARG); ARG2 is the value
enum { STF_ASK = -1, STF_GAIN = 0, STF_AD_ON, STF_AD_SECS, STF_AD_ANY, STF_AD_COVER, STF_BUF_ON, STF_BUF_SECS, STF_TALK, STF_EQ_RESET,
       STF_EQ3 = 10, STF_EQ10 = 20 };
void notify(uint32_t what);

// ---- draw.c ----
enum {
  IC_PLAY, IC_PAUSE, IC_PREV, IC_NEXT, IC_STAR, IC_STAR_O, IC_GLASS, IC_THUMB_DOWN, IC_THUMB_UP,
  IC_SONGID, IC_MOON, IC_SHUFFLE, IC_LIST, IC_LYRICS, IC_PERSON, IC_DEADPHONE, IC_RADIO, IC_CHECK,
  IC_NOTE, IC_TAG, IC_PLUS, IC_CLOCK, IC_GEAR, IC_MIC, IC_DOTS, IC_DICE, IC_SORT, IC_UP, IC_DOWN,
  IC_BANDAID, IC_NO, IC_MINUS, IC_SEARCH, IC_PHONE, IC_FREQ, IC_THUMB_DOWN_F, IC_THUMB_UP_F, IC_PALETTE, IC_VOLUME, IC_BRUSH, IC_EQ,
  IC_COUNT
};
void draw_icon(GContext *ctx, int icon, GRect r, GColor c);
void theme_load(void);
void theme_apply_bytes(const uint8_t *b, int n);
void draw_clock(GContext *ctx, GRect r, GTextAlignment align, GColor c, bool bold);
void draw_header(GContext *ctx, GRect b, const char *title, const char *extra);
#define HEADER_H 26
void draw_edge_icons(GContext *ctx, GRect b, const int icons[3], const GColor *cols);
void draw_hold_ring(GContext *ctx, GRect b, int slot, int icon, GColor c, int permille);
GRect edge_icon_rect(GRect b, int slot);
void draw_bars(GContext *ctx, GRect r, GColor c, int phase);
void draw_dial(GContext *ctx, GRect b, int y, int x0, int x1, int pos, int count);
int draw_pill(GContext *ctx, int x, int y, const Tag *t, int maxw, bool small);
void draw_shuffle(GContext *ctx, GRect r, int f, GColor c);
void draw_text(GContext *ctx, const char *s, GFont f, GRect r, GTextAlignment a, GColor c);
int text_h(const char *s, GFont f, int w);
void draw_bitmap_scaled(GContext *ctx, GBitmap *bm, GRect dst);
GBitmap *shrink_bitmap(GBitmap *src, int size);
void draw_logo(GContext *ctx, GBitmap *logo, GRect r, GColor c);
void now_saved(int delay_ms);  // Now Playing: the + turns into "Saved!" (win_now.c)
void draw_logo_up(GContext *ctx, GBitmap *logo, GRect r, GColor c, int max_k);
GColor gc8(uint8_t argb);
void draw_dotted_hline(GContext *ctx, int x0, int x1, int y, GColor c);
const char *fmt_time(time_t t, char *buf, int n);

// ---- buttons.c: one press/hold engine for every custom screen ----
typedef struct Hold Hold;
typedef void (*HoldFn)(Hold *h, ButtonId b);
struct Hold {
  Layer *layer;           // redrawn while a ring fills
  uint16_t hold_ms[4];    // 0 = no hold on that button (index = ButtonId)
  HoldFn on_click, on_hold;
  void (*on_combo)(Hold *h);
  uint16_t combo_ms;      // Up+Down together: 0 = at once
  // state
  uint8_t down;           // bit per button
  bool fired[4], combo_fired;
  uint32_t t0[4];
  AppTimer *timer;
  void *ctx;
  bool ignore_up;         // the press woke a grown picture: nothing else
};
void hold_click_config(void *context);   // pass the Hold* as click config context
int hold_progress(Hold *h, ButtonId b);  // 0..1000 while held (only buttons with a hold)
int combo_progress(Hold *h);
void hold_cancel(Hold *h);
#if defined(PBL_TOUCH)
bool edge_back(const TouchEvent *e);   // true when a swipe from the left edge to the middle ends: go back
bool edge_tracking(void);              // that swipe is under way: other gestures wait
#endif
void edge_back_subscribe(void);         // for screens with no touch of their own
void touch_off(void);

// ---- windows ----
void now_push(void);
void bio_push_quiet(void);
void lyrics_push_quiet(void);
void back_to_main(void);
extern List *Fp;
#define F (*Fp)
#define F_ROWS 8
void featured_list_clear(void);   // the Add screen's featured row
void now_refresh(uint32_t what);
void main_menu_push(void);
void actions_push(void);
void sleep_push(void);
void bio_push(void);
void lyrics_push(void);
void thumbs_ask_push(void);
// pulling a screen back to Now Playing with the thumb (pull.c, touch watches)
typedef struct {
  Window *win;
  Layer *content, *card;
  int drag, to, dir;   // dir 1: pulled down to go back (bio); -1: pulled up (lyrics)
  AppTimer *timer;
} Pull;
void pull_init(Pull *p, Window *w, Layer *content, int dir);
void pull_deinit(Pull *p);
void pull_move(Pull *p, int dy);
void pull_release(Pull *p);
bool pull_busy(Pull *p);
void hold_click_config_select(void *context);
void thumbs_send_push(void);
void list_push(uint8_t kind);
void sort_push(void);
void services_push(uint32_t id, const char *song);
void add_push(void);
void hint_push(uint8_t then_kind);
void settings_push(void);
void station_set(int field, int value);  // CMD_STATION, and the watch's copy follows at once
void dead_push(void);
void dead_hide(void);
bool dead_showing(void);
void glance_update(void);
void menus_refresh(uint32_t what);
void list_refresh(uint32_t what);
void bio_refresh(uint32_t what);
void lyrics_refresh(uint32_t what);
void thumbs_refresh(uint32_t what);
void add_refresh(uint32_t what);
bool hint_seen(void);
bool swipe_hint_seen(void);
void swipe_hint_push(void);

#ifndef HOLD_MS
#define HOLD_MS 700   // every hold in the app (0.7 s; the user found 0.4 s too short)
#endif

// persist keys
#define PK_THEME 1
#define PK_PREFS 2
#define PK_HINT 3
#define PK_LAST 4
#define PK_NAMES 5
#define PK_SWIPE_HINT 6

int min_i(int a, int b);
int max_i(int a, int b);
bool is_large(void);   // emery / gabbro sizes
uint32_t now_ms(void);
void input_seen(void);   // a press or touch: the playing bars speed up again
uint32_t bars_ms(void);  // the playing bars' frame time now
GFont clock_font(void);
