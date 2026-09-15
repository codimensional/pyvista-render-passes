"""The SSAA depth resolve, and opaque occlusion of a stock GPU volume under SSAA.

Without the resolve, the supersampled depth is discarded and a window depth
read returns stale or empty depth; the resolve is on by default and every test
that reads window depth sets it explicitly.
"""

from __future__ import annotations

import numpy as np
import pytest
import pyvista as pv

import pyvista_render_passes as prp
from tests.backend import numpy_support, vtkFloatArray


def _window_depth(pl: pv.Plotter) -> np.ndarray:
    rw = pl.render_window
    w, h = rw.GetSize()
    arr = vtkFloatArray()
    rw.GetZbufferData(0, 0, w - 1, h - 1, arr)
    return numpy_support.vtk_to_numpy(arr).reshape(h, w)


def _enable_ssaa_with_depth(pl: pv.Plotter, factor: float | None = None) -> None:
    prp.enable_ssaa(pl)
    for renderer in pl.renderers:
        renderer.GetPass().SetResolveDepth(True)
        if factor is not None:
            renderer.GetPass().SetSupersampleFactor(factor)
            renderer.GetPass().SetPrimitiveScaleFactor(factor)


def _tilted_plane_depth(*, ssaa: bool, size: int = 200) -> np.ndarray:
    # A tilted plane filling the view: a depth gradient with no silhouettes.
    pl = pv.Plotter(off_screen=True, window_size=(size, size))
    pl.set_background('black')
    plane = pv.Plane(center=(0, 0, 0), direction=(0, 0, 1), i_size=12, j_size=12)
    plane.rotate_x(35, inplace=True)
    pl.add_mesh(plane, color='gray', render=False)
    pl.camera_position = [(0, 0, 4), (0, 0, 0), (0, 1, 0)]
    if ssaa:
        _enable_ssaa_with_depth(pl)
    pl.render_window.Render()
    depth = _window_depth(pl)
    pl.close()
    return depth


def _occluder_plotter(size: int) -> pv.Plotter:
    pl = pv.Plotter(off_screen=True, window_size=(size, size))
    pl.set_background('black')
    pl.add_mesh(
        pv.Plane(center=(0, 0, -1.5), direction=(0, 0, 1), i_size=10, j_size=10),
        color='blue',
        render=False,
    )
    pl.add_mesh(
        pv.Plane(center=(0, 0, 1.0), direction=(0, 0, 1), i_size=1.2, j_size=1.2),
        color='red',
        render=False,
    )
    pl.camera_position = [(0, 0, 5), (0, 0, 0), (0, 1, 0)]
    return pl


def _occluder_depth(*, ssaa: bool, volume: bool, size: int = 200) -> np.ndarray:
    pl = _occluder_plotter(size)
    if volume:
        grid = pv.ImageData(dimensions=(32, 32, 32), spacing=(0.06,) * 3, origin=(-0.96,) * 3)
        grid['v'] = np.linalg.norm(grid.points, axis=1).astype(np.float32)
        pl.add_volume(grid, scalars='v', opacity=np.linspace(0.0, 0.4, 16), show_scalar_bar=False)
    if ssaa:
        _enable_ssaa_with_depth(pl)
    pl.render_window.Render()
    depth = _window_depth(pl)
    pl.close()
    return depth


def test_ssaa_window_depth_is_not_all_ones():
    depth = _tilted_plane_depth(ssaa=True)
    in_unit = (depth > 0.0) & (depth < 1.0)
    assert in_unit.mean() > 0.99, in_unit.mean()
    assert 0.0 < depth.min() < depth.max() < 1.0, (depth.min(), depth.max())


def test_ssaa_depth_matches_no_ssaa_on_tilted_surface():
    off = _tilted_plane_depth(ssaa=False)
    on = _tilted_plane_depth(ssaa=True)
    valid = (off < 1.0) & (on < 1.0)
    assert valid.mean() > 0.99, valid.mean()
    diff = np.abs(off - on)[valid]
    assert diff.max() < 0.02, (diff.max(), diff.mean())


@pytest.mark.parametrize('volume', [False, True], ids=['opaque', 'with_volume'])
def test_ssaa_depth_occludes(volume):
    depth = _occluder_depth(ssaa=True, volume=volume)
    h, w = depth.shape
    near_med = float(np.median(depth[h // 2 - 5 : h // 2 + 5, w // 2 - 5 : w // 2 + 5]))
    far = depth[5:25, 5:25]
    far_valid = far[far < 1.0]
    assert far_valid.size > 50
    far_med = float(np.median(far_valid))
    assert 0.0 < near_med < 1.0, near_med
    assert 0.0 < far_med < 1.0, far_med
    assert near_med < far_med - 0.05, (near_med, far_med)


@pytest.mark.parametrize('volume', [False, True], ids=['opaque', 'with_volume'])
def test_ssaa_occluder_far_surface_depth_matches_no_ssaa(volume):
    off = _occluder_depth(ssaa=False, volume=volume)
    on = _occluder_depth(ssaa=True, volume=volume)
    h, w = on.shape
    patch = (slice(int(h * 0.05), int(h * 0.22)), slice(int(w * 0.05), int(w * 0.22)))
    off_p, on_p = off[patch], on[patch]
    valid = (off_p < 1.0) & (on_p < 1.0)
    assert valid.mean() > 0.9, valid.mean()
    diff = np.abs(off_p - on_p)[valid]
    assert diff.max() < 0.02, (diff.max(), diff.mean())


def _occluder_color(*, resolve_depth: bool, size: int = 200) -> np.ndarray:
    pl = _occluder_plotter(size)
    prp.enable_ssaa(pl)
    for renderer in pl.renderers:
        renderer.GetPass().SetResolveDepth(resolve_depth)
    pl.render_window.Render()
    img = pl.screenshot(return_img=True)
    pl.close()
    return np.asarray(img)


def test_ssaa_depth_resolve_does_not_perturb_color():
    off = _occluder_color(resolve_depth=False)
    on = _occluder_color(resolve_depth=True)
    assert off.std() > 1.0
    assert np.array_equal(off, on)


def _thin_bar_depth(*, ssaa: bool, factor: float = 2.5, size: int = 200) -> np.ndarray:
    # A near bar a couple of window pixels wide: the regime where min, max and
    # average reductions are macroscopically distinguishable at its silhouettes.
    pl = pv.Plotter(off_screen=True, window_size=(size, size))
    pl.set_background('black')
    pl.add_mesh(
        pv.Plane(center=(0, 0, -1.5), direction=(0, 0, 1), i_size=10, j_size=10),
        color='blue',
        render=False,
    )
    bar = pv.Plane(center=(0, 0, 1.0), direction=(0, 0, 1), i_size=0.10, j_size=6.0)
    pl.add_mesh(bar, color='red', render=False)
    pl.camera_position = [(0, 0, 5), (0, 0, 0), (0, 1, 0)]
    if ssaa:
        _enable_ssaa_with_depth(pl, factor)
    pl.render_window.Render()
    depth = _window_depth(pl)
    pl.close()
    return depth


def test_ssaa_thin_feature_depth_haloes_near_not_far_or_avg():
    # min haloes the near bar (at least as wide as SSAA-off); max erodes it;
    # average leaves a band of intermediate depths at the silhouette.
    on = _thin_bar_depth(ssaa=True)
    off = _thin_bar_depth(ssaa=False)
    h, _ = on.shape

    bar_px_on = on[(on < 0.6) & (on < 1.0)]
    assert bar_px_on.size > 200, bar_px_on.size
    near_ref = float(np.median(bar_px_on))
    far_ref = float(np.median(on[5:20, 5:20]))
    assert 0.0 < near_ref < 1.0, near_ref
    assert 0.0 < far_ref < 1.0, far_ref
    mid_ref = 0.5 * (near_ref + far_ref)
    tol = 0.02
    assert near_ref + tol < mid_ref < far_ref - tol, (near_ref, mid_ref, far_ref)

    band = slice(h // 4, 3 * h // 4)
    on_bar_px = int(((on[band] < mid_ref) & (on[band] < 1.0)).sum())
    off_bar_px = int(((off[band] < mid_ref) & (off[band] < 1.0)).sum())
    assert off_bar_px > 100, off_bar_px
    assert on_bar_px >= off_bar_px, (on_bar_px, off_bar_px)

    valid = on[on < 1.0]
    intermediate = (valid > near_ref + tol) & (valid < far_ref - tol)
    assert intermediate.sum() == 0, int(intermediate.sum())


# -- opaque occlusion of a stock GPU volume under SSAA --------------------------

_OPACITY = np.linspace(0.0, 0.9, 16)
_OPACITY[:2] = 0.0


def _back_blob_volume() -> pv.ImageData:
    grid = pv.ImageData(dimensions=(48, 48, 48), spacing=(1, 1, 1), origin=(-24, -24, -24))
    z = grid.points[:, 2]
    rxy = np.linalg.norm(grid.points[:, :2], axis=1)
    blob = np.clip(1.0 - np.abs(z + 16.0) / 8.0, 0, 1) * np.clip(1.0 - rxy / 16.0, 0, 1)
    grid['T'] = blob.astype(np.float32)
    return grid


def _render_volume_behind_plane(*, ssaa: bool, plane: bool) -> np.ndarray:
    pl = pv.Plotter(off_screen=True, window_size=(300, 300))
    pl.set_background('black')
    pl.add_volume(
        _back_blob_volume(), scalars='T', opacity=_OPACITY, cmap='hot', show_scalar_bar=False
    )
    if plane:
        pl.add_mesh(
            pv.Plane(center=(0, 0, 0), direction=(0, 0, 1), i_size=80, j_size=80), color='blue'
        )
    pl.camera_position = [(0, 0, 80), (0, 0, 0), (0, 1, 0)]
    if ssaa:
        prp.enable_ssaa(pl)
    pl.render_window.Render()
    img = np.asarray(pl.screenshot(return_img=True))[..., :3]
    pl.close()
    return img


def _hot_pixels(img: np.ndarray) -> int:
    r, _g, b = (img[..., i].astype(int) for i in range(3))
    return int(((r > 120) & (r - b > 60)).sum())


def test_opaque_geometry_occludes_stock_volume_under_ssaa():
    # The volume ray march must terminate at the opaque plane in front of it
    # while SSAA renders into its enlarged framebuffer.
    blob_only = _hot_pixels(_render_volume_behind_plane(ssaa=False, plane=False))
    assert blob_only > 2000, blob_only
    occluded_ssaa = _hot_pixels(_render_volume_behind_plane(ssaa=True, plane=True))
    assert occluded_ssaa < blob_only // 10, (occluded_ssaa, blob_only)
