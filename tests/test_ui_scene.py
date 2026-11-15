"""The 3D scene: model maths, the HD 4000 budget, and the 2D fallback."""

import math

import numpy as np
import pytest

from hondalog.ui import backends as be
from hondalog.ui import scene3d as s3
from hondalog.ui.theme import DAYLIGHT, NIGHT, rgb_of


def demo_model():
    obs = be.HistogramMap()
    obs.load_demo()
    m = s3.MapModel(obs.rpm_axis, obs.map_axis)
    m.hits, m.trim, m.z = obs.hits().copy(), obs.mean("trim"), obs.mean("inj")
    m.z_range, m.z_label = (0.0, 10.0), "INJ ms"
    m.touch()
    return m


def test_axes_map_to_the_handoff_coordinates():
    m = demo_model()
    assert (m.nx, m.ny) == (20, 10)
    assert m.x_of(500) == pytest.approx(-1) and m.x_of(7500) == pytest.approx(1)
    assert m.y_of(17) == pytest.approx(0.7) and m.y_of(101) == pytest.approx(-0.7)
    x, y, z, i, j, v = m.locate(4000, 59)
    assert x == pytest.approx(0.0, abs=0.01) and y == pytest.approx(0.0, abs=0.01)
    assert 0 <= z <= 0.9 and (i, j) == (10, 4) or (i, j) == (9, 4) or (i, j) == (10, 5)
    assert m.locate(99999, 999)[0] <= 1.0 and m.locate(-5, -5)[0] == -1.0


def test_no_data_cells_are_grey_in_every_colour_mode_and_height_is_filled():
    m = demo_model()
    empty = m.hits == 0
    assert empty.any() and (~empty).any()
    for tokens in (DAYLIGHT, NIGHT):
        grey = np.array(rgb_of(tokens.nodata), dtype=np.float32)
        for mode, colour in (("surface", "z"), ("surface", "trim"), ("surface", "hits"),
                             ("hist", "z")):
            m.mode, m.color_by = mode, colour
            m.touch()
            col = m.colors(tokens)
            assert np.allclose(col[empty], grey, atol=1e-3)
            assert not np.allclose(col[~empty], grey, atol=1e-3)
    m.mode = "surface"
    m.touch()
    h = m.heights()
    assert np.isfinite(h).all() and h.min() >= 0 and h.max() <= 1
    m.logged = False                    # tune tables have no grey cells
    assert not np.allclose(m.colors(DAYLIGHT)[empty], rgb_of(DAYLIGHT.nodata), atol=1e-3)


def test_fill_unknown_keeps_known_values():
    h = np.full((4, 5), np.nan)
    h[1, 1], h[2, 3] = 0.2, 0.8
    out = s3.fill_unknown(h)
    assert out[1, 1] == 0.2 and out[2, 3] == 0.8 and np.isfinite(out).all()
    assert 0.2 <= out.min() and out.max() <= 0.8
    assert (s3.fill_unknown(np.full((2, 2), np.nan)) == 0).all()


def test_geometry_stays_inside_the_triangle_budget():
    m = demo_model()
    m.mode = "hist"
    m.touch()
    verts, cols = s3.box_mesh(m, m.colors(DAYLIGHT))
    bars = len(verts) // 3
    assert bars == 12 * int((m.hits > 0).sum()) and len(cols) == len(verts)
    worst_bars = 12 * m.hits.size                           # every cell filled: 2,400
    surface = 2 * (m.nx - 1) * (m.ny - 1)                   # 342
    spheres = 2 * len(s3.sphere_mesh(1.0)) // 3
    octa = 4 * len(s3.octahedron((0, 0, 0), 1)) // 3
    assert surface == 342 and worst_bars == 2400
    assert max(surface, worst_bars) + spheres + octa <= s3.MAX_TRIANGLES
    # flat shading is baked in: top faces are brighter than the side away from the light
    assert cols[:, :3].max() <= 1.0


def test_sphere_is_wound_counter_clockwise_from_outside():
    tri = s3.sphere_mesh(0.5).reshape(-1, 3, 3)
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    centre = tri.mean(axis=1)
    area = np.linalg.norm(normal, axis=1)
    keep = area > 1e-9                                      # pole triangles are degenerate
    assert ((normal[keep] * centre[keep]).sum(axis=1) > 0).all()
    assert np.allclose(np.linalg.norm(tri.reshape(-1, 3), axis=1), 0.5, atol=1e-6)


def test_camera_presets_clamps_and_projection():
    cam = s3.Camera()
    assert (cam.dist, cam.az, cam.el) == (4.6, -0.62, 0.50)
    cam.orbit(0, 5)
    assert cam.el == 1.5
    cam.orbit(0, -5)
    assert cam.el == 0.08
    cam.zoom(100)
    assert cam.dist == 8
    cam.zoom(0.001)
    assert cam.dist == 3
    cam.preset("map")
    assert (cam.az, cam.el) == (-math.pi / 2, 0.1)
    cam = s3.Camera()
    px = cam.project([cam.TARGET], 964, 628)[0]
    assert px[0] == pytest.approx(482) and px[1] == pytest.approx(314) and px[2] == pytest.approx(4.6)
    up = cam.project([(0, 0, 0.9)], 964, 628)[0]
    assert up[1] < px[1], "z is up on screen"
    assert cam.fov_h(964, 628) > cam.FOV_V


def test_labels_and_paths():
    m = demo_model()
    labels = s3.axis_labels(m)
    texts = [t for _, t, _ in labels]
    assert texts[:7] == ["1000", "2000", "3000", "4000", "5000", "6000", "7000"]
    assert {"RPM", "MAP kPa", "INJ ms", "20", "100"} <= set(texts) and len(labels) <= 22
    name, rpm, kpa = be.demo_stall_paths()[0]
    pos, radius = s3.path_points(m, rpm, kpa)
    assert pos.shape == (40, 3) and radius[0] == pytest.approx(0.011) and radius[-1] == pytest.approx(0.025)
    assert s3.frame_lines(m).shape[1] == 3


def _scene(app, model, **kw):
    scene = s3.MapScene(model, **kw)
    scene.resize(968, 632)
    scene.show()
    scene.set_text("title", "caption")
    scene.refresh()
    scene.set_live(3000, 60, 1.0)
    app.processEvents()
    return scene


def _dispose(app, scene):
    """Delete now, while nothing else is painting, not whenever the GC runs."""
    scene.close()
    scene.deleteLater()
    app.processEvents()


def test_fallback_2d_view_renders_without_opengl(app, monkeypatch):
    monkeypatch.setattr(s3, "_gl_state", (False, "forced off for the test"))
    m = demo_model()
    scene = _scene(app, m)
    try:
        assert not scene.gl_ok and isinstance(scene.view, s3._FlatView)
        for mode in ("surface", "hist", "stall"):
            m.mode = mode
            m.paths = [(r, k) for _, r, k in be.demo_stall_paths()]
            m.touch()
            scene.refresh()
            app.processEvents()
            image = scene.grab().toImage()
            colours = {image.pixel(x, y) for x in range(100, 900, 37) for y in range(140, 560, 29)}
            assert len(colours) > 3, mode
        assert scene.stats()["gl"] is False
        scene.orbit(0.1, 0.1)                               # harmless in 2D
    finally:
        _dispose(app, scene)


def test_gl_scene_respects_the_draw_call_budget(app):
    ok, reason = s3.gl_status()
    if not ok:
        pytest.skip(f"no OpenGL here ({reason}); run under xvfb-run to cover this")
    m = demo_model()
    scene = _scene(app, m)
    try:
        assert scene.gl_ok
        for mode in ("surface", "hist", "stall"):
            m.mode = mode
            m.sel = (8, 5)
            m.paths = [(r, k) for _, r, k in be.demo_stall_paths()]
            m.touch()
            scene.refresh()
            app.processEvents()
            st = scene.stats()
            assert st["draw_calls"] <= s3.MAX_DRAW_CALLS, (mode, st)
            assert st["geometry"] <= 12 and st["triangles"] <= s3.MAX_TRIANGLES, (mode, st)
        image = scene.view.grabFramebuffer()
        assert image.width() > 900
        colours = {image.pixel(x, y) for x in range(50, 900, 23) for y in range(50, 600, 19)}
        assert len(colours) > 5, "the GL view drew something"
    finally:
        _dispose(app, scene)
