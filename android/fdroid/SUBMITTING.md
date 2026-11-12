# Getting Frequencies onto F-Droid

Wait until you call the app finished. Nothing here has been sent anywhere yet.
Every step below is yours to press; Claude can walk you through each one.

## What's already done (build 154)

- The app builds from the public source alone, with no token or secret
  (`gradlew assembleFdroidRelease`, checked from a clean copy).
- The store page text: `fastlane/metadata/android/en-US/` (title, short and full
  description, a changelog per build). F-Droid reads it from the TOP of the
  public repository, next to the desktop files.
- The entry for F-Droid's list: `android/fdroid/com.frequencies.app.yml`. It
  passes F-Droid's own checker (`fdroid lint`) and source scanner.
- F-Droid will show one warning label: **NonFreeNet** ("depends on a non-free
  network service"), because Song ID uses Shazam and some pictures and bios
  come from Apple, Deezer, Discogs and TuneIn. Everything else works without
  them. No ads, no tracking.

## Signing: let F-Droid sign it (recommended)

- F-Droid's own key: nothing to set up. Anyone who installed a copy you signed
  (like your phone's "Casual" profile) uninstalls it once and installs from F-Droid.
- Your key with "reproducible builds": updates from GitHub and F-Droid mix
  seamlessly, but every release needs a signed APK uploaded to GitHub too.
  Since you chose F-Droid only, F-Droid's key is the easy choice.

## Each time you release a new Android build

1. Upload the new source to GitHub (the upload zip, as usual).
2. On the repository page: **Releases** > **Draft a new release** > **Choose a
   tag** > type `android-1.5.<build>` (for example `android-1.5.160`) > **Create
   new tag** > **Publish release**. F-Droid finds the new tag by itself, builds
   it and publishes it, usually within a few days.

## Submitting (once, when the app is finished)

1. Do "Each time you release" above for the finished build first.
2. Make a free account at https://gitlab.com/users/sign_up (F-Droid lives on GitLab, not GitHub).
3. Open https://gitlab.com/fdroid/fdroiddata and press **Fork** (top right),
   then **Fork project**. This makes your own copy of F-Droid's app list.
4. In your copy, open the **metadata** folder, press **+** > **New file**.
   File name: `com.frequencies.app.yml`. Paste everything from
   `android/fdroid/com.frequencies.app.yml`, and change the four version
   numbers (`versionName`, `versionCode`, `commit`, `CurrentVersion`,
   `CurrentVersionCode`) to the finished build (Claude can do this for you).
5. Below the editor: Commit message `New app: Frequencies`, Target branch
   `com.frequencies.app` (a new branch), tick **Start a new merge request**,
   press **Commit changes**.
6. On the merge request page: Title `New app: Frequencies`, pick the
   **App inclusion** template in the Description box, tick the boxes that
   are true (Claude will go through them with you), press **Create merge request**.
7. F-Droid's robot builds the app right away (the "pipeline"). If it shows a
   red X, copy the log link to Claude.
8. A volunteer reviews it. They may ask questions in comments; paste them to
   Claude. Review can take a few weeks. When they merge it, the app appears in
   the F-Droid app a few days later.

## Notes for whoever helps

- Android Auto works from F-Droid too, with Android Auto's developer setting
  "Unknown sources" on (the store text says so).
- If a reviewer objects to zxing-cpp's prebuilt native library (from Maven
  Central), the fdroid flavor can read QR codes with zxing's Java reader,
  which the app already carries for making codes.
- `android/tools/songid-harness` (a Rust tool used only to make Song ID test data)
  is removed before F-Droid's build (`rm:`): its scanner flags a Cargo.toml
  without a lock file. With that, the scanner finds no problems in the whole
  public tree (desktop files included).
- Fonts (build 157): Noto Sans Regular, Medium and SemiBold are bundled in
  `android/app/src/main/res/font`, from the official Noto release, under the
  SIL Open Font License 1.1 (`android/app/src/main/assets/licenses/noto-sans-OFL.txt`,
  also shown in the app). The OFL is a free license F-Droid accepts; the app's
  own license stays GPL-3.0-or-later.
