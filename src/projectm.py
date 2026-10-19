"""projectM (Milkdrop-compatible) visualizer support.

projectM 4 ships a real C API - projectm_create, projectm_pcm_add_float,
projectm_opengl_render_frame - in extern "C" headers, so ctypes can drive
it directly without compiling a shim. Older packaged versions (the 2.x in
most distro repos) are C++ only and cannot be used this way; this module
detects that and says so rather than half-working.

Deliberately off by default. Rendering OpenGL inside a frameless child
window is the same class of thing that has caused the worst bugs in this
skin, so it stays behind a setting until it has proven itself.
"""

import ctypes
import ctypes.util
import logging
import os

log = logging.getLogger("frequencies.projectm")

# Where the C API lives, newest first. The soname changed at 4.0.
CANDIDATE_LIBRARIES = (
    "libprojectM-4.so.4",
    "libprojectM-4.so",
    "libprojectM.so.4",
)

def _bundled_presets():
    """The pack that ships beside the code.

    Checked before the system ones: inside an AppImage there is no
    /usr/share/projectM to find, and the pack the build downloaded sits
    next to this file.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return (
        os.path.join(here, "presets"),
        os.path.join(here, "..", "presets"),
        os.path.join(here, "..", "share", "frequencies", "presets"),
    )


# Where the presets might be, nearest first.
PRESET_DIRS = _bundled_presets() + (
    os.path.join(os.path.expanduser("~"), ".config", "frequencies",
                 "presets"),
    os.path.join(os.path.expanduser("~"), ".local", "share", "projectM",
                 "presets"),
    "/usr/share/projectM/presets",
    "/usr/local/share/projectM/presets",
    "/usr/share/projectm/presets",
)


class ProjectMUnavailable(RuntimeError):
    """Raised when the library is missing or too old to drive."""


def find_library():
    """The projectM shared library, or None.

    Every attempt is logged. When this fails inside an AppImage the
    reason is almost always a missing dependency of libprojectM itself,
    and the OSError says which - information worth keeping rather than
    swallowing.
    """
    log.info("looking for projectM; LD_LIBRARY_PATH=%s",
             os.environ.get("LD_LIBRARY_PATH", "(unset)"))
    for name in CANDIDATE_LIBRARIES:
        try:
            library = ctypes.CDLL(name)
            log.info("loaded %s", name)
            return library
        except OSError as error:
            log.info("could not load %s: %s", name, error)
    found = ctypes.util.find_library("projectM-4")
    if found:
        try:
            library = ctypes.CDLL(found)
            log.info("loaded %s via find_library", found)
            return library
        except OSError as error:
            log.info("could not load %s: %s", found, error)
    log.warning("projectM was not found")
    return None


def find_presets():
    """A directory with .milk presets in it, or None.

    A directory that exists but is empty is not an answer — the AppImage
    creates the folder whether or not the pack was fetched, and returning
    it meant "presets found, zero presets".
    """
    for path in PRESET_DIRS:
        if not os.path.isdir(path):
            continue
        for _root, _dirs, names in os.walk(path):
            if any(n.lower().endswith(".milk") for n in names):
                log.info("presets from %s", path)
                return path
    log.info("no preset directory had any .milk files")
    return None


def list_presets(path=None):
    """[(display name, full path)] sorted by name.

    The display name drops the extension and the author prefix stays,
    because "Geiss - Journey's End" says more than "Journey's End".
    """
    path = path or find_presets()
    if not path or not os.path.isdir(path):
        return []
    found = []
    for root, _dirs, names in os.walk(path):
        for name in names:
            if name.lower().endswith(".milk"):
                found.append((os.path.splitext(name)[0],
                              os.path.join(root, name)))
    return sorted(found, key=lambda item: item[0].lower())


def availability():
    """(usable, message) - what to show in Options.

    Checked by looking for the C entry points rather than by version
    number: what matters is whether the functions can actually be
    called, and a build without them is useless here however it is
    labeled.
    """
    library = find_library()
    if library is None:
        return False, (
            "projectM isn't installed. On Fedora: sudo dnf install "
            "projectM-libs projectM-presets (version 4 or newer)."
        )

    missing = [
        name for name in ("projectm_create", "projectm_pcm_add_float",
                          "projectm_opengl_render_frame")
        if not hasattr(library, name)
    ]
    if missing:
        return False, (
            "The installed projectM is too old - it has no C API, so it "
            "can't be driven from Python. Version 4 or newer is needed."
        )

    presets = find_presets()
    if presets is None:
        return True, (
            "projectM is available, but no preset collection was found. "
            "Install projectM-presets for something to look at."
        )
    return True, f"projectM is available, with presets in {presets}."


class ProjectM:
    """A projectM instance, driven through its C API.

    Kept deliberately thin: create, feed it audio, render a frame, tear
    it down. Everything about where the frame goes belongs to whatever
    owns the GL context.
    """

    def __init__(self, width, height):
        library = find_library()
        if library is None:
            raise ProjectMUnavailable("projectM is not installed")
        self._lib = library
        self._fbo_capable = None

        library.projectm_create.restype = ctypes.c_void_p
        self._handle = library.projectm_create()
        log.info("projectm_create returned %r", self._handle)
        if not self._handle:
            raise ProjectMUnavailable(
                "projectm_create() returned NULL - projectM rejected the "
                "OpenGL context it was given"
            )

        self.resize(width, height)

        presets = find_presets()
        if presets:
            self.load_preset_dir(presets)

    def resize(self, width, height):
        """Tell projectM the drawing surface changed size.

        Logged rather than swallowed. This used to catch AttributeError
        and say nothing, so if the symbol were missing the visuals would
        simply stay the size they started at with no hint why.
        """
        width, height = max(1, int(width)), max(1, int(height))
        try:
            self._lib.projectm_set_window_size(
                ctypes.c_void_p(self._handle),
                ctypes.c_size_t(width), ctypes.c_size_t(height),
            )
        except AttributeError:
            log.warning("projectm_set_window_size missing; "
                        "the visuals will not follow the window")
        except Exception as error:  # noqa: BLE001
            log.warning("resize to %dx%d failed: %s", width, height, error)

    def load_preset_dir(self, path):
        """Load a random preset from a directory."""
        import random

        presets = list_presets(path)
        if not presets:
            return False
        return self.load_preset(random.choice(presets)[1])

    def load_preset(self, path, smooth=True):
        """Switch to one named preset.

        projectM 4 dropped the built-in playlist, so choosing and
        rotating presets is the caller's job - which is why the window
        keeps its own list.
        """
        self._lib.projectm_load_preset_file(
            ctypes.c_void_p(self._handle),
            str(path).encode("utf-8"),
            ctypes.c_bool(bool(smooth)),
        )
        return True

    def add_audio(self, samples):
        """Feed PCM in.

        `samples` is a flat interleaved stereo sequence of floats in
        -1..1. The count passed to projectM is FRAMES, not values, so it
        is half the length - getting that wrong makes it read past the
        buffer.

        projectM keeps a fixed-size internal window, so there is no
        point handing it more than it can hold; the newest samples are
        the ones that matter.
        """
        frames = len(samples) // 2
        if frames <= 0:
            return

        try:
            self._lib.projectm_pcm_get_max_samples.restype = ctypes.c_uint
            limit = int(self._lib.projectm_pcm_get_max_samples())
        except Exception:  # noqa: BLE001
            limit = 0
        if limit and frames > limit:
            samples = samples[-limit * 2:]
            frames = limit

        buffer = (ctypes.c_float * len(samples))(*samples)
        self._lib.projectm_pcm_add_float(
            ctypes.c_void_p(self._handle), buffer,
            ctypes.c_uint(frames), ctypes.c_int(2),   # PROJECTM_STEREO
        )

    def render(self, framebuffer=0):
        """Draw one frame.

        `framebuffer` matters more than it looks. projectM's plain
        render call draws to framebuffer 0 - the window's default - but
        a QOpenGLWidget does not use framebuffer 0. It renders into an
        FBO of its own and composites that into the window, so a frame
        drawn to 0 goes somewhere the widget never shows. That is a
        visualizer that reports rendering happily while displaying
        nothing at all.

        projectm_opengl_render_frame_fbo takes the target explicitly,
        but it only arrived in projectM 4.2, so this falls back to the
        old call when the symbol is missing.
        """
        if framebuffer and self._can_render_to_fbo():
            self._lib.projectm_opengl_render_frame_fbo(
                ctypes.c_void_p(self._handle), ctypes.c_uint32(framebuffer)
            )
            return
        self._lib.projectm_opengl_render_frame(ctypes.c_void_p(self._handle))

    def _can_render_to_fbo(self):
        if self._fbo_capable is None:
            self._fbo_capable = hasattr(
                self._lib, "projectm_opengl_render_frame_fbo"
            )
            log.info("projectm_opengl_render_frame_fbo available: %s",
                     self._fbo_capable)
        return self._fbo_capable

    def close(self):
        if getattr(self, "_handle", None):
            self._lib.projectm_destroy(ctypes.c_void_p(self._handle))
            self._handle = None
