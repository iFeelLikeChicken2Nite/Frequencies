# Frequencies for Android

Native Kotlin + Jetpack Compose + Material 3 + Media3. Plan and batch order:
`notes/android-survey.md` in the project files; what matches the desktop:
`notes/android-parity.md`.

## Modules

| Module | What | Builds where |
|---|---|---|
| `core` | Desktop formats: stations.json, settings.json, history.json, full backup, featured entries (default frequencies), .pls/.m3u, stream titles; the desktop's now-playing rules, lyrics lookups (lrclib, lyrics.ovh), EQ filters and art matching. Unit-tested on files the desktop wrote. | Anywhere (plain Kotlin/JVM) |
| `songid` | Shazam signature, 16 kHz downsampler, Shazam client. | Anywhere |
| `app` | The Android app: screens, PlaybackService (MediaLibraryService). | A PC with the Android SDK |

`app` joins the build only when `local.properties` exists (it names the SDK),
so `./gradlew test` runs the `core` and `songid` tests in the cloud sandbox,
which can't reach Google's Maven.

## Build (Windows)

Tools: JDK 21 in `C:\Users\YOU\Android\jdk-21`, SDK in `C:\Users\YOU\Android\Sdk`.
Project: `C:\Users\YOU\frequencies-android`. `local.properties`:

    sdk.dir=C\:\\Users\\YOU\\Android\\Sdk

Release signing: the key and its passwords live in `C:\Users\YOU\Android\keys\`
(never in the project, a zip or a chat). `C:\Users\YOU\.gradle\gradle.properties` says where:

    frequencies.signing=C:\\Users\\YOU\\Android\\keys\\signing.properties

with `storeFile`, `storePassword`, `keyAlias`, `keyPassword` in that file.

    set JAVA_HOME=C:\Users\YOU\Android\jdk-21
    gradlew assembleDevRelease assembleUserRelease
    adb install --user 0 -r app\build\outputs\apk\dev\release\app-dev-release.apk
    adb install --user 10 -r app\build\outputs\apk\user\release\app-user-release.apk

Variants, like the desktop's `--dev`: `dev` (id `com.frequencies.app.dev`, named
"Frequencies Dev", shows the Developer settings, installs beside the user build),
`user` (`com.frequencies.app`, signed with your key, for your own phones) and
`fdroid` (`com.frequencies.app`, what F-Droid builds: never signed here, no dev
code paths, nothing that needs a secret, Android Auto kept). Every update must be
signed with the same key, or phones refuse it.

## F-Droid

F-Droid builds `fdroidRelease` from the public repository
(github.com/iFeelLikeChicken2Nite/Frequencies, this project in `android/`),
signs it with F-Droid's key and delivers updates; the app has no updater of
its own. The store text is in `fastlane/metadata/android/en-US/` at the top
of the public repository (F-Droid only looks there); the draft entry for
F-Droid's list is `fdroid/com.frequencies.app.yml`, and the steps to submit it
are in `fdroid/SUBMITTING.md`. Each release: bump `frequencies.build`, add
`fastlane/metadata/android/en-US/changelogs/<build>.txt`, and tag the commit
`android-1.5.<build>` on GitHub; F-Droid picks the tag up by itself.

To check what F-Droid will make, from a clean clone and without local.properties:

    set ANDROID_HOME=C:\Users\YOU\Android\Sdk
    gradlew test assembleFdroidRelease

The build number is `frequencies.build` in `gradle.properties`: the project's one
counter shared with the desktop (versionCode = N, versionName = 1.5.N).

## Checks

    ./gradlew test
    python3 tools/check_backup_on_desktop.py <desktop source>/src

The second one reads the backup the phone code wrote (`core/build/phone-backup.json`)
with the desktop's own `storage.read_backup`.

## Themes and accent

Settings > Appearance has the desktop's 22 shell themes and its accent
choices. `core/src/main/resources/com/frequencies/core/themes.json` is
written from the desktop's `src/themes.py`; never edit it by hand. After a
desktop theme is added or changed:

    python3 tools/export_themes.py <desktop source>/src

`ThemeScheme` lays a theme onto Material 3's roles and nudges any text color
that would read below WCAG AA (4.5:1; 3:1 for icons and borders);
`ThemeSchemeTest` checks every theme with ~100 accents. OG follows Light or
dark (Light = Flashbang); any other theme shows as it is.

Follow the artwork is the desktop's own pick (`ArtAccent`: theme.py's
dominant_color with Qt 5.15's 24x24 nearest-pixel scale and integer HSL,
readable_accent, the one-second eased hue fade). `ArtAccentTest` compares it
with answers the desktop's own code gave for the same pictures
(`tools/make_accent_fixtures.py`, needs PyQt5). The phone's accent choice is
`android_accent`; `accent_source` only ever gets the desktop's values
("artwork", "desktop" = Material You here, "fixed"), and a change made on the
desktop wins. Phone -> desktop -> phone:

    ./gradlew :core:test
    python3 tools/check_accent_on_desktop.py <desktop source>/src
    ./gradlew :core:test

## Look (VO1, build 157)

The desktop's look on the phone (spec: notes/android-overhaul-spec.md in the
Claude project). The tokens come from the theme (`ThemeScheme`, Material's
roles plus `FreqColors` for playing, down, bar and stripes); the type is
Noto Sans, bundled in `app/src/main/res/font` (Regular, Medium, SemiBold,
unhinted, from the official Noto release; SIL Open Font License 1.1 in
`app/src/main/assets/licenses/noto-sans-OFL.txt`, shown under Settings >
Library and tools > About).

`core` `Glyphs` is the desktop's `shell.py` icon drawing (24-unit grid,
same frames, steps and timings, `GlyphMotion`); the app draws it as Compose
vectors (`ui/Glyphs.kt`). `GlyphsTest` renders every glyph and frame and
compares it with what the desktop's own code painted
(`core/src/test/resources/glyphs`, made by `tools/make_glyph_fixtures.py`,
needs PyQt5):

    QT_QPA_PLATFORM=offscreen python3 tools/make_glyph_fixtures.py <desktop source>/src core/src/test/resources/glyphs

`LookTest` checks the Settings tabs (each setting the screen writes on exactly
one) and the new screens' colors in all 22 themes. Remove animations or
battery saver makes every glyph jump to its end and the playing row's bars
stand still.

UI tests (Compose, `app/src/androidTest`) run on a phone through the debug
build (`com.frequencies.app.dev.debug`, never the signed ones). On the Pixel,
for the primary profile only:

    gradlew assembleDevDebug assembleDevDebugAndroidTest
    adb install --user 0 -r app\build\outputs\apk\dev\debug\app-dev-debug.apk
    adb install --user 0 -r app\build\outputs\apk\androidTest\dev\debug\app-dev-debug-androidTest.apk
    adb shell am instrument --user 0 -w com.frequencies.app.dev.debug.test/androidx.test.runner.AndroidJUnitRunner
    adb uninstall com.frequencies.app.dev.debug.test
    adb uninstall com.frequencies.app.dev.debug

## Song ID

`songid` is a Kotlin port of shazamio_core 2.0.0's signature maker (SongRec's
algorithm). `SongIdTest` checks it byte for byte against signatures the desktop's
own shazamio_core made from the same 16 kHz samples (`songid/src/test/resources/desktop`,
made with `tools/make_songid_fixtures.sh`: a Rust harness built from shazamio_core's
source dumps the samples it fingerprints). Two details make the floats match:
the FFT's twiddle factors come from glibc's cosf/sinf (embedded as bit patterns,
`Twiddles.kt`), and ln is glibc's logf. The downsampler (Kaiser-windowed sinc)
can't match rubato bit for bit; `DownsamplerTest` checks the peaks agree.

On the phone, `AudioTap` (a Media3 audio processor, before the EQ) keeps the last
12 s of what the station plays; every 30 s the last 6 s go to Shazam. No second
connection to the station.

## Streaming services, Browse and band logos (AP10b)

- **iBroadcast** (`core` `IBroadcast`, `IBroadcastAccount`; app `IBroadcastKeeper`):
  the desktop's OAuth sign-in with PKCE in a Custom Tab (the phone's browser,
  never a web view), answered on the desktop's registered loopback address
  `http://127.0.0.1:8731/callback` by a one-request server in the app
  (`RedirectCatcher`). The sign-in is `files/frequencies/ibroadcast.json`;
  a background loop renews it with ten minutes left (looks once a minute,
  five minutes between automatic renewals). Each track is signed when it
  plays (`freqsvc://play/<station>/<n>`, `PlaybackService.serviceSpec`);
  a refused track renews the sign-in and is tried once more before the
  station is marked down. Track names, covers and lyrics come from the
  station's saved rows; Song ID doesn't run on them.
- **Twitch** (`core` `Twitch`): the GQL playback token and the usher stream
  list, the audio-only rendition (also what Android Auto plays). The
  opening ad is covered (`PlaybackService.AdCover`): a second player plays
  another saved frequency while the channel plays its ad at volume 0, then
  static, then the channel. No chat on Android.
- **YouTube** (NewPipeExtractor, pinned in `libs.versions.toml`, from
  JitPack; core library desugaring on): a playlist or channel listed once a
  day (`YouTube.Cache`), each video resolved when it plays.
- **Browse** tab: today's Featured list (`FeaturedList`, the curator's own
  words, cached a day) and the directory by Most listened, Trending, Random,
  Most voted, New and Name (`RadioBrowser`).
- **Band logos** (`BandLogos`, `ArtistKit.checkLogos`): logo-pack.json beside
  the photo pack, its own folder, Settings > Band logos (on by default).
- **Static**: the mix re-rolls every 1.6-3.2 s (`StaticPlayer`, two sets of
  players so a re-roll never leaves a gap); the hand-over waits for the
  level meter (`AudioTap.levelDb`: above -55 dB for 0.4 s, 45 s at most).
- **Tips** (`core` `Hints`): one-time Material tip cards on Add, Saved,
  the QR window and Settings; Settings > Show the tips again.

Live Twitch check on the build PC (nothing is saved):

    gradlew :core:test --tests "*LiveCheckTest*" -Pfrequencies.live=true

## Android Auto

`PlaybackService` is the whole of it (no custom screens, as Auto's media rules
ask): tabs Saved (manual order), Recent, Tags and Live now (the last three
only once they have something), stations as a grid of their pictures, search,
and "play … on Frequencies" by name, custom name or tag (`core` `Browse`, with
tests). Next and previous frequency wrap around the list and pass over
frequencies marked down (never an iBroadcast one). Station pictures
reach Auto as `content://<app id>.art/…` links (`ArtProvider`, read only).

Desktop Head Unit test (phone plugged in, Android Auto's developer settings
on with Unknown sources, and "Start head unit server" chosen in its menu):

    adb forward tcp:5277 tcp:5277
    C:\Users\YOU\Android\Sdk\extras\google\auto\desktop-head-unit.exe

## Artist info, photos and thumbs down

Now Playing's Artist tab is the desktop's artist panel (`core` ArtistInfo,
ArtistPhotos, ArtistOverrides, ArtistLookup): MusicBrainz + Wikipedia, then
Wikipedia alone, then Discogs; a photo from Wikidata, Deezer or Apple when
the biography has none; talk stations keep their own info. Which half of a
stream's "A - B" is the artist is asked of MusicBrainz
(`ArtistInfo.splitArtistTitle`). The photo pack (Settings > Lyrics and
artists, off by default) and the developer's bio fixes come from the pack
address (`PackUpdater`: the zip in base32 twice, checked against its
sha256). A thumbs down asks Bio / Photo / Both and, 15 s later, posts it to
your report Worker (desktop `cloudflare/report-worker.js`, the request
format in its `cloudflare/README.md`; `ThumbsDown`, `ReportPoster`), which
files the issue; no address, or a Worker that didn't file it, opens the
pre-filled issue in the browser. Android Auto has a one-tap thumbs down
that sends Both. No build carries a token of any kind.
`ArtistTest` checks the names, talk stations, issue
wording and a real photo pack against the desktop's own code
(`tools/make_artist_fixtures.py`, no PyQt5 needed).

## Shipped settings (assets/shipped)

What the desktop build ships from your dev build's picks lives here as
plain public data, committed with the source, because F-Droid builds from
the public tree and nothing else reaches its build:
artist_overrides.json (your text fixes), photo_pack.json (photo pack,
report-service and picture-link addresses), station_overrides.json (talk
stations) and twitch_app.json (the Twitch Client ID, a public value). The
default frequencies are in core (`default_stations.json`). The desktop's
visualizer list isn't needed (no projectM on Android). To refresh them after
changing things in the desktop dev build:

    py -3 tools\ship_assets.py C:\path\to\the\desktop\source\folder

It runs the desktop's own ship tools on a copy of %APPDATA%\frequencies (or
`--config <folder>`, or FREQUENCIES_DEV_CONFIG) with every token file
deleted first. Every app build checks the assets (`checkShippedAssets`):
nothing else in shipped/, no token file anywhere, no token-looking text.
The static recordings in `assets/static/` are optional: a build without
them tunes in silently.

## Health checks and Schedule

`core` `Health` is the desktop's health.json and its rules (storage.py,
workers.probe_stream / LaunchCheck, bridge._step_past_down); `Schedule` is
schedule.py and clock.py. `HealthTest` and `ScheduleTest` compare them with
answers the desktop's own code gave (`tools/make_schedule_fixtures.py`, no
PyQt5 needed):

    python3 -I tools/make_schedule_fixtures.py <desktop source>/src core/src/test/resources/schedule

On the phone (`HealthChecks`, `ScheduleWatch`): every saved stream is checked
8 s after launch (4 at a time; if the first 8 all fail the phone is taken to
be offline and nothing is marked), frequencies marked down are re-checked
every `health_recheck_minutes` while the app runs, and a WorkManager job
(`HealthWorker`, network required) does the launch sweep about once a day;
"Re-check frequencies that are down" in Settings turns the last two off. A
station that fails when played is marked down at once, one that never makes
a sound in 10 s or stalls for `stall_timeout` s too (and the next one plays
when "Skip to the next frequency…" is on); connecting clears the mark. The
Schedule screen opens from Saved Frequencies' top bar; reminders are the
only notifications (channel "Show reminders"), and nothing starts a show.

## Logs

`adb logcat -s Frequencies` shows `[stream]` (playlist picks, titles, failures),
`[static]` (hand-over), `[auto]` (what Android Auto asked to play or search),
`[songid]` (song checks), `[lyrics]`, `[art]` (covers, art upgrade), `[artist]` (bios, photos, which half of a stream title is the artist), `[talk]`, `[pack]` (photo pack, bio fixes, thumbs-down reports), `[sleep]`,
`[health]` (launch, daily and re-checks), `[tune]` (a stream that never
started), `[skip]` (next/previous passing over frequencies marked down), `[shuffle]` (shuffle on/off and its random picks) and
`[schedule]` (reminders) and `[sync]` (pairing, finding the computer, sessions, what was taken) lines.

## Batch 11 (AP11, build 161)

- Picture links on share (desktop build 128): `core` `ArtLink` is
  artlink.py. Opening a station's QR code uploads a picture the user chose
  (no link yet, not from a code) to the "art" address of
  `assets/shipped/photo_pack.json` (the developer's Cloudflare Worker,
  `cloudflare/art-worker.js` in the desktop source; not a secret, the same
  plain-data file the report address is in). `POST <address>/upload`,
  1.5 MB at most (bigger pictures shrink to 1200 px first), answer
  `{"url": ...}`; the link is saved as `art_url` + `art_url_sha` and the
  code carries it. The share buttons wait for it; a failed upload keeps the
  small copy with the desktop's message.
- Pre-buffering: `buffer_enabled` / `buffer_seconds` (0 = the measured
  `connect_worst`, which the phone now learns too), 1–60 s, the desktop's
  wording. `PrebufferLoadControl` (a DefaultLoadControl) holds the start,
  and the restart after a drop-out, until that much is buffered (at most
  the target + 12 s); other stations keep Media3's normal buffer.
- Twitch ad cover controls in Station settings > Sound: the desktop's
  `ad_override`, `ad_seconds`, `ad_filler_kind`, `ad_filler_station_id`
  (written the way the desktop's dialog writes them).
- "ICY 200 OK" servers (old SHOUTcast, desktop task 220): OkHttp reads that
  status line as HTTP/1.0, so the phone plays them with no relay.
  `IcyStatusTest` keeps it so with a tiny local server; the live check
  `oldShoutcastServerPlays` tries Radio Caprice's :9003 server
  (`-Pfrequencies.live=true`).

## Batch 14a (CV1a, build 162)

- Comfort Now Playing (notes/android-comfort-view-spec.md, sections 3, 5b,
  7, 9): on a phone, Now Playing and Comfort View are one screen. One state
  (`core` `Comfort`), Out or Comfort, saved across rotation; 20 s (7 s and
  a setting from build 175) without a touch (any touch, scrolling the lyrics too) goes to Comfort when "Fade
  into Comfort on its own" is on (`android_comfort_auto`, on); a new song
  comes back Out; a tap on the artwork, the small header or the corners
  button always works. A screen reader on: no automatic changes. One
  progress value drives the artwork (drawn at its Out size and scaled, so
  pictures never reload), the title (22 to 16 sp) and the body in the
  layout and draw phases (`ComfortHero`). Tabs Lyrics | Artist right above
  five even controls (Equalizer, Previous, Play, Next, Shuffle); the sleep
  timer moved to the top bar. Only the status bar hides in Comfort. "Keep
  the screen on in Comfort" (`android_comfort_keep_awake`, off): only in
  Comfort while playing. (Sideways and wide layouts: build 163, below.)
- Shuffle (`core` `Shuffle`): the desktop's `shuffle` key; Next picks at
  random among the others, passing over the ones marked down unless that is
  all there is, never one that can't play (offline, a folder not on this
  phone). Previous is always the one played before (`Shuffle.BackStack`,
  bridge._back_stack), with shuffle on or off. `local_station_ratio`
  ("Custom station frequency", Settings > Frequencies): 0 = no weighting
  (what desktop 160 does; its new shell keeps the key but has no slider
  any more), N% = a custom station (local files or YouTube) N% of the time,
  never twice running (the old desktop window's `_weighted_choice`).
  `StationPlayer` reports it as Media3's shuffle mode (ExoPlayer's own
  shuffle order stays off) and the notification and Android Auto get a
  Shuffle button (`com.frequencies.SHUFFLE`).
- Glyphs: shuffle_0..4 and next_shuffle1..4 from desktop shell.py, 30 ms
  a step (`GlyphsTest` fixtures from `tools/make_glyph_fixtures.py`).

## Batch 14b (CV1b, build 163)

- Moving background (comfort spec 8): `ui/MovingBackground.kt`
  `ComfortBackground` replaces the still wash (now `StillWash` in
  Chrome.kt, what "Moving background: Off" shows). Desktop
  nowplaying.py `_paint_aurora` ported in `core` `Aurora`: four radial
  pools of the accent's hue (saturation 65-95%), Screen blend on dark
  themes, 34% paint on light ones, under a veil of bg0 that starts at
  24% (dark) / 30% (light) and rises in 4% steps until text and text2
  read at 4.5:1 and the accent (the sung line, large semibold) at 3:1 on
  every overlap of the four pools (`Aurora.look`, `AuroraTest` over all
  22 themes x 10 accents). Drawn in the draw phase of its own layer;
  the time is published at most 30 a second, 15 after two minutes
  without a touch; play/pause eases the drift over 450 ms; a new accent
  cross-fades over 1 s. `Aurora.run`: Remove animations = the still
  frame (t = 14); out of sight = the clock stops; "Auto" also stands
  still with Battery Saver or under 20% battery not charging; "Always"
  ignores those two. Setting `android_moving_bg` (auto, always, off).
- Layouts by window size (`Comfort.shape`, spec 2): Upright (Compact
  width; a Medium-wide window standing upright too, cover up to 420),
  Sideways (Compact height, wider than tall: `SidewaysComfort`), Wide
  (Medium/Expanded both ways: `WideComfort`, cover 340 to 150, artist
  column 240 with the logo on top as desktop `LogoName`, the player bar's
  Comfort button switches the retract). Same state machine for all three.
- Band logo on the artist photo (spec 6): `PhotoLogo` places it with
  `BandLogos.onPhoto` (desktop `header_box` + `place`, bounds inset 4),
  fades in sliding up once the photo shows, outlined against the photo's
  bottom band (`bottomTone`); no logo or no photo: the name as plain text
  in the same spot. Golden numbers from the desktop's own code:
  `tools/make_logo_fixtures.py` (PyQt5) writes
  `core/src/test/resources/logo/placement.json`.


## Batch 13 (AP13, build 164): sync with the desktop

Same-network sync with a paired desktop (desktop build 160, task 126).
The contract is `notes/sync-protocol.md` in the project files; this is
its section 8.

- `core`: `SyncCrypto` (P-256 ECDH, HKDF-SHA256, HMAC, AES-256-GCM, all
  java.security / javax.crypto), `SyncWire` (frames, codes, the QR text,
  the pair and hello handshakes, the encrypted channel, the UDP probe,
  TXT records, version check), `SyncData` (stamps, `observe`,
  `buildState`, `merge`: sync_core's rules), `SyncPhone` (the phone's
  side of a session over a `SyncLibrary`), `SyncSession` (one short
  session: hello, state, `art?`/`art`, ping, `bye`).
- Phone-only keys never travel: settings outside section 6.2's first
  list (every `android_*` key, volume, sign-ins…), and the station keys
  in `SyncData.NEVER_SENT`, which adds the phone's folder link
  `android_folder` to the protocol's four. Match the theme now writes the
  desktop's own `accent_source` "theme" (the desktop has it since 151).
- App: `Sync.kt` `SyncKeeper` (one per app): files in `files/sync/`
  (sync.json, sync-peers.json, sync-state.json; allowBackup is off, so
  no cloud backup copies the id or keys), NsdManager browsing of
  `_frequencies._tcp` plus the UDP probe on 47615 every 5 s, a
  MulticastLock only while looking, sessions only while MainActivity is
  started (3 s grace for a rotation; then each session finishes its
  pictures, at most 8 s, and says `bye`). The phone never listens.
  Library implements `SyncLibrary` (`edit`); History's Clear writes
  `history_cleared_at` so the computer clears the same rows.
- UI (`ui/SyncSettings.kt`): Settings > Library and tools > "Sync with
  another device" with the desktop's wording; "Pair a device…" scans the
  computer's QR code or takes a typed code. A sync QR code scanned from
  Add Frequency opens the same pairing.
- Tests: `SyncCryptoTest` (the desktop's `tools/sync_vectors.py` output,
  byte for byte), `SyncDataTest` (section 7's cases), `SyncWireTest`
  (codes, QR, datagrams, version 2 refused and logged) and
  `SyncDesktopTest`: the phone against the desktop's own sync_core and
  storage (`tools/sync_desktop_peer.py`), over TCP:

      ./gradlew :core:test --tests '*SyncDesktopTest*' -Pfrequencies.desktop=<desktop source>/src

  (needs python3 with `cryptography`; skipped without the property).

## Batch 15 (AP15, build 166): twins keep both sides, no Custom station frequency

Matches desktop build 165 (Batch CL, tasks 221/222; sync-protocol.md 6.1
"Joining twins" and 6.2).

- `SyncData.merge`: when an incoming station is this phone's twin (same
  address, another id), `joinTwins` runs after the fields are merged by
  stamp, as desktop `sync_core._join_twins`: `play_count` and
  `last_played` take the larger of the two copies, `tags` both lists
  (`tagUnion`: the smaller id's copy first, compared trimmed and
  lowercased, first spelling kept, empty ones dropped). A joined value
  that differs from what the stamps chose gets a fresh stamp of this
  phone's (above the field's old `t`), so a device that doesn't join
  twins takes it too. A tombstoned twin's old id re-points to the one
  that stays, never to itself.
- "Custom station frequency" (`local_station_ratio`) is removed: no
  slider (Settings > Frequencies), no weighting in `Shuffle.next`, not
  in `SHARED_SETTINGS` or the defaults, and `Settings.REMOVED` drops it
  when settings.json or a backup is read (desktop
  `storage.REMOVED_SETTINGS`). Other unknown keys still round-trip.
- Tests: `SyncDataTest` twin cases (the 164 regression: both sides end
  identical and settle; either side first; an old device takes the
  joined values), `SyncDesktopTest.twinsKeepBothSidesDetailsWithTheDesktop`
  and `customStationFrequencyNeverTravels` against desktop 165's own
  sync_core, `ShuffleTest.customStationFrequencyIsGone`.

## Batch 20 (AP20, build 175): Comfort delay setting, swipe hint on Settings tabs, six YouTube defaults

- Comfort View: the artwork moves aside after 7 seconds without a touch
  (was 20). Settings > Appearance "Fade into Comfort after": a slider of
  whole seconds, 5 to 20, under "Fade into Comfort on its own" (grayed
  out while that is off), right above "Keep the screen on in Comfort".
  Key `android_comfort_retract_seconds` (Android only, like the other
  Comfort keys: not in SHARED_SETTINGS, never sent or overwritten by
  sync); read through `Comfort.retractSeconds`, rounded and kept to 5-20,
  anything else gives 7.
- Settings tabs (`TextTabs(edge = …)`): while tabs are out of sight, that
  side of the row fades out and shows a round arrow button (tap scrolls
  that way; TalkBack "More tabs" / "Earlier tabs"); the first time the
  row shows in a run of the app it slides a little left and back like a
  swipe (not with Remove animations on). Every tab row now scrolls the
  chosen tab into view (a link to Library and tools shows it).
- Default stations: desktop build 174's six YouTube stations after the
  13 (Frequencies AUX AUTHORITY, SYNTH HAPPENS, NOISE COMPLAINT, FRONT
  ROW, CONTEXT CUT, COLLISION COURSE), `default_stations.json` copied
  from desktop 174 byte for byte in content (same addresses and
  pictures, so they join as twins in sync). Fresh profiles only.
  `SyncData.addressFixes` now skips `youtube://` addresses (their "?"
  is a playlist id, not the old-address case).
- Tests: `ComfortTest` +2 (7 s default, 5-20 clamp), `LibraryTest` (19
  defaults against a fresh desktop 174 profile), `SyncDesktopTest`
  against desktop 174 (a fresh phone and desktop end with 19, no
  duplicates).

## Batch 19 (AP19, build 173): eight Add tiles, a real Previously featured button, desktop 171 sync

- Add Frequency: exactly eight tiles, URL, Search, Import QR code, Scan QR
  code, Local, YouTube, Twitch, iBroadcast (two full rows). The File and
  Paste tiles are gone; Settings' Restore still opens a file, and a
  shared link or code still opens in the app. Featured and Discover stay
  under the tiles.
- "Previously featured frequencies…" is a filled tonal button (history
  icon, at least 48 dp tall) under the Featured cards, no longer a text
  link beside the heading.
- Matches desktop build 171 (Batch CO, tasks 226 and 227; sync-protocol.md
  6.1, 6.4, 7):
  - New Age Radio and Dare-FM ship with the desktop's addresses (with the
    query string). `SyncData.joinOldAddresses` (after every merge, in
    `SyncPhone.takeState`) joins a pair whose addresses differ only by
    that query string as rule 6.1 does; the kept one gets the shipped
    address. Only those two default stations (`addressFixes`).
  - Every station that arrives without its picture gets `[-1, me]`
    ("filled in", never sent), not only while Station pictures are off;
    incoming stamps with t < 0 are ignored (`wireStampOf`). One-time
    repair `repairFilledIn` (`filled_fix` in sync-state.json).
  - A part turned on in "Choose what to sync…" while a session is open
    is merged at once from the computer's last state
    (`SyncSession.retake`).
- Tests: `Batch19Test` (6), `SyncDesktopTest` +1 against desktop 171's
  own sync_core (an old-address phone joins a fresh desktop's defaults).

## Batch 18 (AP18, build 172): Add screen tiles, previously featured, no stream address on cards

- Add Frequency (user, 2026-11-05): no search bar on top. The tiles are
  URL, Search, Import QR code, Scan QR code, then File, Paste, Local,
  YouTube, Twitch, iBroadcast (labels up to two lines, a short last row
  keeps the tile size). Search opens the radio-browser.info search as a
  page of the Add screen (field focused, the search tip there, results
  as before); Import QR code opens Android's photo picker and reads the
  code in the picture (`readPictureCode`, shared with the scanner's
  "Choose a picture"); Scan QR code opens the camera scanner. Back (the
  arrow or the system back) returns to the tiles.
- Previously featured frequencies (desktop build 151, task 211): a
  "Previously featured frequencies…" button beside Featured opens a page
  of every frequency featured before, newest first, with the days it was
  featured ("Sep 3 – Oct 18"). `core/FeaturedHistory` is the desktop's
  featuredhistory.py: the featured repository's commits API once a day
  (an hour's pause after a failure; offline skips it), each version of
  featured.json read once from raw.githubusercontent.com and folded into
  `files/data/featured/history.json` (the desktop's shape), plus every
  list this phone was shown (`noteSeen`). Tap a row for the curator's
  card (Play, Add); + adds it. The quiet line under the list says the
  desktop's words ("this phone" for "this computer").
- Featured cards never show where a station streams from (user,
  2026-11-05, all platforms; desktop task 228): with no blurb a card
  shows only the format and bitrate (`FeaturedList.streamInfo`).
  Discover rows, search results and the card sheet showed no address
  before and still don't.
- Discover: the "Name" list is gone (user, 2026-11-05); Most listened,
  Trending, Random, Most voted, New.
- Tests: `FeaturedHistoryTest` (3), `ServicesTest` updated.

## Batch 17 (AP17, build 170): untouched values and Frequensync

Matches desktop build 169 (Batch CN, tasks 224 and 225; sync-protocol.md
6.4, 7, 8, 9).

- Untouched values (`SyncData.settingIsDefault`, `stationFieldIsDefault`,
  `shippedStations`): the first stamp on a shared setting or a station
  field still at the phone's default is `[0, me]` ("never edited"), so a
  fresh device's defaults never beat real changes on the first sync. A
  default frequency (same address as one a fresh profile starts with) is
  compared with how the phone ships it, picture included; its `added_at`
  always counts as untouched. An edit is dated at least the old stamp + 1.
- One-time repair (`SyncData.repairFirstScan`, `untouched_fix` in
  sync-state.json): stamps from builds 164-168's first look on values
  still at the default and unchanged since become `[0, me]`.
- A twin removed by a tombstone and brought back under the other id in
  the same state keeps this phone's own keys (its picture file).
- Frequensync (`ui/Frequensync.kt`): what to sync with each paired
  computer: Saved Frequencies, Station pictures, Settings in four groups
  (Look, Playback, Lyrics and artists, Saved Frequencies list; the
  desktop's "Desktop only" group is never shown on a phone), Favorites,
  History. Shown after pairing (Not now / Sync) and on every Sync now
  (Cancel / Sync); Settings > Library and tools > Sync with another
  device > "Choose what to sync…" (Cancel / Save). Kept per computer in
  sync-peers.json (`what`, the desktop's form); until the first answer
  the computer's row says "Paired · choose what to sync to start
  syncing" and runs skip it. A part that is off is neither sent nor
  taken (`SyncData.What`, `buildState`/`merge` `what =`); stamps keep
  being made. A station that arrives while pictures are off has its
  "no picture" dated `[-1, me]` (never sent), so the picture comes when
  pictures are turned on (dated `[0, me]` the bigger device id would keep
  "no picture").
- Tests: `Batch17Test` (10), `SyncDesktopTest` +3 against desktop 169's
  own sync_core (fresh desktop vs set-up phone, fresh phone vs set-up
  desktop, parts off).

## Build 170 UI batch (from the user's screenshots)

Bottom bar Saved, Add (Browse merged in: tiles, then Featured/Discover), History, Schedule (red badge while live), Settings. Saved: no "All" chip, bigger tag pills, List/Thumbnails (`station_grid`, local). Mini player: drag up opens Now Playing; star, previous, play, next. Now Playing: centered tabs, no Comfort View button, song menu by the star (Apple Music, YouTube Music, Spotify, Tidal). SVG band logos (AndroidSVG 1.4). Stream certificates not checked for stations (`StationTls.EVERY_STATION`). Talk radio switch (`talk_radio`). Glyph animations 1.4x slower. System theme: separate dark and light pickers (`android_theme_dark`/`_light`, not synced). Share QR with the desktop frame. Details: /mnt/project-files/android/frequencies-android-170-ui-notes.md.

## Batch 16 (AP16, build 168): iBroadcast sign-in, tag pills, Jump to playing, ad hourglass, sync runs

Matches desktop build 167 (Batch CM, task 223; sync-protocol.md 2, 5.1,
6.1, 6.2, 8, 9).

- iBroadcast sign-in: `RedirectCatcher.await` reads every browser
  connection on its own thread, so a silent preconnect or a
  /favicon.ico request no longer ends the wait with an error (before,
  one silent spare connection failed the sign-in after 5 s). The sign-in
  runs in the app's scope under a short foreground service
  (`SignInService`, "Signing in to iBroadcast"), so the app keeps its
  network while the browser tab is in front. "Back to Frequencies" goes
  through `SignedInActivity`, which brings back the open app instead of
  starting a second one. `[ibroadcast] sign-in:` lines in logcat say
  what came back (never the code or tokens).
- Tag pills (`core/TagLook`, `ui/TagPills.kt`): the desktop's numbers
  for Saved rows (at most two, right side), the tag filter row and the
  sidebar (off / include / struck exclude), and the pickers in the Tags
  dialogs (picked = solid). Colors from `tag_colors`, unsaved tags take
  the next palette color as the desktop does.
- "Jump to playing": a pill at the list's bottom (or top) edge when the
  playing frequency is out of sight; it stacks with the live-show pills.
  Settings > Frequencies "Automatically scroll list position to
  currently playing frequency" (`jump_on_change`, shared with nothing,
  default on) follows the station on Next/Previous.
- Ad hourglass (`core/AdGlass`, `ui/AdHourglass.kt`): beside the star on
  the player bar while an opening ad is covered; sand by area, follows
  the player's ad clock (`playback/AdNow`), empty glass stays 1.5 s.
- Sync runs (`core/SyncRuns`): a run at launch, every
  `sync_interval_minutes` (default 30; 5/15/30/60/120/Off; this phone's
  own, never sent) while the app is on screen, after pairing and on
  Sync now; a run ends when every paired device sent its state and the
  pictures asked for came (25 s look, 90 s pictures, spinner at least
  0.8 s). Sync now button and "Sync automatically" in Settings > Library
  and tools; a turning sync icon on the player bar while a run goes (a
  problem shows it still, in red; tap opens the sync settings).
  `eq_advanced` is shared. A picture over 8 MiB goes as a ≤2048 px copy
  (`SyncData.shrink`, made with Android's decoder); each station gets
  its own picture file (`sync-<id>-<sha16>.<ext>`).
- Tests: `Batch16Test` (redirect catcher with a silent preconnect,
  palette and pill numbers against desktop storage.py, sand by area,
  run timing and wording against desktop sync.py/syncpage.py, big
  picture copy, one file per station) and `SyncDesktopTest` now also
  checks `eq_advanced` and per-station picture files against desktop 167.

## Batch 21 (AP21, build 176): iBroadcast says why, one bad track no longer stops the station, bigger band logo

- User, build 175: "The station's server said no." on an iBroadcast
  station (the user build on the Casual profile; the dev build played).
  The phone's log had rolled over, so the HTTP answer is unknown; a new
  sign-in made it play again. That message is the player's word for any
  HTTP error, so it never said which.
- A refused iBroadcast track: 401/403 (or no HTTP answer) renews the
  sign-in and tries the same track once more, as before; anything else
  (404, 5xx, a file iBroadcast can't send) and a second refusal go on to
  another track, up to `IBroadcast.TRACK_TRIES` (3) in a row before the
  station is marked down. Before, one bad track took the station down.
- `IBroadcast.refusal` / `said` / `renewalProblem`: plain words with
  iBroadcast's own reason (a JSON message / error_description / error,
  or a short plain-text line). `app/playback/ServiceProblems` carries it
  to `PlayerConnection`, which shows it in place of the player's words
  for any service station that gave up (YouTube and Twitch too); cleared
  when the station is tuned again or plays. The log line now reads
  `[ibroadcast] '<name>': track N refused (HTTP nnn: <reason>)`.
- A renewal that iBroadcast turns down for good (401, or 400
  invalid_grant: `Renewal.dead`) forgets the sign-in on the phone, so
  Settings and Add > iBroadcast offer Sign in again instead of keeping a
  dead one.
- Now Playing: the band logo under the song name gets a 48 dp box (was
  30), still as wide as the title column allows.
- Tests: `Batch21Test` (4).

## Batch 22 (AP22, build 179): sync freeze, sync sends on its own thread, no blank tiles, Song ID on Now Playing, Comfort follows Song ID

- Sync froze (user's Pixel bug report, ANR): a lock-order deadlock.
  `SyncPhone.takeState` held the sync store's lock and called `taken()`
  -> `SyncKeeper.savePeers()` (the keeper's lock), while
  `SyncKeeper.checkRun()` held the keeper's lock and asked
  `waitingArt` (the store's lock). Now `taken()` runs after the store's
  lock is let go and `checkRun` asks `waitingArt` before it takes the
  keeper's lock. Rule: never call out of `SyncPhone` while holding the
  store's lock, and never take the store's lock while holding the
  keeper's. `SyncLockTest`.
- `SyncSession` reads on the session thread and writes on its own
  `sync-write` thread (desktop build 178, sync-protocol.md section 3):
  everything sent goes through one queue in order, the writer pings when
  nothing went out for 30 s, and an `art?` is answered by the writer,
  reading the pictures batch by batch. `close()` hands the goodbye to the
  writer and closes the socket once it has gone (3 s at the most). A
  failed send closes the socket, which ends the session. Pictures go
  about 4 MB per message (`ART_BATCH`, as desktop 177). The read limit is
  still 90 s per message (`IDLE_LIMIT_MS`); Java sockets have no send
  timeout. `SyncWriterTest`: an old desktop (177) that sends its 4 MB
  state before reading, with 16 KB socket buffers on both ends, hangs the
  old session and passes with the writer.
- Blank tiles (Radio Caprice): `Library.artUri` no longer falls back to
  the station's favicon (the desktop never shows favicons:
  `bridge.logo_for`); `StationArt` draws the letter while a picture loads
  and when it can't be read, and a picture with see-through parts sits on
  the tinted square. A zero-byte picture file counts as none.
- Now Playing: Song ID (`SongIdButton`, the desktop's shazam / shazam_on
  glyphs, the ripple and the fill from the middle when a song is named)
  left of the song menu and the star, in the upright layout (Out and
  Comfort; shown with no song too, hidden on the Artist page like the
  star) and the sideways layout (Out row, and the Comfort grid's third
  slot). The wide layout has it in its bar, as before. The phone's mini
  player has no room for it (sync, star, hourglass, previous, play, next).
- Comfort View's automatic entry (user): only with Song ID on for the
  station (`Comfort.songIdOn`, `Comfort.autoPage`). With Song ID off it
  still comes in on its own on a talk station the dev marked (the shipped
  talk list or its talk radio switch) that has its own typed-in bio
  (`ArtistOverrides.talkWithBio`), and then on the Artist page. A tap
  always works. The desktop has no automatic Comfort View.
- Tests: `Batch22Test` (3), `SyncLockTest`, `SyncWriterTest`.

## Build 180: the desktop's Song ID mark

- User on 179: the Song ID button showed a ring with a "c" (off) and a
  dot (on). Those are the shapes the desktop only draws when its
  icons/shazam.png is missing; the desktop shows that file's mark, tinted
  (text2 off, the accent on). `SongIdGlyph` now draws the same picture
  (`res/drawable-nodpi/song_id_mark.png`, a copy of desktop
  icons/shazam.png), tinted the same way, with the gray mark under the
  accent one clipped to a circle growing from the middle while a song is
  named (desktop shazam_fill), then the settle, as before.
