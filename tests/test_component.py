"""``plotter.render_passes``: state, dispatch to the chain, and the lifecycle."""

from __future__ import annotations

import gc
import logging
import warnings

import numpy as np
import pytest
import pyvista as pv

import pyvista_render_passes as prp
from pyvista_render_passes import DEFAULT_SSAA_FACTOR, RenderPassComponent
from pyvista_render_passes.component import _ssao_depth_format
from tests.backend import vtkCameraPass, vtkOpenGLRenderPass, vtkTextureObject
from tests.conftest import vtk_stderr


def _make_plotter(**kwargs) -> pv.Plotter:
    defaults = {'off_screen': True, 'window_size': (400, 300)}
    defaults.update(kwargs)
    pl = pv.Plotter(**defaults)
    pl.add_mesh(pv.Sphere())
    pl.set_background('white')
    return pl


# -- registration -------------------------------------------------------------


def test_component_is_reachable_on_any_plotter():
    pl = _make_plotter()
    assert isinstance(pl.render_passes, prp.RenderPasses)
    assert isinstance(pl.render_passes.active, RenderPassComponent)
    assert pl.render_passes is pl.render_passes
    assert pl.render_passes.plotter is pl
    assert pl.render_passes.renderer is pl.renderer


def test_render_passes_follows_the_active_subplot():
    pl = pv.Plotter(off_screen=True, shape=(1, 2), window_size=(320, 120))
    for column in range(2):
        pl.subplot(0, column)
        pl.add_mesh(pv.Sphere(radius=0.8), color='white')
        pl.set_background('black')
    pl.subplot(0, 0)
    left = pl.render_passes.enable_edl()
    pl.subplot(0, 1)
    right = pl.render_passes.enable_ssao()
    assert left is not right
    assert left.renderer is pl.renderers[0]
    assert right.renderer is pl.renderers[1]
    assert [c.describe() for c in pl.render_passes.components] == [
        'EDL(annotations bypassed)',
        'SSAO(r=0.5, derived)',
    ]
    img = pl.screenshot(return_img=True)
    assert pl.renderers[0].GetPass() is left.chain.GetTopPass()
    assert pl.renderers[1].GetPass() is right.chain.GetTopPass()
    lit = [(img[:, :160].max(axis=2) > 12).sum(), (img[:, 160:].max(axis=2) > 12).sum()]
    assert min(lit) > 1000, lit
    pl.close()
    assert not left.is_active
    assert not right.is_active


def test_entry_point_names_the_component_module():
    from importlib.metadata import entry_points

    eps = entry_points(group='pyvista.plotter_components', name='render_passes')
    assert [ep.value for ep in eps] == ['pyvista_render_passes.component']


# -- state --------------------------------------------------------------------


def test_default_state_matches_a_fresh_component():
    rpm = _make_plotter().render_passes
    assert RenderPassComponent.default_state() == rpm.get_state()
    assert not any(rpm.get_state()[key] for key in ('depth_peeling', 'edl', 'ssao', 'dof'))
    assert rpm.get_state()['annotation_bypass'] is True


def test_msaa_disabled_on_init_even_if_the_window_asked_for_it():
    pl = pv.Plotter(off_screen=True, window_size=(400, 300))
    pl.add_mesh(pv.Sphere(), opacity=0.5)
    pl.render_window.SetMultiSamples(8)
    rpm = pl.render_passes
    assert pl.render_window.GetMultiSamples() == 0
    pl.render_window.SetMultiSamples(8)
    rpm.enable_depth_peeling().apply()
    assert pl.render_window.GetMultiSamples() == 0
    rpm.enable_ssao().apply()
    assert pl.render_window.GetMultiSamples() == 0


def test_enable_depth_peeling_records_parameters():
    rpm = _make_plotter().render_passes
    rpm.enable_depth_peeling(max_peels=16, occlusion_ratio=0.05)
    state = rpm.get_state()
    assert state['depth_peeling'] is True
    assert state['depth_peeling_max_peels'] == 16
    assert state['depth_peeling_occlusion_ratio'] == 0.05
    rpm.disable_depth_peeling()
    assert rpm.get_state()['depth_peeling'] is False


def test_enable_ssao_records_parameters():
    rpm = _make_plotter().render_passes
    rpm.enable_ssao(radius=1.0, bias=0.01, kernel_size=128, blur=False)
    state = rpm.get_state()
    assert state['ssao'] is True
    assert state['ssao_radius'] == 1.0
    assert state['ssao_bias'] == 0.01
    assert state['ssao_kernel_size'] == 128
    assert state['ssao_blur'] is False


def test_ssao_and_dof_are_mutually_exclusive():
    rpm = _make_plotter().render_passes
    rpm.enable_ssao()
    with pytest.raises(ValueError, match='incompatible'):
        rpm.enable_dof()
    rpm.disable_ssao().enable_dof()
    with pytest.raises(ValueError, match='incompatible'):
        rpm.enable_ssao()


def test_presets():
    rpm = _make_plotter().render_passes
    rpm.enable_msaa().enable_edl()
    state = rpm.preset_interactive().get_state()
    assert all(state[k] for k in ('depth_peeling', 'anti_aliasing'))
    assert not any(state[k] for k in ('edl', 'ssao', 'dof', 'shadows', 'blur', 'msaa'))

    state = rpm.preset_photo_real().get_state()
    assert all(state[k] for k in ('depth_peeling', 'ssao', 'anti_aliasing', 'shadows'))
    assert state['dof'] is False

    rpm = _make_plotter().render_passes
    state = rpm.preset_still().get_state()
    assert all(state[k] for k in ('depth_peeling', 'anti_aliasing'))


def test_describe():
    rpm = _make_plotter().render_passes
    assert rpm.describe() == '(default pipeline)'
    rpm.enable_depth_peeling().enable_edl().enable_ssao().enable_anti_aliasing()
    desc = rpm.describe()
    for name in ('DepthPeeling', 'EDL', 'SSAO', 'AntiAliasing'):
        assert name in desc


def test_get_state_round_trips_through_set_state():
    rpm = _make_plotter().render_passes
    rpm.enable_depth_peeling(max_peels=12).enable_edl().enable_ssao(radius=0.8)
    rpm.enable_anti_aliasing(factor=1.5).enable_msaa(4)
    original = rpm.get_state()

    rpm2 = _make_plotter().render_passes
    rpm2.set_state(original)
    assert rpm2.get_state() == original


def test_set_state_ignores_unknown_keys_and_keeps_missing_ones():
    rpm = _make_plotter().render_passes
    rpm.enable_edl()
    rpm.set_state({'unknown_key': 42, 'depth_peeling': True})
    assert rpm.get_state()['depth_peeling'] is True
    assert rpm.get_state()['edl'] is True


def test_set_state_refuses_ssao_with_dof():
    rpm = _make_plotter().render_passes
    with pytest.raises(ValueError, match='incompatible'):
        rpm.set_state({'ssao': True, 'dof': True})


def test_fluent_api():
    rpm = _make_plotter().render_passes.active
    assert rpm.enable_depth_peeling().enable_edl().enable_anti_aliasing().disable_edl() is rpm


# -- apply ----------------------------------------------------------------------


def test_apply_picks_builtin_depth_peeling_when_no_other_pass_is_active():
    pl = _make_plotter()
    rpm = pl.render_passes
    rpm.enable_depth_peeling().apply()
    assert pl.renderer.GetUseDepthPeeling() == 1
    assert pl.renderer.GetPass() is None
    assert rpm.chain.GetDepthPeelingPass() is None
    assert not rpm.is_active


def test_apply_picks_manual_depth_peeling_when_other_passes_are_active():
    pl = _make_plotter()
    rpm = pl.render_passes
    rpm.enable_depth_peeling().enable_ssao().apply()
    assert pl.renderer.GetUseDepthPeeling() == 0
    assert rpm.chain.GetDepthPeelingPass() is not None
    assert pl.renderer.GetPass() is rpm.chain.GetTopPass()


def test_apply_installs_the_pass_the_chain_built_and_is_idempotent():
    pl = _make_plotter()
    rpm = pl.render_passes
    rpm.enable_ssao().enable_edl().enable_anti_aliasing()
    rpm.apply()
    rpm.apply()
    assert pl.renderer.GetPass() is rpm.chain.GetTopPass()
    assert rpm.is_active
    assert isinstance(rpm.chain.GetScenePass(), vtkOpenGLRenderPass)
    assert isinstance(rpm.chain.GetSsaaPass(), prp.pvSSAAVolumePass)


def test_ssao_delegates_to_edl():
    rpm = _make_plotter().render_passes
    rpm.enable_ssao().enable_edl().disable_annotation_bypass().apply()
    below_edl = rpm.chain.GetEDLPass().GetDelegatePass()
    assert below_edl.GetPasses().GetItemAsObject(0).GetDelegatePass() is rpm.chain.GetSsaoPass()


def test_settings_reach_the_chain():
    rpm = _make_plotter().render_passes
    rpm.enable_ssao(radius=0.25, bias=0.002, kernel_size=64, blur=False)
    rpm.enable_anti_aliasing(factor=3.0)
    rpm.apply()
    ssao = rpm.chain.GetSsaoPass()
    assert ssao.GetRadius() == pytest.approx(0.25)
    assert ssao.GetBias() == pytest.approx(0.002)
    assert ssao.GetKernelSize() == 64
    assert not ssao.GetBlur()
    assert rpm.chain.GetSsaaPass().GetSupersampleFactor() == pytest.approx(3.0)


def test_disable_anti_aliasing_removes_the_pass():
    pl = _make_plotter()
    rpm = pl.render_passes
    rpm.enable_anti_aliasing().apply()
    assert rpm.chain.GetSsaaPass() is not None
    rpm.disable_anti_aliasing().apply()
    assert rpm.chain.GetSsaaPass() is None
    assert pl.renderer.GetPass() is None


def test_a_plain_chain_delegates_straight_to_the_camera_pass():
    pl = _make_plotter()
    pl.render_passes.enable_anti_aliasing().apply()
    assert isinstance(pl.render_passes.chain.GetScenePass().GetDelegatePass(), vtkCameraPass)


def test_msaa_is_a_window_property_set_on_apply(caplog):
    pl = _make_plotter()
    rpm = pl.render_passes
    rpm.enable_msaa(samples=4).apply()
    assert pl.render_window.GetMultiSamples() == 4
    rpm.disable_msaa().apply()
    assert pl.render_window.GetMultiSamples() == 0

    rpm.enable_msaa(samples=8).enable_anti_aliasing()
    with caplog.at_level(logging.WARNING):
        rpm.apply()
    assert 'MSAA has no effect' in caplog.text

    caplog.clear()
    rpm.disable_anti_aliasing().enable_depth_peeling()
    with caplog.at_level(logging.WARNING):
        rpm.apply()
    assert 'MSAA + depth peeling' in caplog.text


def test_hidden_line_removal_is_a_renderer_property(caplog):
    pl = _make_plotter()
    rpm = pl.render_passes
    rpm.enable_hidden_line_removal().apply()
    assert pl.renderer.GetUseHiddenLineRemoval()
    rpm.disable_hidden_line_removal().apply()
    assert not pl.renderer.GetUseHiddenLineRemoval()


def test_shadows_under_edl_warn_about_the_annotation_stage(caplog):
    rpm = _make_plotter().render_passes
    rpm.enable_edl().enable_shadows()
    with caplog.at_level(logging.WARNING):
        rpm.apply()
    assert 'Shadows are not applied to annotation props' in caplog.text


def test_ssao_over_an_unshaded_volume_logs_no_error():
    # vtkSSAOPass inspects every volume in its prop array and logs an error for
    # each unshaded one; the chain keeps volumes out of that array.
    with vtk_stderr() as log:
        pl = pv.Plotter(off_screen=True, window_size=(200, 200))
        pl.add_mesh(pv.Cube(), color='tan')
        pl.add_volume(pv.Wavelet(), opacity='sigmoid', show_scalar_bar=False)
        pl.render_passes.enable_ssao().enable_depth_peeling().enable_anti_aliasing()
        img = np.asarray(pl.screenshot(return_img=True))[..., :3]
        pl.render_window.Render()
        pl.close()
    assert 'Shading must be enabled' not in log['text'], log['text']
    assert 'ERR|' not in log['text'], log['text']
    assert (img.max(axis=2) < 250).sum() > 2000


def _label_pixels(pl: pv.Plotter) -> int:
    pl.render_window.Render()
    img = np.asarray(pl.screenshot(return_img=True))[..., :3].astype(int)
    # The filled label boxes are mid grey; the wireframe is thin and dark.
    grey = (np.abs(img[..., 0] - img[..., 1]) < 8) & (img[..., 0] > 90) & (img[..., 0] < 160)
    return int(grey.sum())


@pytest.mark.parametrize(
    'enable', ['anti_aliasing', 'edl', 'ssao', 'blur', 'dof', 'depth_peeling', 'shadows']
)
def test_point_labels_survive_every_pass(enable):
    # Labels culled through the window depth hide every other frame inside a
    # pass's framebuffer (pyvista #4831); docs/design.md "overlay stage".
    cube = pv.Cube()

    def scene() -> pv.Plotter:
        pl = pv.Plotter(off_screen=True, window_size=(300, 300))
        pl.add_mesh(cube, style='wireframe')
        pl.add_point_labels(
            cube.cell_centers().points, list(range(cube.n_cells)), show_points=False
        )
        return pl

    pl = scene()
    plain = _label_pixels(pl)
    pl.close()
    assert plain > 500, plain

    pl = scene()
    getattr(pl.render_passes, f'enable_{enable}')()
    frames = [_label_pixels(pl) for _ in range(4)]
    pl.close()
    assert min(frames) > 0.5 * plain, (plain, frames)
    assert len(set(frames)) == 1, frames


# -- the SSAA factor --------------------------------------------------------------


def test_set_ssaa_factor_writes_through_to_the_live_pass_without_a_rebuild():
    rpm = _make_plotter().render_passes.active
    rpm.enable_anti_aliasing().apply()
    ssaa = rpm.chain.GetSsaaPass()
    assert rpm.set_ssaa_factor(1.5) is rpm
    assert rpm.chain.GetSsaaPass() is ssaa
    assert ssaa.GetSupersampleFactor() == pytest.approx(1.5)
    assert ssaa.GetPrimitiveScaleFactor() == pytest.approx(1.5)
    assert rpm._dirty is False


@pytest.mark.parametrize(('requested', 'expected'), [(10.0, 4.0), (0.2, 1.0), (2.0, 2.0)])
def test_set_ssaa_factor_clamps(requested, expected):
    rpm = _make_plotter().render_passes
    rpm.enable_anti_aliasing().apply()
    rpm.set_ssaa_factor(requested)
    ssaa = rpm.chain.GetSsaaPass()
    assert ssaa.GetSupersampleFactor() == pytest.approx(expected)
    assert ssaa.GetPrimitiveScaleFactor() == pytest.approx(expected)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_set_ssaa_factor_ignores_nonfinite(bad):
    rpm = _make_plotter().render_passes
    rpm.enable_anti_aliasing().apply()
    rpm.set_ssaa_factor(2.0)
    rpm.set_ssaa_factor(bad)
    assert rpm.chain.GetSsaaPass().GetSupersampleFactor() == pytest.approx(2.0)


def test_ssaa_factor_set_before_the_build_is_honored():
    rpm = _make_plotter().render_passes
    rpm.set_ssaa_factor(1.75)
    rpm.enable_anti_aliasing().apply()
    assert rpm.chain.GetSsaaPass().GetSupersampleFactor() == pytest.approx(1.75)
    assert rpm.get_state()['ssaa_factor'] == pytest.approx(1.75)


def test_default_ssaa_factor():
    rpm = _make_plotter().render_passes
    rpm.enable_anti_aliasing().apply()
    assert rpm.chain.GetSsaaPass().GetSupersampleFactor() == pytest.approx(DEFAULT_SSAA_FACTOR)


# -- the SSAO radius policy ------------------------------------------------------


def test_ssao_radius_defaults_to_derived():
    assert RenderPassComponent.default_state()['ssao_radius'] is None
    assert RenderPassComponent.default_state()['ssao_bias'] is None


def test_derived_radius_tracks_the_scene_scale():
    radii = []
    for scale in (1.0, 100.0):
        pl = pv.Plotter(off_screen=True)
        pl.add_mesh(pv.Cube().scale(scale))
        pl.render_passes.enable_ssao().apply()
        radii.append(pl.render_passes.chain.GetSsaoPass().GetRadius())
    assert radii[1] == pytest.approx(100.0 * radii[0], rel=1e-6)


def test_an_explicit_radius_is_never_re_derived():
    pl = pv.Plotter(off_screen=True)
    pl.add_mesh(pv.Cube().scale(100.0))
    pl.render_passes.enable_ssao(radius=0.5).apply()
    assert pl.render_passes.chain.GetSsaoPass().GetRadius() == pytest.approx(0.5)
    pl.add_mesh(pv.Cube().scale(1000.0))
    assert not pl.render_passes.resync_derived_ssao()


def test_ssao_radius_round_trips_as_none():
    pl = pv.Plotter(off_screen=True)
    pl.add_mesh(pv.Cube())
    rpm = pl.render_passes
    rpm.enable_ssao().apply()
    state = rpm.get_state()
    assert state['ssao_radius'] is None
    assert state['ssao_bias'] is None
    rpm.set_state(state)
    rpm.apply()
    assert rpm.chain.GetSsaoPass().GetRadius() == pytest.approx(0.03 * np.sqrt(3.0), rel=1e-6)


def test_describe_reports_the_radius_in_force():
    pl = pv.Plotter(off_screen=True)
    pl.add_mesh(pv.Cube())
    rpm = pl.render_passes
    rpm.enable_ssao().apply()
    assert 'derived' in rpm.describe()
    rpm.enable_ssao(radius=0.5).apply()
    assert 'SSAO(r=0.5)' in rpm.describe()


@pytest.mark.parametrize('radius', [0.0, -1.0, float('nan'), float('inf')])
def test_enable_ssao_refuses_an_unusable_radius(radius):
    rpm = _make_plotter().render_passes
    with pytest.raises(ValueError, match='finite and positive'):
        rpm.enable_ssao(radius=radius)
    assert rpm.get_state()['ssao_radius'] is None
    assert not rpm.get_state()['ssao']


@pytest.mark.parametrize('bias', [-0.1, float('nan'), float('inf')])
def test_enable_ssao_refuses_an_unusable_bias(bias):
    with pytest.raises(ValueError, match='finite and non-negative'):
        _make_plotter().render_passes.enable_ssao(bias=bias)


def test_enable_ssao_accepts_a_zero_bias():
    rpm = _make_plotter().render_passes
    rpm.enable_ssao(radius=0.5, bias=0.0).apply()
    assert rpm.chain.GetSsaoPass().GetBias() == pytest.approx(0.0)


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        ('ssao_radius', 0.0),
        ('ssao_radius', -3.0),
        ('ssao_radius', float('nan')),
        ('ssao_bias', -1.0),
        ('ssao_bias', float('inf')),
        ('ssao_radius', 'not a number'),
    ],
)
def test_set_state_drops_an_unusable_number_instead_of_raising(key, value, caplog):
    pl = pv.Plotter(off_screen=True)
    pl.add_mesh(pv.Cube())
    state = RenderPassComponent.default_state() | {'ssao': True, key: value}
    with caplog.at_level(logging.WARNING):
        pl.render_passes.set_state(state)
    assert pl.render_passes.get_state()[key] is None
    assert key in caplog.text
    pl.render_passes.apply()
    assert pl.render_passes.chain.GetSsaoPass().GetRadius() == pytest.approx(
        0.03 * np.sqrt(3.0), rel=1e-6
    )


def test_resync_derived_ssao_picks_up_a_dataset_added_after_the_build():
    pl = pv.Plotter(off_screen=True)
    rpm = pl.render_passes
    rpm.enable_ssao().apply()
    empty_radius = rpm.chain.GetSsaoRadius()
    pl.add_mesh(pv.Cube().scale(100.0))
    assert rpm.resync_derived_ssao()
    assert rpm.chain.GetSsaoRadius() != pytest.approx(empty_radius)
    assert rpm.chain.GetSsaoRadius() == pytest.approx(0.03 * 100.0 * np.sqrt(3.0), rel=1e-6)
    assert not rpm.resync_derived_ssao()


# -- the depth-format probe -------------------------------------------------------


def test_ssao_depth_format_by_renderer_kind():
    assert _ssao_depth_format(is_software_renderer=True) is None
    assert _ssao_depth_format(is_software_renderer=False) == vtkTextureObject.Fixed32


def test_apply_wires_the_depth_format_onto_the_pass():
    rpm = _make_plotter().render_passes
    rpm.enable_ssao().apply()
    expected = _ssao_depth_format()
    assert rpm.chain.GetSsaoPass().GetDepthFormat() == (
        vtkTextureObject.Float32 if expected is None else expected
    )


def test_the_depth_format_probe_is_skipped_while_ssao_is_off(monkeypatch):
    calls = []
    monkeypatch.setattr(
        'pyvista_render_passes.component._ssao_depth_format',
        lambda **kwargs: calls.append(kwargs) or None,
    )
    rpm = _make_plotter().render_passes
    rpm.enable_edl().enable_anti_aliasing().apply()
    assert calls == []
    rpm.enable_ssao().apply()
    assert len(calls) == 1


# -- auto-apply --------------------------------------------------------------------


def test_fresh_component_is_not_dirty():
    rpm = _make_plotter().render_passes
    assert rpm._dirty is False
    assert rpm._auto_apply is True
    assert rpm._start_event_tag is not None


def test_every_setter_path_marks_dirty_and_apply_clears_it():
    rpm = _make_plotter().render_passes
    mutations = (
        rpm.enable_ssao,
        rpm.disable_ssao,
        rpm.enable_edl,
        rpm.disable_edl,
        rpm.enable_depth_peeling,
        rpm.enable_anti_aliasing,
        rpm.enable_msaa,
        rpm.preset_photo_real,
        rpm.invalidate,
        lambda: rpm.set_state({'blur': True}),
    )
    for mutate in mutations:
        rpm.apply()
        assert rpm._dirty is False
        mutate()
        assert rpm._dirty is True, mutate


def test_toggling_auto_apply_does_not_dirty():
    rpm = _make_plotter().render_passes
    rpm.apply()
    rpm.disable_auto_apply()
    rpm.enable_auto_apply()
    assert rpm._dirty is False


def test_auto_apply_installs_the_chain_on_render():
    pl = _make_plotter()
    rpm = pl.render_passes
    rpm.enable_anti_aliasing()
    assert pl.renderer.GetPass() is None
    pl.show(auto_close=False)
    assert rpm._dirty is False
    assert pl.renderer.GetPass() is rpm.chain.GetTopPass()


def test_auto_apply_reapplies_after_show_then_a_later_change():
    pl = _make_plotter()
    rpm = pl.render_passes
    pl.show(auto_close=False)
    assert pl.renderer.GetPass() is None
    rpm.enable_ssao()
    pl.render()
    assert rpm._dirty is False
    assert rpm.chain.GetSsaoPass() is not None


def test_auto_apply_matches_explicit_apply_pixels():
    pl_auto = _make_plotter()
    pl_auto.render_passes.enable_anti_aliasing()
    img_auto = pl_auto.screenshot(return_img=True)

    pl_explicit = _make_plotter()
    pl_explicit.render_passes.enable_anti_aliasing().apply()
    img_explicit = pl_explicit.screenshot(return_img=True)
    np.testing.assert_array_equal(img_auto, img_explicit)


def test_disable_auto_apply_leaves_the_change_queued():
    pl = _make_plotter()
    rpm = pl.render_passes
    rpm.disable_auto_apply().enable_anti_aliasing()
    pl.show(auto_close=False)
    assert rpm._dirty is True
    assert pl.renderer.GetPass() is None
    rpm.enable_auto_apply()
    pl.render()
    assert rpm._dirty is False
    assert pl.renderer.GetPass() is not None


def test_auto_apply_swallows_errors_warns_once_and_rearms(monkeypatch):
    pl = _make_plotter()
    rpm = pl.render_passes.active
    calls = {'n': 0}

    def boom(*_args, **_kwargs):
        calls['n'] += 1
        msg = 'kaboom'
        raise RuntimeError(msg)

    monkeypatch.setattr(rpm, 'apply', boom)
    rpm.enable_anti_aliasing()
    with pytest.warns(RuntimeWarning, match='auto-apply failed'):
        pl.show(auto_close=False)
    assert calls['n'] == 1
    assert rpm._auto_apply_failed is True

    with warnings.catch_warnings():
        warnings.simplefilter('error')
        pl.render()
    assert calls['n'] == 1

    rpm.enable_ssao()
    assert rpm._auto_apply_failed is False
    assert rpm._dirty is True


def test_annotation_props_are_tagged_on_render_under_edl():
    pl = _make_plotter()
    pl.render_passes.enable_edl()
    pl.show_bounds()
    axes = [p for p in pl.renderer.GetViewProps() if isinstance(p, prp.component.vtkCubeAxesActor)]
    assert axes
    assert not prp.prop_filter_tag_is_set(axes[0])
    pl.show(auto_close=False)
    assert prp.has_prop_filter_tag(axes[0])


def test_tag_scene_annotations_respects_an_explicit_untag():
    pl = _make_plotter()
    pl.show_bounds()
    tagged = prp.tag_scene_annotations(pl)
    assert len(tagged) == 1
    prp.set_prop_filter_tag(tagged[0], tagged=False)
    assert prp.tag_scene_annotations(pl.renderer) == []
    assert not prp.has_prop_filter_tag(tagged[0])


# -- ownership and teardown -----------------------------------------------------------


def test_is_active_tracks_the_installed_chain():
    pl = _make_plotter()
    rpm = pl.render_passes
    assert rpm.is_active is False
    rpm.enable_anti_aliasing().apply()
    assert rpm.is_active is True
    pl.renderer.SetPass(None)
    assert rpm.is_active is False
    rpm.disable_anti_aliasing().apply()
    assert rpm.is_active is False


def test_invalidate_queues_a_rebuild_without_changing_a_setting():
    rpm = _make_plotter().render_passes.active
    rpm.enable_anti_aliasing().apply()
    state = rpm.get_state()
    assert rpm.invalidate() is rpm
    assert rpm._dirty is True
    assert rpm.get_state() == state


def test_close_removes_the_observer_and_drops_the_chain():
    pl = _make_plotter()
    rpm = pl.render_passes
    rpm.enable_ssao().enable_edl().enable_anti_aliasing()
    pl.screenshot(return_img=True)
    assert rpm.chain.GetTopPass() is not None
    pl.close()
    assert rpm._start_event_tag is None
    assert rpm.chain.GetTopPass() is None
    assert not rpm.is_active


def test_deep_clean_resets_state_and_keeps_the_component_usable():
    pl = _make_plotter()
    rpm = pl.render_passes
    rpm.enable_ssao().enable_edl().enable_anti_aliasing()
    pl.show(auto_close=False)
    assert rpm.chain.GetTopPass() is not None

    pl.deep_clean()
    assert rpm.chain.GetTopPass() is None
    assert rpm.chain.GetSceneBasePass() is None
    assert rpm.get_state() == RenderPassComponent.default_state()
    assert rpm._start_event_tag is not None
    assert rpm._dirty is False

    rpm.enable_edl()
    pl.render()
    assert rpm.chain.GetEDLPass() is not None


def test_deep_clean_releases_the_built_chain():
    # vtkEDLShading's destructor complains if it still holds its FBO.
    with vtk_stderr() as log:
        pl = _make_plotter()
        rpm = pl.render_passes
        rpm.enable_edl()
        pl.show(auto_close=False)
        pl.deep_clean()
        assert pl.renderer.GetPass() is None
        rpm.enable_edl()
        pl.render()
        pl.close()
        del pl, rpm
        gc.collect()
    assert 'should have been deleted' not in log['text'], log['text']


def test_rebuilding_the_chain_releases_the_shadow_framebuffer():
    # vtkShadowMapBakerPass's destructor complains if it still holds an FBO;
    # every other pass leaks in silence, so this one is the canary.
    with vtk_stderr() as log:
        pl = _make_plotter()
        rpm = pl.render_passes
        rpm.enable_shadows()
        pl.screenshot(return_img=True)
        for _ in range(3):
            rpm.invalidate()
            pl.render()
        pl.close()
    assert 'should have been deleted' not in log['text'], log['text']
