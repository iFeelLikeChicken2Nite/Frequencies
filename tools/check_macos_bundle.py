#!/usr/bin/env python3
"""Reject macOS bundles containing incompatible native binaries."""

import os
import sys

from macholib.MachO import MachO
from macholib import mach_o

MAGIC = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
         b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
         b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
         b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
LOADS = {mach_o.LC_LOAD_DYLIB, mach_o.LC_LOAD_WEAK_DYLIB,
         mach_o.LC_REEXPORT_DYLIB, mach_o.LC_LOAD_UPWARD_DYLIB,
         mach_o.LC_LAZY_LOAD_DYLIB}
MINIMUM = (11, 0, 0)


def version_tuple(value):
    return value >> 16, (value >> 8) & 0xff, value & 0xff


def inspect_image(path):
    problems = []
    for header in MachO(path).headers:
        version = None
        for load, command, data in header.commands:
            if load.cmd == mach_o.LC_BUILD_VERSION and command.platform == 1:
                version = version_tuple(command.minos)
            elif load.cmd == mach_o.LC_VERSION_MIN_MACOSX:
                version = version_tuple(command.version)
            elif load.cmd in LOADS:
                dependency = data.split(b"\0", 1)[0].decode("utf-8", "replace")
                normalized = os.path.normpath(dependency)
                if dependency.startswith("/") and not normalized.startswith(
                        ("/usr/lib/", "/System/Library/")):
                    problems.append("non-system dependency: " + dependency)
        if version is None:
            problems.append("missing macOS deployment target")
        elif version > MINIMUM:
            problems.append("requires macOS " + ".".join(map(str, version)))
    return problems


def main(argv):
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print("usage: check_macos_bundle.py <application bundle>", file=sys.stderr)
        return 2
    root = os.path.abspath(argv[1])
    errors = []
    count = 0
    for folder, _directories, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            if os.path.islink(path):
                continue
            try:
                with open(path, "rb") as handle:
                    if handle.read(4) not in MAGIC:
                        continue
                count += 1
                for problem in inspect_image(path):
                    errors.append(f"{os.path.relpath(path, root)}: {problem}")
            except (OSError, ValueError) as error:
                errors.append(f"{os.path.relpath(path, root)}: {error}")
    if not count:
        print("No Mach-O binaries found in the app bundle.", file=sys.stderr)
        return 1
    if errors:
        for error in errors[:20]:
            print("  " + error, file=sys.stderr)
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more issues", file=sys.stderr)
        print("Bundle is not suitable for macOS 11; use a compatible Python and dependencies.",
              file=sys.stderr)
        return 1
    print(f"    {count} Mach-O binaries declare macOS 11 or earlier; no build-host dylib paths")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))