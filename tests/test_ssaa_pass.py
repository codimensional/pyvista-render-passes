"""The volume-safe SSAA pass and the SSAO helpers."""

from __future__ import annotations

import numpy as np
import pytest
import pyvista as pv

import pyvista_render_passes as prp
from tests.backend import (
    vtkCameraPass,
    vtkCommand,
    vtkOpenGLRenderPass,
    vtkRenderPass,
    vtkRenderStepsPass,
    vtkSequencePass,
    vtkSSAOPass,
)
from tests.conftest import vtk_stderr


def test_ssaa_volume_pass_is_opengl_render_pass():
    # Unlike stock vtkSSAAPass, so it registers the RenderPasses() key the GPU
    # volume mapper reads its viewport from.
    assert issubclass(prp.pvSSAAVolumePass, vtkOpenGLRenderPass)
    assert isinstance(prp.pvSSAAVolumePass(), vtkRenderPass)


def test_delegate_pass_roundtrip():
    p = prp.pvSSAAVolumePass()
    delegate = vtkRenderStepsPass()
    p.SetDelegatePass(delegate)
    assert p.GetDelegatePass() is delegate


def test_ssaa_defaults():
    p = prp.pvSSAAVolumePass()
    assert p.GetSupersampleFactor() == pytest.approx(np.sqrt(5.0))
    assert p.GetPrimitiveScaleFactor() == pytest.approx(np.sqrt(5.0))
    assert p.GetResolveDepth()


def test_ssaa_primitive_scale_factor_clamps_to_native_size():
    p = prp.pvSSAAVolumePass()
    p.SetPrimitiveScaleFactor(0.5)
    assert p.GetPrimitiveScaleFactor() == pytest.approx(1.0)


@pytest.mark.parametrize(('requested', 'expected'), [(10.0, 4.0), (0.2, 1.0), (2.0, 2.0)])
def test_ssaa_supersample_factor_clamps(requested, expected):
    p = prp.pvSSAAVolumePass()
    p.SetSupersampleFactor(requested)
    assert p.GetSupersampleFactor() == pytest.approx(expected)


def test_ssaa_supersample_factor_rejects_nonfinite():
    p = prp.pvSSAAVolumePass()
    p.SetSupersampleFactor(2.0)
    p.SetSupersampleFactor(float('nan'))
    assert p.GetSupersampleFactor() == pytest.approx(2.0)


def test_make_ssaa_pass_sets_factor_and_primitive_scale():
    p = prp.make_ssaa_pass(factor=1.5)
    assert isinstance(p, prp.pvSSAAVolumePass)
    assert isinstance(p.GetDelegatePass(), vtkCameraPass)
    assert p.GetSupersampleFactor() == pytest.approx(1.5)
    assert p.GetPrimitiveScaleFactor() == pytest.approx(1.5)


@pytest.mark.parametrize('factor', [0.5, 4.5, 0.999, 4.001, float('nan'), float('inf')])
def test_make_ssaa_pass_rejects_invalid_factor(factor):
    with pytest.raises(ValueError, match='factor'):
        prp.make_ssaa_pass(factor=factor)


@pytest.mark.parametrize('factor', [1.0, 4.0])
def test_enable_ssaa_accepts_inclusive_factor_bounds(factor):
    pl = pv.Plotter()
    prp.enable_ssaa(pl, factor=factor)
    assert pl.renderer.GetPass().GetSupersampleFactor() == pytest.approx(factor)


def test_enable_ssaa_rejects_invalid_factor():
    pl = pv.Plotter()
    with pytest.raises(ValueError, match='factor'):
        prp.enable_ssaa(pl, factor=10.0)


def test_enable_ssaa_replaces_rather_than_stacks():
    pl = pv.Plotter()
    prp.enable_ssaa(pl)
    prp.enable_ssaa(pl)
    installed = pl.renderer.GetPass()
    assert isinstance(installed, prp.pvSSAAVolumePass)
    assert isinstance(installed.GetDelegatePass(), vtkCameraPass)


def test_ssaa_scales_and_restores_primitive_properties():
    pl = pv.Plotter(off_screen=True, window_size=(160, 160))
    actor = pl.add_lines(
        np.array([[-0.7, 0.0, 0.0], [0.7, 0.0, 0.0]], dtype=float), color='white', width=3
    )
    actor.prop.point_size = 5
    prop = actor.prop
    observed: list[tuple[float, float]] = []
    observer = prop.AddObserver(
        vtkCommand.ModifiedEvent, lambda obj, _: observed.append((obj.point_size, obj.line_width))
    )
    prp.enable_ssaa(pl)
    pl.renderer.GetPass().SetPrimitiveScaleFactor(2.0)
    pl.camera_position = 'xy'
    pl.render_window.Render()
    prop.RemoveObserver(observer)

    assert any(point_size == pytest.approx(10.0) for point_size, _ in observed)
    assert any(line_width == pytest.approx(6.0) for _, line_width in observed)
    assert prop.point_size == pytest.approx(5.0)
    assert prop.line_width == pytest.approx(3.0)


def _filled_cube_scene(*, factor: float | None, size: int = 240) -> np.ndarray:
    pl = pv.Plotter(off_screen=True, window_size=(size, size))
    pl.set_background('black')
    mesh = pv.Cube().triangulate().rotate_z(17, inplace=False).rotate_x(23, inplace=False)
    pl.add_mesh(mesh, color='white', show_edges=False, lighting=False, render=False)
    if factor is not None:
        prp.enable_ssaa(pl, factor=factor)
    pl.camera_position = 'iso'
    pl.render_window.Render()
    img = np.asarray(pl.screenshot(return_img=True))[..., :3].astype(np.int16)
    pl.close()
    return img.mean(axis=2)


def _partial_coverage_px(grey: np.ndarray) -> int:
    return int(((grey > 20) & (grey < 235)).sum())


def test_ssaa_factor_one_matches_no_pass():
    plain = _filled_cube_scene(factor=None)
    passthrough = _filled_cube_scene(factor=1.0)
    assert int(np.abs(plain - passthrough).max()) <= 2


def test_ssaa_higher_factor_is_smoother():
    counts = [_partial_coverage_px(_filled_cube_scene(factor=f)) for f in (1.0, 1.5, np.sqrt(5.0))]
    assert counts[0] < 50, counts
    assert counts[1] > 200, counts
    assert counts[2] > 200, counts


def _blob(dims: tuple[int, int, int] = (32, 32, 32)) -> pv.ImageData:
    grid = pv.ImageData(dimensions=dims)
    x, y, z = np.mgrid[0 : dims[0], 0 : dims[1], 0 : dims[2]]
    center = (np.array(dims) - 1) / 2.0
    radius = np.sqrt((x - center[0]) ** 2 + (y - center[1]) ** 2 + (z - center[2]) ** 2)
    grid.point_data['T'] = (100.0 - radius).ravel(order='F').astype(np.float32)
    return grid


def _volume_mask(*, ssaa: bool, factor: float = np.sqrt(5.0), size: int = 300) -> np.ndarray:
    pl = pv.Plotter(off_screen=True, window_size=(size, size))
    pl.set_background('black')
    pl.add_volume(_blob(), scalars='T', opacity='linear', show_scalar_bar=False)
    if ssaa:
        prp.enable_ssaa(pl, factor=factor)
    pl.camera_position = 'iso'
    pl.render_window.Render()
    img = np.asarray(pl.screenshot(return_img=True))[..., :3]
    pl.close()
    return img.max(axis=2) > 30


def _dilate(mask: np.ndarray) -> np.ndarray:
    out = mask.copy()
    out[1:, :] |= mask[:-1, :]
    out[:-1, :] |= mask[1:, :]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


@pytest.mark.parametrize('factor', [1.5, np.sqrt(5.0)])
def test_volume_renders_whole_through_ssaa(factor):
    # Stock SSAA leaves the GPU volume mapper reading the logical window size,
    # which drops a notch of the volume or blanks it. The silhouette must match.
    plain = _volume_mask(ssaa=False)
    ssaa = _volume_mask(ssaa=True, factor=factor)
    assert int(plain.sum()) > 1000
    missing = int((plain & ~_dilate(ssaa)).sum())
    assert missing < 0.02 * int(plain.sum()), (factor, missing, int(plain.sum()))


def _line_thickness(factor: float | None, *, width: int = 4, size: int = 200) -> float:
    pl = pv.Plotter(off_screen=True, window_size=(size, size))
    pl.set_background('black')
    pl.add_lines(
        np.array([[-0.85, 0.0, 0.0], [0.85, 0.0, 0.0]], dtype=float), color='white', width=width
    )
    if factor is not None:
        prp.enable_ssaa(pl, factor=factor)
    pl.camera_position = 'xy'
    pl.render_window.Render()
    grey = np.asarray(pl.screenshot(return_img=True))[..., :3].mean(axis=2)
    pl.close()
    lit_cols = int((grey.max(axis=0) > 30).sum())
    return float(grey.sum() / 255.0 / max(lit_cols, 1))


def test_ssaa_primitive_width_constant_across_factors():
    thick = {f: _line_thickness(f) for f in (1.0, 1.5, np.sqrt(5.0))}
    base = thick[1.0]
    assert base > 1.0
    for f, t in thick.items():
        assert abs(t - base) / base < 0.35, (f, thick)


def _lit_pixels(img: np.ndarray) -> int:
    return int((img.max(axis=2) > 12).sum())


def test_make_ssao_pass_applies_its_arguments():
    ssao = prp.make_ssao_pass(radius=0.25, bias=0.01, kernel_size=64, blur=False)
    assert isinstance(ssao, vtkSSAOPass)
    assert ssao.GetRadius() == pytest.approx(0.25)
    assert ssao.GetBias() == pytest.approx(0.01)
    assert ssao.GetKernelSize() == 64
    assert ssao.GetBlur() is False


def test_make_ssao_pass_builds_the_full_delegate_chain():
    camera = prp.make_ssao_pass().GetDelegatePass()
    assert isinstance(camera, vtkCameraPass)
    assert isinstance(camera.GetDelegatePass(), vtkSequencePass)


@pytest.mark.parametrize(
    ('radius', 'bias', 'kernel_size'),
    [
        pytest.param(0.0, 0.005, 256, id='zero-radius'),
        pytest.param(-1.0, 0.005, 256, id='negative-radius'),
        pytest.param(float('nan'), 0.005, 256, id='nan-radius'),
        pytest.param(0.5, -0.1, 256, id='negative-bias'),
        pytest.param(0.5, 0.005, 0, id='kernel-below-range'),
        pytest.param(0.5, 0.005, 1001, id='kernel-above-range'),
    ],
)
def test_make_ssao_pass_rejects_out_of_range_settings(radius, bias, kernel_size):
    with pytest.raises(ValueError, match=r'radius|bias|kernel_size'):
        prp.make_ssao_pass(radius=radius, bias=bias, kernel_size=kernel_size)


def test_enable_ssao_installs_a_configured_pass_on_every_renderer():
    pl = pv.Plotter(off_screen=True, shape=(1, 2))
    prp.enable_ssao(pl, radius=0.25, kernel_size=64, blur=False)

    passes = [renderer.GetPass() for renderer in pl.renderers]
    assert len({id(p) for p in passes}) == len(passes)
    for installed in passes:
        assert isinstance(installed, vtkSSAOPass)
        assert installed.GetRadius() == pytest.approx(0.25)
        assert installed.GetKernelSize() == 64
        assert installed.GetBlur() is False


# -- subplots ------------------------------------------------------------------


def _subplot_tiles(shape: tuple[int, int], setup) -> list[np.ndarray]:
    rows, cols = shape
    pl = pv.Plotter(off_screen=True, shape=shape, window_size=(160 * cols, 120 * rows))
    for index in range(rows * cols):
        pl.subplot(index // cols, index % cols)
        pl.set_background('black')
        pl.add_mesh(pv.Sphere(radius=0.8), color='white')
        pl.add_mesh(pv.Cube(center=(0.5, 0.5, -0.5)), color='lightgray')
        setup(pl, index)
    img = np.asarray(pl.screenshot(return_img=True))[..., :3]
    pl.close()
    h, w = img.shape[:2]
    return [
        img[r * h // rows : (r + 1) * h // rows, c * w // cols : (c + 1) * w // cols]
        for r in range(rows)
        for c in range(cols)
    ]


def _install(pl: pv.Plotter, **flags: object) -> None:
    chain = prp.pvRenderPassChain()
    chain.SetAnnotationBypass(False)
    for name, value in flags.items():
        getattr(chain, f'Set{name}')(value)
    pl.renderer.SetPass(chain.Build(pl.renderer))
    pl.renderer._pass_chain = chain  # the test holds it; the fixture releases it


@pytest.mark.parametrize('shape', [(1, 2), (2, 1), (2, 2)])
@pytest.mark.parametrize(
    'flags',
    [
        pytest.param({'EDL': True}, id='edl'),
        pytest.param({'Blur': True}, id='blur'),
        pytest.param({'DepthOfField': True}, id='dof'),
        pytest.param({'EDL': True, 'SSAO': True, 'AntiAliasing': True}, id='edl-ssao-ssaa'),
    ],
)
def test_screen_space_passes_render_every_subplot_alike(shape, flags):
    # vtkEDLShading and the image-processing passes clear and composite the
    # whole window from inside one subplot, wiping the others (VTK #18849).
    plain = _subplot_tiles(shape, lambda pl, i: None)
    tiles = _subplot_tiles(shape, lambda pl, i: _install(pl, **flags))
    lit = [_lit_pixels(t) for t in tiles]
    assert min(lit) > 0.25 * _lit_pixels(plain[0]), lit
    assert max(lit) - min(lit) <= 0.02 * max(lit), lit
    for tile in tiles[1:]:
        assert np.abs(tile.astype(int) - tiles[0].astype(int)).mean() < 2.0


def test_edl_in_one_subplot_leaves_the_others_untouched():
    plain = _subplot_tiles((1, 2), lambda pl, i: None)
    tiles = _subplot_tiles((1, 2), lambda pl, i: _install(pl, EDL=True) if i == 1 else None)
    assert np.array_equal(tiles[0], plain[0])
    assert not np.array_equal(tiles[1], plain[1])
    assert _lit_pixels(tiles[1]) > 0.25 * _lit_pixels(plain[1])


def test_ssaa_pass_survives_a_release_and_re_render():
    # vtkOpenGLHelper::ReleaseGraphicsResources nulls the program but keeps the
    # helper, so a pass that only forwards the release re-readies a null program
    # and freezes on the pre-release frame. The zoom makes a live pass visible.
    shots: dict[str, np.ndarray] = {}
    with vtk_stderr() as log:
        pl = pv.Plotter(off_screen=True, window_size=(200, 200))
        pl.set_background('black')
        pl.add_mesh(pv.Sphere(radius=0.8), color='white')
        ssaa = prp.make_ssaa_pass()
        pl.renderer.SetPass(ssaa)
        shots['before'] = pl.screenshot(return_img=True)
        ssaa.ReleaseGraphicsResources(pl.render_window)
        pl.camera.zoom(2.0)
        pl.render_window.Render()
        shots['after'] = pl.screenshot(return_img=True)
        pl.close()

    before, after = _lit_pixels(shots['before']), _lit_pixels(shots['after'])
    assert "Couldn't build the SSAA downsample shader program" not in log['text'], log['text']
    assert before > 1000
    assert after > 1.5 * before
