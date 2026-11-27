"""The RPM x MAP scene: one class, three modes, two hosts (HANDOFF section 6).

``MapModel`` is the numpy side: heights, colours, the live-point lookup. It
is shared by the OpenGL view and by the 2D heat map that is used when OpenGL
2.1 (compatibility profile) is not available, detected at runtime.

Scene axes follow the handoff, turned for pyqtgraph's z-up camera:
x = RPM (-1..+1), y = MAP (+0.7 at the lowest kPa to -0.7), z = value (0..0.9).
"""

from __future__ import annotations

import math
import os
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QSurfaceFormat
from PySide6.QtWidgets import QWidget

from . import paint, theme
from .theme import T

HEIGHT = 0.9
LOG901 = math.log(901.0)
PRESETS = {"iso": (-0.62, 0.50), "top": (0.0, 1.5), "rpm": (0.0, 0.1), "map": (-math.pi / 2, 0.1)}
MAX_DRAW_CALLS = 40
MAX_TRIANGLES = 6000
PATH_TOKENS = ("series1", "series2", "series3")


# ---- model ------------------------------------------------------------------

class MapModel:
    def __init__(self, rpm_axis, map_axis):
        self.rpm = np.asarray(rpm_axis, dtype=np.float64)
        self.map = np.asarray(map_axis, dtype=np.float64)
        self.nx, self.ny = len(self.rpm), len(self.map)
        self.mode = "surface"           # surface / hist / stall
        self.color_by = "z"             # z / trim / hits
        self.logged = True              # cells without samples go grey
        self.z = np.zeros((self.ny, self.nx))
        self.z_range = (0.0, 1.0)
        self.z_label = ""
        self.hits = np.zeros((self.ny, self.nx), dtype=np.int32)
        self.trim = np.zeros((self.ny, self.nx))
        self.paths: list = []           # (rpm array, kPa array), oldest sample first
        self.sel: tuple | None = None   # (i_rpm, j_map)
        self.xs = np.linspace(-1.0, 1.0, self.nx)
        self.ys = np.linspace(0.7, -0.7, self.ny)
        self._h = None
        self.rev = 0

    def touch(self) -> None:
        self._h = None
        self.rev += 1

    def x_of(self, rpm):
        return -1.0 + 2.0 * (np.asarray(rpm, dtype=float) - self.rpm[0]) / (self.rpm[-1] - self.rpm[0])

    def y_of(self, kpa):
        return 0.7 - 1.4 * (np.asarray(kpa, dtype=float) - self.map[0]) / (self.map[-1] - self.map[0])

    def heights(self) -> np.ndarray:
        """(ny, nx) in 0..1. Cells without data take their neighbours' height
        so the surface stays in one piece; their colour says they are guesses."""
        if self._h is not None:
            return self._h
        if self.mode == "hist":
            h = np.log1p(self.hits) / LOG901
        else:
            lo, hi = self.z_range
            h = (self.z - lo) / ((hi - lo) or 1.0)
            known = np.isfinite(h)
            if self.logged:
                known &= self.hits > 0
            h = fill_unknown(np.where(known, h, np.nan))
        self._h = np.clip(h, 0.0, 1.0)
        return self._h

    def colors(self, tokens=None) -> np.ndarray:
        """(ny, nx, 3) floats, straight from the ramps so they match the legend."""
        tokens = tokens or T.tokens
        grey = np.array(theme.rgb_of(tokens.nodata), dtype=np.float32)
        if self.mode == "stall":
            return np.broadcast_to(grey, (self.ny, self.nx, 3)).copy()
        if self.mode == "hist" or self.color_by == "hits":
            col = theme.seq_ramp(np.log1p(self.hits) / LOG901)
        elif self.color_by == "trim":
            col = theme.div_ramp((np.nan_to_num(self.trim) + 12.0) / 24.0)
        else:
            col = theme.seq_ramp(self.heights())
        if self.logged or self.mode == "hist":
            col = np.where((self.hits > 0)[..., None], col, grey)
        return col.astype(np.float32)

    def locate(self, rpm: float, kpa: float):
        """Bilinear position on the surface: (x, y, z, i, j, value)."""
        fx = float(np.interp(rpm, self.rpm, np.arange(self.nx)))
        fy = float(np.interp(kpa, self.map, np.arange(self.ny)))
        x, y, h, value = self.at_index(fx, fy)
        ni = int(round(min(max(fx, 0.0), self.nx - 1.001)))
        nj = int(round(min(max(fy, 0.0), self.ny - 1.001)))
        return x, y, h, ni, nj, value

    def at_index(self, fx: float, fy: float):
        """The surface at a fractional cell index (column = RPM, row = MAP),
        as ``tuning.TableTracer`` gives it: (x, y, z, value), bilinear."""
        fx = float(np.clip(fx, 0, self.nx - 1.001))
        fy = float(np.clip(fy, 0, self.ny - 1.001))
        i, j = int(fx), int(fy)
        a, b = fx - i, fy - j

        def lerp(grid):
            return ((grid[j, i] * (1 - a) + grid[j, i + 1] * a) * (1 - b)
                    + (grid[j + 1, i] * (1 - a) + grid[j + 1, i + 1] * a) * b)
        h = float(lerp(self.heights())) * HEIGHT
        x = -1.0 + 2.0 * fx / (self.nx - 1)
        y = 0.7 - 1.4 * fy / (self.ny - 1)
        ni, nj = int(round(fx)), int(round(fy))
        value = float(self.hits[nj, ni]) if self.mode == "hist" else float(
            lerp(np.nan_to_num(self.z)))
        return x, y, h, value

    def z_ticks(self):
        """Five (height 0..0.9, label) pairs for the Z axis."""
        out = []
        for k in range(5):
            f = k / 4.0
            if self.mode == "hist":
                text = str(int(round(math.exp(f * LOG901) - 1)))
            else:
                lo, hi = self.z_range
                text = f"{lo + (hi - lo) * f:.4g}"
            out.append((f * HEIGHT, text))
        return out

    def z_title(self) -> str:
        return "SAMPLES" if self.mode == "hist" else self.z_label


def fill_unknown(h: np.ndarray) -> np.ndarray:
    """Replace NaN cells by the mean of known neighbours, spreading outward."""
    h = h.copy()
    unknown = np.isnan(h)
    if not unknown.any():
        return h
    if np.isnan(h).all():
        return np.zeros_like(h)
    for _ in range(h.shape[0] + h.shape[1]):
        nan = np.isnan(h)
        if not nan.any():
            break
        pad = np.pad(h, 1, constant_values=np.nan)
        stack = np.stack([pad[:-2, 1:-1], pad[2:, 1:-1], pad[1:-1, :-2], pad[1:-1, 2:]])
        count = np.isfinite(stack).sum(axis=0)
        mean = np.nansum(stack, axis=0) / np.maximum(count, 1)
        fill = nan & (count > 0)
        h[fill] = mean[fill]
    h = np.nan_to_num(h)
    for _ in range(40):                         # relax the guesses into a smooth sheet
        pad = np.pad(h, 1, mode="edge")
        mean = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:]) / 4.0
        h[unknown] = mean[unknown]
    return h


def box_mesh(model: MapModel, colors: np.ndarray):
    """Time-in-cell bars merged into one mesh: (vertices, vertex colours).

    Flat Lambert shading is baked into the colours (ambient 0.72 plus one
    directional 0.45), so the GL side needs no lights and no normals.
    """
    hits = model.hits
    jj, ii = np.nonzero(hits)
    n = len(ii)
    if n == 0:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 4), dtype=np.float32)
    hw, hd = 0.082 / 2, 0.12 / 2
    height = np.maximum(0.012, model.heights()[jj, ii] * HEIGHT)
    corners = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0],
                        [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], dtype=np.float32)
    faces = np.array([[4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                      [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7], [0, 2, 1], [0, 3, 2]])
    normals = np.array([[0, 0, 1], [0, 0, 1], [0, -1, 0], [0, -1, 0], [1, 0, 0], [1, 0, 0],
                        [0, 1, 0], [0, 1, 0], [-1, 0, 0], [-1, 0, 0], [0, 0, -1], [0, 0, -1]],
                       dtype=np.float32)
    light = np.array([-1.5, -2.0, 3.0], dtype=np.float32)
    light /= np.linalg.norm(light)
    shade = 0.72 + 0.45 * np.maximum(0.0, normals @ light)              # (12,)
    scale = np.stack([np.full(n, hw), np.full(n, hd), height], axis=1)   # (n, 3)
    origin = np.stack([model.xs[ii], model.ys[jj], np.zeros(n)], axis=1)
    verts = corners[None, :, :] * scale[:, None, :] + origin[:, None, :]  # (n, 8, 3)
    tri = verts[:, faces, :]                                             # (n, 12, 3, 3)
    col = np.clip(colors[jj, ii][:, None, :] * shade[None, :, None], 0, 1)  # (n, 12, 3)
    vcol = np.ones((n, 12, 3, 4), dtype=np.float32)
    vcol[..., :3] = col[:, :, None, :]
    return tri.reshape(-1, 3).astype(np.float32), vcol.reshape(-1, 4)


def octahedron(center, radius: float) -> np.ndarray:
    """(24, 3) vertices of 8 triangles."""
    cx, cy, cz = center
    p = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]],
                 dtype=np.float32) * radius + np.array([cx, cy, cz], dtype=np.float32)
    idx = [[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]]
    return p[np.array(idx)].reshape(-1, 3)


def sphere_mesh(radius: float, rows: int = 12, cols: int = 16) -> np.ndarray:
    """Loose triangles of a UV sphere, wound counter-clockwise seen from outside."""
    lat = np.linspace(-np.pi / 2, np.pi / 2, rows + 1)
    lon = np.linspace(0, 2 * np.pi, cols + 1)
    pts = np.stack([np.cos(lat)[:, None] * np.cos(lon)[None, :],
                    np.cos(lat)[:, None] * np.sin(lon)[None, :],
                    np.sin(lat)[:, None] * np.ones_like(lon)[None, :]], axis=-1) * radius
    a, b = pts[:-1, :-1], pts[:-1, 1:]
    c, d = pts[1:, :-1], pts[1:, 1:]
    return np.stack([a, b, d, a, d, c], axis=2).reshape(-1, 3).astype(np.float32)


def path_points(model: MapModel, rpm, kpa, n: int = 40):
    """Resample one stall approach to n beads on the surface: (pos, radius)."""
    rpm, kpa = np.asarray(rpm, dtype=float), np.asarray(kpa, dtype=float)
    ok = np.isfinite(rpm) & np.isfinite(kpa)
    rpm, kpa = rpm[ok], kpa[ok]
    if len(rpm) < 2:
        return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.float32)
    s = np.linspace(0, len(rpm) - 1, n)
    k = np.arange(len(rpm))
    pos = np.empty((n, 3), dtype=np.float32)
    for q, (r, m) in enumerate(zip(np.interp(s, k, rpm), np.interp(s, k, kpa))):
        x, y, h, *_ = model.locate(max(r, model.rpm[0]), min(m, model.map[-1]))
        pos[q] = (x, y, h + 0.025)
    radius = (0.011 + 0.014 * np.linspace(0, 1, n)).astype(np.float32)
    return pos, radius


# ---- camera -----------------------------------------------------------------

class Camera:
    """Orbit camera with the handoff's numbers. az / el in radians."""

    FOV_V = 30.0
    TARGET = (0.0, 0.0, 0.3)

    def __init__(self, dist: float = 4.6, az: float = -0.62, el: float = 0.50):
        self.dist, self.az, self.el = dist, az, el
        self.home = (dist, az, el)

    def orbit(self, daz: float, d_el: float) -> None:
        self.az += daz
        self.el = min(1.5, max(0.08, self.el + d_el))

    def zoom(self, factor: float) -> None:
        self.dist = min(8.0, max(3.0, self.dist * factor))

    def preset(self, name: str) -> None:
        self.az, self.el = PRESETS[name]

    def eye(self):
        ce = math.cos(self.el)
        return (self.TARGET[0] + self.dist * ce * math.sin(self.az),
                self.TARGET[1] - self.dist * ce * math.cos(self.az),
                self.TARGET[2] + self.dist * math.sin(self.el))

    def pg_angles(self):
        """(elevation, azimuth) in degrees for GLViewWidget."""
        return math.degrees(self.el), math.degrees(self.az) - 90.0

    def fov_h(self, w: float, h: float) -> float:
        """GLViewWidget's fov is horizontal; the handoff's 30 degrees is vertical."""
        return math.degrees(2 * math.atan(math.tan(math.radians(self.FOV_V / 2)) * w / max(1.0, h)))

    def matrix(self, w: float, h: float) -> np.ndarray:
        """World -> clip, for placing the 2D labels over the view."""
        eye = np.array(self.eye())
        f = np.array(self.TARGET) - eye
        f /= np.linalg.norm(f)
        s = np.cross(f, (0.0, 0.0, 1.0))
        s /= np.linalg.norm(s)
        u = np.cross(s, f)
        view = np.eye(4)
        view[0, :3], view[1, :3], view[2, :3] = s, u, -f
        view[:3, 3] = -view[:3, :3] @ eye
        t = 1.0 / math.tan(math.radians(self.FOV_V / 2))
        near, far = self.dist * 0.001, self.dist * 1000.0
        proj = np.zeros((4, 4))
        proj[0, 0], proj[1, 1] = t / (w / max(1.0, h)), t
        proj[2, 2], proj[2, 3] = (far + near) / (near - far), 2 * far * near / (near - far)
        proj[3, 2] = -1.0
        return proj @ view

    def project(self, points, w: float, h: float) -> np.ndarray:
        """(n, 3) world points -> (n, 3): pixel x, pixel y, depth (w)."""
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        clip = np.c_[pts, np.ones(len(pts))] @ self.matrix(w, h).T
        wc = np.where(np.abs(clip[:, 3]) < 1e-9, 1e-9, clip[:, 3])
        return np.c_[(clip[:, 0] / wc + 1) * 0.5 * w, (1 - clip[:, 1] / wc) * 0.5 * h, wc]


def axis_labels(model: MapModel):
    """(world position, text, is_title) for every label in the scene."""
    out = []
    r0, r1 = model.rpm[0], model.rpm[-1]
    step = 1000 if r1 - r0 > 3500 else 500
    r = math.ceil(r0 / step) * step
    while r <= r1 - 1:
        if r > r0:
            out.append(((float(model.x_of(r)), -0.84, -0.03), f"{r:g}", False))
        r += step
    out.append(((0.0, -1.04, -0.03), "RPM", True))
    for m in (20, 40, 60, 80, 100):
        if model.map[0] <= m <= model.map[-1]:
            out.append(((1.15, float(model.y_of(m)), -0.02), str(m), False))
    out.append(((1.44, 0.0, -0.02), "MAP kPa", True))
    for z, text in model.z_ticks():
        out.append(((-1.16, 0.7, z), text, False))
    out.append(((-1.1, 0.7, 1.04), model.z_title(), True))
    return out


def frame_lines(model: MapModel) -> np.ndarray:
    seg = []
    for x in model.xs:
        seg += [(x, 0.7, 0), (x, -0.7, 0)]
    for y in model.ys:
        seg += [(-1, y, 0), (1, y, 0)]
    for k in range(1, 5):
        z = k * 0.225
        seg += [(-1, 0.7, z), (1, 0.7, z), (-1, 0.7, z), (-1, -0.7, z)]
    seg += [(-1, 0.7, 0), (-1, 0.7, HEIGHT), (1, 0.7, 0), (1, 0.7, HEIGHT),
            (-1, -0.7, 0), (-1, -0.7, HEIGHT)]
    return np.array(seg, dtype=np.float32)


# ---- OpenGL detection -------------------------------------------------------

_gl_state: tuple | None = None


def gl_status() -> tuple[bool, str]:
    """(usable, reason). Checked once: can we get a 2.1+ compatibility context?"""
    global _gl_state
    if _gl_state is not None:
        return _gl_state
    forced = os.environ.get("HONDALOG_GL", "")
    if forced == "0":
        _gl_state = (False, "disabled by HONDALOG_GL=0")
        return _gl_state
    try:
        import OpenGL.GL  # noqa: F401
        import pyqtgraph.opengl  # noqa: F401
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext
    except Exception as exc:                # PyOpenGL or QtOpenGLWidgets missing
        _gl_state = (False, f"OpenGL modules missing ({exc.__class__.__name__}: {exc})")
        return _gl_state
    try:
        fmt = QSurfaceFormat.defaultFormat()
        ctx = QOpenGLContext()
        ctx.setFormat(fmt)
        if not ctx.create():
            raise RuntimeError("no OpenGL context on this display")
        surface = QOffscreenSurface()
        surface.setFormat(ctx.format())
        surface.create()
        if not ctx.makeCurrent(surface):
            raise RuntimeError("cannot make an OpenGL context current")
        got = ctx.format()
        version = (got.majorVersion(), got.minorVersion())
        core = got.profile() == QSurfaceFormat.CoreProfile
        ctx.doneCurrent()
        if ctx.isOpenGLES():
            raise RuntimeError("OpenGL ES only")
        if version < (2, 1) or core:
            raise RuntimeError(f"need OpenGL 2.1+ compatibility profile, got "
                               f"{version[0]}.{version[1]}{' core' if core else ''}")
        _gl_state = (True, f"OpenGL {version[0]}.{version[1]}")
    except Exception as exc:
        _gl_state = (False, str(exc))
    return _gl_state


def default_surface_format(samples: int = 4) -> None:
    """Call before QApplication: 2.1 compatibility, depth buffer, MSAA.

    Contexts are shared so the two 3D views (Map 3D and Tune) and any view
    that is re-created (MSAA step-down) can use the same shader programs;
    pyqtgraph 0.14 caches those per process, not per context.
    """
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(samples)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)


# ---- the scene widget -------------------------------------------------------

class MapScene(QWidget):
    """Host: 2 px frame, the 3D view (or the 2D fallback), labels on top."""

    cameraChanged = Signal()

    def __init__(self, model: MapModel, dist: float = 4.6, el: float = 0.50,
                 key_items=(("live", "live point + 7 s trail"),), hint: str = "drag = orbit",
                 label_px=(16, 20), wire_skip: int = 1, settings=None, parent=None):
        super().__init__(parent)
        self.model = model
        self.cam = Camera(dist, -0.62, el)
        self.settings = settings
        self.title = ""
        self.caption = ""
        self.key_items = key_items
        self.hint = hint
        self.label_px = label_px
        self.wire_skip = wire_skip        # draw every n-th MAP line (small views)
        self.live: tuple | None = None          # (x, y, z) scene position
        self.trail = np.zeros((60, 3), dtype=np.float32)
        self.trail_n = 0
        self._trail_t = 0.0
        self.frame_ms = 0.0
        self._slow = 0
        self._built_rev = -1
        self._built_theme = ""
        self.samples = int(settings.value("gl/msaa", 4)) if settings is not None else 4
        self.gl_ok, self.gl_reason = gl_status()
        # Created up front, not on first use: adding the first QOpenGLWidget to
        # a window that is already showing makes Qt re-create the native
        # window (it loses focus and the GL items lose their context).
        self.view = None
        self.overlay = _Overlay(self)
        self._make_view()

    # -- view management --

    def _make_view(self) -> None:
        old = self.view
        if self.gl_ok:
            try:
                self.view = _GLView(self)
            except Exception as exc:            # driver said yes, then no
                self.gl_ok, self.gl_reason = False, f"{exc.__class__.__name__}: {exc}"
        if not self.gl_ok:
            self.view = _FlatView(self)
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        self.view.setGeometry(2, 2, max(1, self.width() - 4), max(1, self.height() - 4))
        self.view.show()
        self.overlay.raise_()
        self._built_rev = -1

    def resizeEvent(self, _event) -> None:
        self.view.setGeometry(2, 2, self.width() - 4, self.height() - 4)
        self.overlay.setGeometry(2, 2, self.width() - 4, self.height() - 4)
        self.view.apply_camera()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        paint.frame(p, QRectF(self.rect()), 2, T.rule)
        p.end()

    # -- public API --

    def set_text(self, title: str, caption: str) -> None:
        if (title, caption) != (self.title, self.caption):
            self.title, self.caption = title, caption
            self.overlay.relayout()

    def set_live(self, rpm: float | None, kpa: float | None, now: float) -> None:
        """Move the live point (about 10 Hz). None hides it."""
        if rpm is None or kpa is None or rpm != rpm or kpa != kpa:
            if self.live is not None:
                self.live = None
                self.view.live_changed()
            return
        x, y, h, *_ = self.model.locate(rpm, kpa)
        pos = (x, y, h + 0.03)
        if now - self._trail_t >= 0.12:
            self._trail_t = now
            self.trail[:-1] = self.trail[1:]
            self.trail[-1] = pos
            self.trail_n = min(60, self.trail_n + 1)
        if pos != self.live:
            self.live = pos
            self.view.live_changed()

    def set_track(self, cursor, trail) -> None:
        """The live point and its trail from a ``tuning.TableTracer``:
        ``cursor`` is (col, row) as fractional cell indices or None, ``trail``
        the tracer's ``trail()`` rows (col, row, age, alpha), oldest first.
        The scene draws them; it keeps no history of its own for this."""
        if cursor is None:
            if self.live is not None or self.trail_n:
                self.live, self.trail_n = None, 0
                self.view.live_changed()
            return
        model = self.model
        x, y, h, _ = model.at_index(cursor[0], cursor[1])
        pos = (x, y, h + 0.03)
        n = len(trail)
        if n > 60:                              # the scene draws 60 spheres: thin an older, denser trail
            trail = trail[np.linspace(0, n - 1, 60).astype(int)]
            n = 60
        for k in range(n):
            tx, ty, th, _ = model.at_index(float(trail[k, 0]), float(trail[k, 1]))
            self.trail[60 - n + k] = (tx, ty, th + 0.03)
        changed = pos != self.live or n != self.trail_n
        self.live, self.trail_n = pos, n
        if changed or n:
            self.view.live_changed()

    def set_model(self, model: MapModel) -> None:
        """Another table with other axes (a ROM was opened): start over."""
        self.model = model
        self.live, self.trail_n = None, 0
        self._built_rev = -1
        self.refresh()

    def refresh(self) -> None:
        """Rebuild geometry if the model or theme changed since the last build."""
        if self.model.rev != self._built_rev or T.name != self._built_theme:
            self._built_rev, self._built_theme = self.model.rev, T.name
            self.view.rebuild()
            self.overlay.relayout()

    def camera_moved(self) -> None:
        self.view.apply_camera()
        self.overlay.update()
        self.cameraChanged.emit()

    def set_preset(self, name: str) -> None:
        self.cam.preset(name)
        self.camera_moved()

    def orbit(self, daz: float, d_el: float) -> None:
        self.cam.orbit(daz, d_el)
        self.camera_moved()

    def zoom(self, factor: float) -> None:
        self.cam.zoom(factor)
        self.camera_moved()

    def stats(self) -> dict:
        return self.view.stats()

    def note_frame(self, ms: float) -> None:
        """MSAA 4x -> 2x -> off when frames stay above 12 ms."""
        self.frame_ms = ms if self.frame_ms == 0 else 0.9 * self.frame_ms + 0.1 * ms
        self._slow = self._slow + 1 if ms > 12.0 else 0
        if self._slow >= 30 and self.samples > 0:
            self._slow = 0
            self.samples = 2 if self.samples > 2 else 0
            if self.settings is not None:
                self.settings.setValue("gl/msaa", self.samples)
            self._make_view()
            self.refresh()
            self.resizeEvent(None)


class _Overlay(QWidget):
    """Title box, marker key and the axis labels, drawn over the view."""

    def __init__(self, scene: MapScene):
        super().__init__(scene)
        self.scene = scene
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._labels: list = []
        self._pos = np.zeros((0, 3))
        self._cap_lines: list = []

    def relayout(self) -> None:
        self._labels = axis_labels(self.scene.model) if self.scene.gl_ok else []
        self._pos = np.array([lab[0] for lab in self._labels]).reshape(-1, 3)
        self._cap_lines = paint.wrap(T.font(13, 600), self.scene.caption, 536)[:3]
        self.update()

    def paintEvent(self, _event) -> None:
        sc = self.scene
        p = QPainter(self)
        w, h = self.width(), self.height()
        if len(self._labels):
            px = sc.cam.project(self._pos, w, h)
            tick, title = T.font(sc.label_px[0], 700), T.font(sc.label_px[1], 700)
            p.setPen(T.fg)
            for (x, y, depth), (_, text, big) in zip(px, self._labels):
                if depth <= 0:
                    continue
                p.setFont(title if big else tick)
                p.drawText(QRectF(x - 80, y - 14, 160, 28), Qt.AlignCenter, text)
        if sc.title:
            box_h = 12 + 22 + 18 * len(self._cap_lines) + 8
            box = QRectF(-2, -2, 562, box_h)
            paint.frame(p, box, 2, T.rule, T.panel)
            p.setFont(T.font(16, 800))
            p.setPen(T.fg)
            p.drawText(QRectF(12, 8, 540, 22), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(T.font(16, 800), sc.title, 540))
            p.setFont(T.font(13, 600))
            p.setPen(T.fg2)
            for i, line in enumerate(self._cap_lines):
                p.drawText(QRectF(12, 31 + 18 * i, 540, 18), Qt.AlignVCenter | Qt.AlignLeft, line)
        # marker key, bottom right (top right when there are two rows)
        font = T.font(13, 700)
        mono = T.font(13, 700, mono=True)
        rows = len(sc.key_items)
        hint = sc.hint if sc.gl_ok else ""
        if rows == 1:
            text = sc.key_items[0][1]
            kw = 12 + 16 + 8 + paint.text_w(font, text) + (
                10 + paint.text_w(mono, hint) if hint else 0) + 12
            box = QRectF(w - kw + 2, h - 28, kw, 30)
            paint.frame(p, box, 2, T.rule, T.panel)
            self._key_icon(p, sc.key_items[0][0], box.x() + 12, box.y() + 7)
            p.setFont(font)
            p.setPen(T.fg)
            p.drawText(QRectF(box.x() + 36, box.y(), kw, 30), Qt.AlignVCenter | Qt.AlignLeft, text)
            if hint:
                p.setFont(mono)
                p.drawText(QRectF(box.x(), box.y(), kw - 12, 30),
                           Qt.AlignVCenter | Qt.AlignRight, hint)
        else:
            kw = 12 + 16 + 8 + max(paint.text_w(font, t) for _, t in sc.key_items) + 12
            box = QRectF(w - kw + 2, -2, kw, 10 + 20 * rows)
            paint.frame(p, box, 2, T.rule, T.panel)
            for i, (kind, text) in enumerate(sc.key_items):
                self._key_icon(p, kind, box.x() + 12, box.y() + 7 + 20 * i)
                p.setFont(font)
                p.setPen(T.fg)
                p.drawText(QRectF(box.x() + 36, box.y() + 5 + 20 * i, kw, 20),
                           Qt.AlignVCenter | Qt.AlignLeft, text)
        if not sc.gl_ok:
            note = f"2D view: {sc.gl_reason}"
            p.setFont(T.font(12, 700, mono=True))
            p.setPen(T.fg2)
            p.drawText(QRectF(10, h - 22, w - 320, 18), Qt.AlignVCenter | Qt.AlignLeft,
                       paint.elide(T.font(12, 700, mono=True), note, w - 320))
        p.end()

    @staticmethod
    def _key_icon(p, kind, x, y) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        if kind == "live":
            p.setPen(T.cpen(T.fg, 2.5))
            p.setBrush(T.live)
            p.drawEllipse(QRectF(x + 1, y + 1, 14, 14))
            p.setBrush(Qt.NoBrush)
        elif kind == "sel":
            paint.shape(p, paint.DIAMOND, x, y, 16, T.accent)
        elif kind == "stall":
            paint.shape(p, paint.DIAMOND, x, y, 16, T.series1)
        p.setRenderHint(QPainter.Antialiasing, False)


def _rgba(color: QColor):
    return (color.redF(), color.greenF(), color.blueF(), 1.0)


def _build_gl_view():
    """The GL classes are created on first use, so importing this module never
    touches OpenGL (it may not be installed)."""
    import pyqtgraph as pg
    import pyqtgraph.opengl as gl
    from OpenGL import GL

    opaque = {GL.GL_DEPTH_TEST: True, GL.GL_BLEND: False, GL.GL_CULL_FACE: False,
              GL.GL_POLYGON_OFFSET_FILL: False}
    surface_opts = {GL.GL_DEPTH_TEST: True, GL.GL_BLEND: False, GL.GL_CULL_FACE: False,
                    GL.GL_POLYGON_OFFSET_FILL: True, "glPolygonOffset": (1.0, 1.0)}
    shell_opts = {GL.GL_DEPTH_TEST: True, GL.GL_BLEND: False, GL.GL_CULL_FACE: True,
                  GL.GL_POLYGON_OFFSET_FILL: False, "glCullFace": (GL.GL_FRONT,)}
    sprite_opts = {GL.GL_DEPTH_TEST: True, GL.GL_BLEND: True, GL.GL_CULL_FACE: False,
                   GL.GL_POLYGON_OFFSET_FILL: False,
                   "glBlendFunc": (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)}

    def mesh(verts, colors=None, color=None, opts=opaque):
        """Unlit mesh from loose triangles (n*3 vertices)."""
        faces = np.arange(len(verts), dtype=np.uint32).reshape(-1, 3)
        kw = dict(vertexes=np.ascontiguousarray(verts, dtype=np.float32), faces=faces)
        if colors is not None:
            kw["vertexColors"] = np.ascontiguousarray(colors, dtype=np.float32)
        item = gl.GLMeshItem(meshdata=gl.MeshData(**kw), smooth=True, shader=None,
                             computeNormals=False, drawEdges=False,
                             color=color or (1, 1, 1, 1), glOptions="opaque")
        item.setGLOptions(opts)
        item.n_tri = len(faces)
        return item

    def sphere(radius, color, opts=opaque):
        return mesh(sphere_mesh(radius), color=color, opts=opts)

    def lines(pos, color, opts=opaque):
        item = gl.GLLinePlotItem(pos=pos, color=color, width=2.0, mode="lines",
                                 antialias=True, glOptions="opaque")
        item.setGLOptions(opts)
        item.n_tri = 0
        return item

    class GLView(gl.GLViewWidget):
        def __init__(self, scene: MapScene):
            fmt = QSurfaceFormat(QSurfaceFormat.defaultFormat())
            fmt.setSamples(scene.samples)
            super().__init__()
            self.setFormat(fmt)
            self.setParent(scene)
            self.scene = scene
            self.setFocusPolicy(Qt.NoFocus)
            self.dynamic: dict = {}
            self._drag = None
            self.apply_camera()

        def mousePressEvent(self, event):
            self._drag = event.position()

        def mouseMoveEvent(self, event):
            if self._drag is None or not (event.buttons() & Qt.LeftButton):
                return
            d = event.position() - self._drag
            self._drag = event.position()
            self.scene.orbit(-d.x() * 0.008, d.y() * 0.006)

        def mouseReleaseEvent(self, _event):
            self._drag = None

        def wheelEvent(self, event):
            self.scene.zoom(0.999 ** event.angleDelta().y())

        def keyPressEvent(self, event):         # keys belong to the shell
            event.ignore()

        def keyReleaseEvent(self, event):
            event.ignore()

        def paintGL(self, *args, **kwargs):
            t0 = time.perf_counter()
            super().paintGL(*args, **kwargs)
            self.scene.note_frame((time.perf_counter() - t0) * 1000.0)

        def apply_camera(self):
            cam = self.scene.cam
            el, az = cam.pg_angles()
            self.opts["center"] = pg.Vector(*cam.TARGET)
            self.opts["fov"] = cam.fov_h(self.width(), self.height())
            self.setCameraPosition(distance=cam.dist, elevation=el, azimuth=az)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.apply_camera()

        def rebuild(self):
            """Throw the items away and build the scene for the current mode."""
            sc, model = self.scene, self.scene.model
            for item in list(self.items):
                self.removeItem(item)
            self.dynamic = {}
            self.setBackgroundColor(T.bg)
            fg, rule2 = _rgba(T.fg), _rgba(T.rule2)
            self.addItem(lines(frame_lines(model), rule2))
            h = model.heights() * HEIGHT
            col = model.colors()
            gx, gy = np.meshgrid(model.xs, model.ys)
            grid = np.stack([gx, gy, h], axis=-1).astype(np.float32)        # (ny, nx, 3)
            if model.mode == "hist":
                verts, vcol = box_mesh(model, col)
                if len(verts):
                    self.addItem(mesh(verts, vcol))
            else:
                a, b = grid[:-1, :-1], grid[:-1, 1:]
                c, d = grid[1:, :-1], grid[1:, 1:]
                tri = np.stack([a, c, b, b, c, d], axis=2).reshape(-1, 3)
                ca, cb = col[:-1, :-1], col[:-1, 1:]
                cc, cd = col[1:, :-1], col[1:, 1:]
                tcol = np.stack([ca, cc, cb, cb, cc, cd], axis=2).reshape(-1, 3)
                self.addItem(mesh(tri, np.c_[tcol, np.ones(len(tcol), dtype=np.float32)],
                                  opts=surface_opts))
                rows = sorted(set(range(0, model.ny, sc.wire_skip)) | {model.ny - 1})
                along = grid[rows]
                wire = np.concatenate([
                    np.stack([along[:, :-1], along[:, 1:]], axis=2).reshape(-1, 3),
                    np.stack([grid[:-1, :], grid[1:, :]], axis=2).reshape(-1, 3)])
                self.addItem(lines(wire, fg))
            if model.mode == "stall":
                pos, size, pcol, octa, ocol = [], [], [], [], []
                for n, (rpm, kpa) in enumerate(model.paths[:3]):
                    rgba = _rgba(T.color(PATH_TOKENS[n]))
                    pts, radius = path_points(model, rpm, kpa)
                    if not len(pts):
                        continue
                    pos.append(pts)
                    size.append(radius * 2)
                    pcol.append(np.tile(rgba, (len(pts), 1)))
                    end = pts[-1] + (0, 0, 0.065 + 0.02 * n)
                    octa.append(octahedron(end, 0.06))
                    ocol.append(np.tile(rgba, (24, 1)))
                if pos:
                    beads = gl.GLScatterPlotItem(
                        pos=np.concatenate(pos), size=np.concatenate(size),
                        color=np.concatenate(pcol).astype(np.float32), pxMode=False)
                    beads.setGLOptions(sprite_opts)
                    beads.n_tri = 0
                    self.addItem(beads)
                    self.addItem(mesh(np.concatenate(octa), np.concatenate(ocol)))
            else:
                d = self.dynamic
                d["drop"] = lines(np.zeros((6, 3), dtype=np.float32), fg)
                d["trail"] = gl.GLScatterPlotItem(pos=np.zeros((60, 3), dtype=np.float32),
                                                  size=0.0, color=fg, pxMode=False)
                d["trail"].setGLOptions(sprite_opts)
                d["trail"].n_tri = 0
                d["shell"] = sphere(0.052, fg, shell_opts)
                d["point"] = sphere(0.036, _rgba(T.live))
                self._trail_size = np.linspace(0.014, 0.040, 60).astype(np.float32)
                for item in d.values():
                    self.addItem(item)
            if model.sel is not None and model.mode == "surface":
                i, j = model.sel
                center = (model.xs[i], model.ys[j], h[j, i] + 0.07)
                self.addItem(mesh(octahedron(center, 0.045), color=_rgba(T.accent)))
            self.live_changed()

        def live_changed(self):
            d = self.dynamic
            if not d:
                return
            live = self.scene.live
            for item in d.values():
                item.setVisible(live is not None)
            if live is None:
                self.update()
                return
            x, y, z = live
            for key in ("point", "shell"):
                d[key].resetTransform()
                d[key].translate(x, y, z)
            zs = z - 0.03
            d["drop"].setData(pos=np.array([(x, y, 0), (x, y, zs), (x, -0.7, 0), (x, y, 0),
                                            (1, y, 0), (x, y, 0)], dtype=np.float32))
            n = self.scene.trail_n
            size = self._trail_size.copy()
            size[:60 - n] = 0.0
            d["trail"].setData(pos=self.scene.trail, size=size)
            self.update()

        def stats(self) -> dict:
            items = [i for i in self.items if i.visible()]
            labels = len(self.scene.overlay._labels)
            return {"draw_calls": len(items) + labels, "geometry": len(items),
                    "labels": labels,
                    "triangles": int(sum(getattr(i, "n_tri", 0) for i in items)),
                    "frame_ms": self.scene.frame_ms, "msaa": self.scene.samples, "gl": True}

    return GLView


_GLViewClass = None


def _GLView(scene: MapScene):
    global _GLViewClass
    if _GLViewClass is None:
        _GLViewClass = _build_gl_view()
    return _GLViewClass(scene)


class _FlatView(QWidget):
    """2D heat map with the same data, colours and live point. Used when
    OpenGL is not available; needs nothing but QPainter."""

    def __init__(self, scene: MapScene):
        super().__init__(scene)
        self.scene = scene
        self._cache: QPixmap | None = None
        self._grid = QRectF()
        self.setFocusPolicy(Qt.NoFocus)

    def apply_camera(self) -> None:
        pass

    def rebuild(self) -> None:
        self._cache = None
        self.update()

    def live_changed(self) -> None:
        self.update()

    def resizeEvent(self, _event) -> None:
        self._cache = None

    def stats(self) -> dict:
        return {"draw_calls": 0, "geometry": 0, "labels": 0, "triangles": 0,
                "frame_ms": 0.0, "msaa": 0, "gl": False}

    def _render(self) -> QPixmap:
        sc, model = self.scene, self.scene.model
        w, h = self.width(), self.height()
        pm = QPixmap(w, h)
        pm.fill(T.bg)
        p = QPainter(pm)
        top = 116 if sc.title else 40
        g = self._grid = QRectF(70, top, w - 70 - 24, h - top - 84)
        nx, ny = model.nx, model.ny
        cw, ch = g.width() / nx, g.height() / ny
        col = (model.colors() * 255).astype(int)
        hist = model.mode == "hist"
        values = model.hits if hist else model.z
        known = model.hits > 0 if (model.logged or hist) else np.isfinite(model.z)
        decimals = 0 if hist else (2 if (model.z_range[1] - model.z_range[0]) <= 12 else 1)
        font = T.font(12, 700, mono=True)
        p.setFont(font)
        for j in range(ny):
            for i in range(nx):
                # row 0 of the model is the lowest kPa: draw it at the bottom
                r = QRectF(g.x() + i * cw, g.bottom() - (j + 1) * ch, cw, ch)
                c = col[j, i]
                p.fillRect(r, QColor(int(c[0]), int(c[1]), int(c[2])))
                if known[j, i] and model.mode != "stall" and cw >= 38:
                    lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
                    p.setPen(T.bg if lum > 140 else T.fg)
                    p.drawText(r, Qt.AlignCenter, f"{values[j, i]:.{decimals}f}")
        p.setPen(T.pen("bg", 2))          # the cells are separated by the ground, as in the Tune table
        for i in range(nx + 1):
            x = g.x() + i * cw
            p.drawLine(QPointF(x, g.y()), QPointF(x, g.bottom()))
        for j in range(ny + 1):
            y = g.y() + j * ch
            p.drawLine(QPointF(g.x(), y), QPointF(g.right(), y))
        tick, title = T.font(14, 700), T.font(16, 700)
        p.setPen(T.fg)
        p.setFont(tick)
        for i in range(0, nx, 2 if cw < 60 else 1):
            p.drawText(QRectF(g.x() + i * cw - 20, g.bottom() + 6, cw + 40, 18),
                       Qt.AlignCenter, f"{model.rpm[i]:g}")
        for j in range(ny):
            p.drawText(QRectF(0, g.bottom() - (j + 1) * ch, 62, ch),
                       Qt.AlignVCenter | Qt.AlignRight, f"{model.map[j]:g}")
        p.setFont(title)
        p.drawText(QRectF(g.x(), g.bottom() + 26, g.width(), 22), Qt.AlignCenter, "RPM")
        p.drawText(QRectF(4, g.y() - 24, 420, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   f"MAP kPa ↓   cells: {model.z_title()}")
        if model.mode == "stall":
            p.setRenderHint(QPainter.Antialiasing, True)
            for n, (rpm, kpa) in enumerate(model.paths[:3]):
                pts, radius = path_points(model, rpm, kpa)
                colour = T.color(PATH_TOKENS[n])
                p.setPen(Qt.NoPen)
                p.setBrush(colour)
                for (x, y, _), r in zip(pts, radius):
                    c = self._flat(x, y)
                    p.drawEllipse(c, r * 260, r * 260)
                if len(pts):
                    c = self._flat(pts[-1][0], pts[-1][1])
                    paint.shape(p, paint.DIAMOND, c.x() - 12, c.y() - 12, 24, colour)
        if model.sel is not None and model.mode == "surface":
            i, j = model.sel
            r = QRectF(g.x() + i * cw, g.bottom() - (j + 1) * ch, cw, ch)
            p.setBrush(Qt.NoBrush)
            p.setPen(T.cpen(T.accent, 3, dash=(3.0, 2.0)))
            p.drawRect(r.adjusted(1.5, 1.5, -1.5, -1.5))
        p.end()
        return pm

    def _flat(self, x: float, y: float) -> QPointF:
        g = self._grid
        # scene y runs +0.7 (low kPa) .. -0.7 (high kPa); low kPa is at the bottom
        return QPointF(g.x() + (x + 1.0) / 2.0 * g.width(),
                       g.bottom() - (0.7 - y) / 1.4 * g.height())

    def paintEvent(self, _event) -> None:
        if self._cache is None:
            self._cache = self._render()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        sc = self.scene
        if sc.live is not None and sc.model.mode != "stall":
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(Qt.NoPen)
            p.setBrush(T.fg)
            n = sc.trail_n
            for k in range(60 - n, 60):
                x, y, _ = sc.trail[k]
                r = 2.0 + 4.0 * k / 60.0
                p.drawEllipse(self._flat(float(x), float(y)), r, r)
            c = self._flat(sc.live[0], sc.live[1])
            p.setPen(T.cpen(T.fg, 4))
            p.setBrush(T.live)
            p.drawEllipse(c, 10, 10)
        p.end()
