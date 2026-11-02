#!/usr/bin/env python3
"""Stop a build that would ship a token (build 151, tasks 209/212).

Run by both build scripts on what is about to be shipped (the AppImage's
AppDir, the Windows dist folder):

    python3 tools/scan_tokens.py AppDir
    <secret files as JSON on stdin> | python tools\\scan_tokens.py dist\\Frequencies --secrets-stdin

It looks through every file, binaries included, for:
  - GitHub tokens by their shape: ghp_ / gho_ / ghu_ / ghs_ / ghr_ and
    github_pat_ (classic and fine-grained);
  - the exact secrets your dev build keeps: the photo-publish token
    (github_token.json), the release token (release_token.json), an old
    report token (report_token.json), your Twitch chat sign-in
    (twitch_chat.json) and a Twitch client secret if one was ever saved
    (twitch_app.json). Read from FREQUENCIES_DEV_CONFIG, else
    ~/.config/frequencies; on Windows the build hands them over on stdin
    (Python from the Microsoft Store can't see the real AppData);
  - those files themselves, by name.

Never prints a token: only the file it was found in (and a GitHub
token's public prefix, like ghp_). Exits 1 when it finds one, so the build stops.
"""

import json
import mmap
import os
import re
import sys

SECRET_FILES = ("github_token.json", "release_token.json",
                "report_token.json", "twitch_chat.json")
SECRET_KEYS = ("token", "access_token", "refresh_token", "client_secret")
SHAPES = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,})")


def config_dir():
    own = os.environ.get("FREQUENCIES_DEV_CONFIG")
    if own:
        return own
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", ""), "frequencies")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "frequencies")


def _values(data):
    """Secret-looking strings in one secret file's JSON."""
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in SECRET_KEYS and isinstance(value, str) \
                    and len(value.strip()) >= 12:
                found.append(value.strip())
            elif isinstance(value, (dict, list)):
                found += _values(value)
    elif isinstance(data, list):
        for item in data:
            found += _values(item)
    return found


def known_secrets(from_stdin=False):
    """The dev build's own secrets, as bytes, longest first."""
    docs = []
    if from_stdin:
        text = sys.stdin.read().strip()
        if text:
            try:
                docs.append(json.loads(text))
            except ValueError:
                print("!! scan_tokens: the secrets handed over weren't JSON; "
                      "checking by shape only")
    else:
        folder = config_dir()
        for name in SECRET_FILES + ("twitch_app.json",):
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    docs.append(json.load(f))
            except (OSError, ValueError):
                continue
    values = {v.encode("utf-8") for doc in docs for v in _values(doc)}
    return sorted(values, key=len, reverse=True)


def scan(root, secrets):
    """[(path, what)] for every token found under root."""
    problems = []
    for folder, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            if name.lower() in SECRET_FILES:
                problems.append((path, "a secret file"))
                continue
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            try:
                size = os.path.getsize(path)
                if size == 0:
                    continue
                with open(path, "rb") as f, \
                        mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
                    hit = SHAPES.search(m)
                    if hit:
                        problems.append(
                            (path, "a GitHub token (" +
                             hit.group(0)[:4].decode() + "…)"))
                        continue
                    for secret in secrets:
                        if m.find(secret) != -1:
                            problems.append(
                                (path, "one of your dev build's tokens"))
                            break
            except (OSError, ValueError):
                continue
    return problems


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = argv[1]
    secrets = known_secrets("--secrets-stdin" in argv)
    problems = scan(root, secrets)
    if problems:
        print("!! A token was found in what this build would ship. The build "
              "stops here; nothing was made to share.")
        for path, what in problems:
            print(f"!!   {os.path.relpath(path, root)}: {what}")
        print("!! Remove it from the source folder (src/) and build again.")
        return 1
    print(f"    no tokens in {root} (checked by shape"
          + (f" and against {len(secrets)} of your dev build's secrets"
             if secrets else "") + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
