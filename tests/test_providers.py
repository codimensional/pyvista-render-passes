"""Pass providers: discovery, registration, state, vetoes and composition."""

from __future__ import annotations

import gc
import importlib
from importlib.metadata import EntryPoint
import inspect
import itertools
import json
import logging
import re
import sys
from typing import get_args
import warnings
import weakref

import numpy as np
import pytest
import pyvista as pv

import pyvista_render_passes as prp
from pyvista_render_passes import RenderPassComponent, providers as providers_module
from tests.backend import vtkCameraPass, vtkEDLShading, vtkFramebufferPass, vtkGaussianBlurPass
from tests.conftest import vtk_stderr

_names = itertools.count()
_CAMERA = [(2.4, 1.7, 3.1), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)]


class _Provider(prp.BasePassProvider):
    def __init__(self, stage: str, factory=None) -> None:
        self.name = f'{stage}_{next(_names)}'
        self.stages = (stage,)
        self.factory = factory
        self.calls: list[object] = []
        self.built: list[object] = []

    def build_pass(self, stage, renderer, chain, delegate):
        self.calls.append(delegate)
        if self.factory is None:
            return None
        pass_ = self.factory()
        if delegate is not None:
            pass_.SetDelegatePass(delegate)
        self.built.append(pass_)
        return pass_


class _OuterBlur(prp.BasePassProvider):
    """A colour-only outer pass: its framebuffer has no depth the window could keep."""

    name = 'outer_blur'
    stages = ('outer',)

    def build_pass(self, stage, renderer, chain, delegate):
        return vtkGaussianBlurPass()


class FakeToneMapping(prp.BasePassProvider):
    """Stands in for an installed extension: an outer pass, settings and a veto."""

    name = 'fake_tone_mapping'
    stages = ('outer',)

    def __init__(self) -> None:
        self.state = self.default_state()

    def default_state(self):
        return {'enabled': True, 'exposure': 1.0, 'preset': 'linear'}

    def get_state(self):
        return dict(self.state)

    def set_state(self, state):
        self.state |= state

    def set_enabled(self, enabled):
        self.state['enabled'] = enabled
        self.invalidate()

    def build_pass(self, stage, renderer, chain, delegate):
        return vtkGaussianBlurPass() if self.state['enabled'] else None

    def veto(self, settings):
        if self.state['enabled'] and settings['msaa']:
            return 'it reads back depth, so MSAA must stay off'
        return None


class _RaisesOnInit(prp.BasePassProvider):
    name = 'raises_on_init'

    def __init__(self) -> None:
        msg = 'extension misconfigured'
        raise RuntimeError(msg)


def _install_entry_points(monkeypatch, *pairs: tuple[str, str]) -> None:
    points = [EntryPoint(name, value, prp.ENTRY_POINT_GROUP) for name, value in pairs]

    def fake_entry_points(*, group):
        return points if group == prp.ENTRY_POINT_GROUP else []

    monkeypatch.setattr(providers_module, 'entry_points', fake_entry_points)


_FAKE = ('fake_tone_mapping', 'tests.test_providers:FakeToneMapping')


def _plotter(**kwargs) -> pv.Plotter:
    pl = pv.Plotter(window_size=(200, 150), **kwargs)
    pl.add_mesh(pv.Sphere(), opacity=0.5)
    return pl


# -- discovery -----------------------------------------------------------------


_FIXTURE_MODULE = """
from pyvista_render_passes import BasePassProvider


class FixtureProvider(BasePassProvider):
    name = 'fixture_probe'
"""


def test_discovery_reads_the_real_entry_point_group(tmp_path, monkeypatch):
    # The only test that does not monkeypatch providers.entry_points: a wrong
    # ENTRY_POINT_GROUP passes every other one.
    (tmp_path / 'prp_fixture_ext.py').write_text(_FIXTURE_MODULE)
    dist_info = tmp_path / 'prp_fixture_ext-1.0.dist-info'
    dist_info.mkdir()
    (dist_info / 'METADATA').write_text(
        'Metadata-Version: 2.1\nName: prp-fixture-ext\nVersion: 1.0\n'
    )
    (dist_info / 'entry_points.txt').write_text(
        f'[{prp.ENTRY_POINT_GROUP}]\nfixture = prp_fixture_ext:FixtureProvider\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, 'prp_fixture_ext', raising=False)
    importlib.invalidate_caches()

    provider = _plotter().render_passes.providers['fixture_probe']
    assert type(provider).__module__ == 'prp_fixture_ext'


def test_an_installed_provider_registers_itself_per_subplot(monkeypatch):
    _install_entry_points(monkeypatch, _FAKE)
    pl = _plotter(shape=(1, 2))
    rpm = pl.render_passes
    provider = rpm.providers['fake_tone_mapping']
    assert isinstance(provider, FakeToneMapping)
    pl.subplot(0, 1)
    assert pl.render_passes.providers['fake_tone_mapping'] is not provider

    pl.screenshot(return_img=True)
    assert rpm.chain.GetScenePass() is rpm.chain.GetOuterPass()
    assert isinstance(rpm.chain.GetOuterPass(), vtkGaussianBlurPass)


def test_untouched_subplots_compose_installed_providers(monkeypatch):
    _install_entry_points(monkeypatch, _FAKE)
    pl = _plotter(shape=(1, 2))
    pl.subplot(0, 1)
    pl.add_mesh(pv.Cube())
    pl.subplot(0, 0)
    _ = pl.render_passes  # touched once, from the first subplot only
    pl.screenshot(return_img=True)
    assert all(renderer.GetPass() is not None for renderer in pl.renderers)


@pytest.mark.xfail(
    strict=True,
    reason='PyVista creates a plotter component on first attribute access and offers no '
    'creation hook, so nothing of ours runs for a plotter that never touches render_passes',
)
def test_an_installed_provider_applies_without_touching_render_passes(monkeypatch):
    _install_entry_points(monkeypatch, _FAKE)
    pl = _plotter()
    pl.screenshot(return_img=True)
    assert pl.renderer.GetPass() is not None


@pytest.mark.parametrize(
    ('entry_point', 'error'),
    [
        (('missing', 'tests.no_such_module:Provider'), ModuleNotFoundError),
        (('raises', 'tests.test_providers:_RaisesOnInit'), RuntimeError),
        (('not_a_provider', 'builtins:object'), TypeError),
        (('duplicate', 'tests.test_providers:FakeToneMapping'), ValueError),
    ],
)
def test_a_broken_provider_logs_warns_at_the_caller_and_is_skipped(
    monkeypatch, caplog, entry_point, error
):
    _install_entry_points(monkeypatch, _FAKE, entry_point)
    label = f'{entry_point[0]} = {entry_point[1]}'
    expected = re.escape(f"entry point '{label}' was skipped: {error.__name__}")
    pl = _plotter()
    with caplog.at_level(logging.ERROR), pytest.warns(RuntimeWarning, match=expected) as record:
        rpm = pl.render_passes
    assert [w.filename for w in record] == [__file__]
    assert [r.levelno for r in caplog.records if label in r.getMessage()] == [logging.ERROR]
    assert list(rpm.providers) == ['fake_tone_mapping']
    assert isinstance(rpm.provider_errors[label], error)


# -- registration --------------------------------------------------------------


def test_protocol_is_structural():
    assert isinstance(_Provider('base'), prp.PassProvider)
    assert not isinstance(object(), prp.PassProvider)


def test_the_stage_tuple_and_the_stage_literal_agree():
    assert get_args(providers_module.Stage) == providers_module.STAGES
    assert set(providers_module.STAGES) > providers_module.SINGLE_PROVIDER_STAGES


def test_register_is_idempotent_and_ordered():
    pl = _plotter()
    first, second = _Provider('base'), _Provider('base')
    prp.register_pass_provider(pl, second)
    prp.register_pass_provider(pl, first)
    prp.register_pass_provider(pl, second)
    assert tuple(pl.render_passes.providers.values()) == (second, first)


@pytest.mark.parametrize('stage', sorted(providers_module.SINGLE_PROVIDER_STAGES))
def test_single_pass_stages_refuse_a_second_provider(stage):
    pl = _plotter()
    prp.register_pass_provider(pl, _Provider(stage))
    with pytest.raises(ValueError, match=f'{stage!r} provider is already registered'):
        prp.register_pass_provider(pl, _Provider(stage))


def test_registration_refuses_bad_providers():
    pl = _plotter()
    provider = _Provider('base')
    prp.register_pass_provider(pl, provider)
    twin = _Provider('base')
    twin.name = provider.name
    with pytest.raises(ValueError, match='already registered'):
        prp.register_pass_provider(pl, twin)
    with pytest.raises(ValueError, match='unknown stages'):
        prp.register_pass_provider(pl, _Provider('late'))
    with pytest.raises(TypeError, match='does not implement PassProvider'):
        prp.register_pass_provider(pl, lambda *args: None)


def test_unregister_by_object_class_or_name():
    pl = _plotter()
    provider = _Provider('base')
    prp.unregister_pass_provider(pl, provider)
    for handle in (provider, _Provider, provider.name):
        prp.register_pass_provider(pl, provider)
        prp.unregister_pass_provider(pl, handle)
        assert not pl.render_passes.providers


def test_a_provider_holding_its_plotter_is_collected_with_it():
    pl = _plotter()
    provider = _Provider('base')
    provider.plotter = pl
    prp.register_pass_provider(pl, provider)
    gone = weakref.ref(pl)
    pl.close()
    del pl, provider
    gc.collect()
    assert gone() is None


def test_class_decorator_registers_an_instance():
    pl = _plotter()

    @prp.register_pass_provider(pl)
    class Provider(prp.BasePassProvider):
        name = 'blur'
        stages = ('post',)

        def build_pass(self, stage, renderer, chain, delegate):
            return vtkGaussianBlurPass()

    assert isinstance(pl.render_passes.providers['blur'], Provider)
    pl.screenshot(return_img=True)
    assert pl.render_passes.chain.GetScenePass() is pl.render_passes.chain.GetPostPass()


def test_registry_is_per_plotter():
    a, b = _plotter(), _plotter()
    prp.register_pass_provider(a, _Provider('translucent'))
    assert not b.render_passes.providers


# -- state ---------------------------------------------------------------------


def test_provider_state_round_trips_through_a_fresh_plotter(monkeypatch):
    _install_entry_points(monkeypatch, _FAKE)
    rpm = _plotter().render_passes
    assert rpm.get_state() == rpm.default_state()
    rpm.providers['fake_tone_mapping'].set_state({'exposure': 2.5, 'preset': 'filmic'})
    rpm.enable_edl()
    state = json.loads(json.dumps(rpm.get_state()))
    assert state['providers']['fake_tone_mapping'] == {
        'enabled': True,
        'exposure': 2.5,
        'preset': 'filmic',
    }

    fresh = _plotter().render_passes
    assert fresh.get_state() != state
    fresh.set_state(state)
    assert fresh.get_state() == state


def test_state_for_an_absent_provider_is_kept_and_handed_over():
    pl = _plotter()
    rpm = pl.render_passes
    rpm.set_state({'providers': {'fake_tone_mapping': {'exposure': 3.0}}})
    assert rpm.get_state()['providers'] == {'fake_tone_mapping': {'exposure': 3.0}}
    provider = prp.register_pass_provider(pl, FakeToneMapping())
    assert provider.state['exposure'] == 3.0
    assert rpm.get_state()['providers']['fake_tone_mapping']['preset'] == 'linear'
    with pytest.raises(TypeError, match='must map provider names'):
        rpm.set_state({'providers': ['fake_tone_mapping']})


class _Picky(prp.BasePassProvider):
    name = 'picky'

    def __init__(self) -> None:
        self.state = {'level': 1}

    def get_state(self):
        return dict(self.state)

    def set_state(self, state):
        self.state |= state  # mutates before it refuses
        if not isinstance(self.state['level'], int):
            msg = 'level must be an int'
            raise TypeError(msg)


def test_a_refused_pending_state_warns_stays_pending_and_leaves_the_provider_unchanged():
    pl = _plotter()
    rpm = pl.render_passes
    rpm.set_state({'providers': {'picky': {'level': 'high'}}})
    provider = _Picky()
    with pytest.warns(RuntimeWarning, match="'picky' refused its restored state"):
        prp.register_pass_provider(pl, provider)
    assert provider.state == {'level': 1}
    assert rpm.providers['picky'] is provider
    assert rpm._pending_provider_states == {'picky': {'level': 'high'}}


def test_removing_a_provider_keeps_its_state_for_the_next_one():
    pl = _plotter()
    rpm = pl.render_passes
    provider = prp.register_pass_provider(pl, FakeToneMapping())
    rpm.set_state({'providers': {'fake_tone_mapping': {'exposure': 4.0}}})
    prp.unregister_pass_provider(pl, provider)
    assert rpm.get_state()['providers']['fake_tone_mapping']['exposure'] == 4.0
    assert prp.register_pass_provider(pl, FakeToneMapping()).state['exposure'] == 4.0


def test_a_provider_invalidates_through_its_bound_handle():
    pl = _plotter()
    provider = prp.register_pass_provider(pl, FakeToneMapping())
    rpm = pl.render_passes
    pl.screenshot(return_img=True)
    assert rpm.chain.GetOuterPass() is not None
    provider.set_enabled(False)
    pl.render()
    assert rpm.chain.GetOuterPass() is None
    prp.unregister_pass_provider(pl, provider)
    assert provider._invalidate is None


class _NanExposure(FakeToneMapping):
    name = 'nan_exposure'

    def get_state(self):
        return {**self.state, 'exposure': float('nan')}  # a new NaN, never equal


def test_rendering_does_not_rebuild_for_an_unchanged_provider():
    pl = _plotter()
    prp.register_pass_provider(pl, _NanExposure())
    rpm = pl.render_passes
    pl.screenshot(return_img=True)
    top = rpm.chain.GetTopPass()
    for _ in range(3):
        pl.render()
    assert rpm.chain.GetTopPass() is top


# -- vetoes and transactions ---------------------------------------------------


@pytest.mark.parametrize(
    'refused',
    [
        lambda rpm: rpm.enable_msaa(),
        lambda rpm: rpm.set_state({'edl': True, 'msaa': True}),
    ],
    ids=['enable_msaa', 'set_state'],
)
def test_a_vetoing_provider_refuses_msaa_and_leaves_state_unchanged(refused):
    pl = _plotter()
    rpm = pl.render_passes
    prp.register_pass_provider(pl, FakeToneMapping())
    rpm.enable_anti_aliasing(factor=2.0).apply()
    before, dirty = rpm.get_state(), rpm._dirty
    with pytest.raises(prp.SettingsVetoedError, match="'fake_tone_mapping' refused the settings"):
        refused(rpm)
    assert rpm.get_state() == before
    assert rpm._dirty is dirty
    rpm.apply()
    assert pl.render_window.GetMultiSamples() == 0


def test_a_veto_sees_provider_state_from_the_same_call():
    pl = _plotter()
    prp.register_pass_provider(pl, FakeToneMapping())
    rpm = pl.render_passes
    rpm.set_state({'msaa': True, 'providers': {'fake_tone_mapping': {'enabled': False}}})
    assert rpm.get_state()['msaa'] is True


def test_a_provider_refusing_current_settings_is_not_registered():
    pl = _plotter()
    rpm = pl.render_passes.enable_msaa()
    with pytest.raises(prp.SettingsVetoedError):
        prp.register_pass_provider(pl, FakeToneMapping())
    assert 'fake_tone_mapping' not in rpm.providers


def test_apply_refuses_a_provider_changed_directly_into_a_conflict():
    pl = _plotter()
    provider = FakeToneMapping()
    provider.state['enabled'] = False
    prp.register_pass_provider(pl, provider)
    rpm = pl.render_passes.enable_msaa()
    provider.state['enabled'] = True
    with pytest.raises(prp.SettingsVetoedError):
        rpm.apply()


# Public methods that change no setting a provider could refuse.
_NOT_SETTINGS = {
    'add_provider',
    'apply',
    'default_state',
    'describe',
    'disable_auto_apply',
    'enable_auto_apply',
    'get_state',
    'invalidate',
    'remove_provider',
    'resync_derived_ssao',
    'set_ssaa_factor',  # called every frame by a frame-time governor
}


def test_every_public_setting_method_is_a_transaction():
    public = {
        name
        for name, _ in inspect.getmembers(RenderPassComponent, inspect.isfunction)
        if not name.startswith('_')
    }
    assert public >= _NOT_SETTINGS
    wrapped = {n for n in public if getattr(getattr(RenderPassComponent, n), '__vetoable__', 0)}
    assert wrapped == public - _NOT_SETTINGS


def test_a_subclass_override_is_a_transaction_too():
    class Custom(RenderPassComponent):
        def enable_edl(self):
            self._msaa = True
            return super().enable_edl()

    pl = _plotter()
    component = Custom(pl, pl.renderer)
    try:
        component.add_provider(FakeToneMapping())
        before = component.get_state()
        with pytest.raises(prp.SettingsVetoedError):
            component.enable_edl()
        assert component.get_state() == before
    finally:
        component.__plotter_close__()


def test_a_failed_restore_is_raised_from_its_cause():
    class Brittle(FakeToneMapping):
        name = 'brittle'

        def set_state(self, state):
            if self.state['preset'] == 'odd' and state.get('preset') == 'linear':
                msg = 'cannot go back'
                raise OSError(msg)
            super().set_state(state)

    pl = _plotter()
    rpm = pl.render_passes
    provider = prp.register_pass_provider(pl, Brittle())
    with pytest.raises(RuntimeError, match=r'SettingsVetoedError.*could not be restored') as info:
        rpm.set_state({'msaa': True, 'providers': {'brittle': {'preset': 'odd'}}})
    assert isinstance(info.value.__cause__, OSError)
    provider.state['preset'] = 'linear'  # so teardown can restore the defaults
    rpm.disable_msaa()


class _Explodes(prp.BasePassProvider):
    name = 'explodes'
    stages = ('post',)

    def build_pass(self, stage, renderer, chain, delegate):
        msg = 'boom'
        raise RuntimeError(msg)


def test_auto_apply_failure_is_reported_on_every_render(caplog):
    pl = _plotter()
    prp.register_pass_provider(pl, _Explodes)

    def failures():
        return sum('auto-apply failed' in r.getMessage() for r in caplog.records)

    with caplog.at_level(logging.ERROR), warnings.catch_warnings(record=True) as record:
        warnings.simplefilter('always')
        pl.screenshot(return_img=True)
        first = failures()
        pl.render_window.Render()
        pl.render_window.Render()
    assert first >= 1
    assert failures() == first + 2
    assert all('boom' in str(w.message) for w in record)
    assert {w.filename for w in record} == {__file__}


# -- composition ---------------------------------------------------------------


def test_outer_pass_sees_the_window_while_post_sees_the_supersampled_frame():
    pl = _plotter()
    outer = _Provider('outer', vtkFramebufferPass)
    post = _Provider('post', vtkFramebufferPass)
    prp.register_pass_provider(pl, outer)
    prp.register_pass_provider(pl, post)
    pl.render_passes.enable_anti_aliasing(factor=2.0)
    pl.screenshot(return_img=True)

    def size(provider):
        texture = provider.built[-1].GetColorTexture()
        return texture.GetWidth(), texture.GetHeight()

    assert size(outer) == (200, 150)
    assert size(post) == (400, 300)


def _depth_scene(*, outer: bool, anti_aliasing: bool) -> tuple[np.ndarray, float]:
    pl = pv.Plotter(window_size=(300, 200))
    pl.add_mesh(pv.Sphere(center=(0.2, 0.1, 0.0)))
    pl.camera_position = _CAMERA
    if outer:
        prp.register_pass_provider(pl, _OuterBlur)
    if anti_aliasing:
        pl.render_passes.enable_anti_aliasing(factor=2.0)
    image = pl.screenshot(return_img=True).astype(int)
    pl.render()
    depth = pl.get_image_depth(fill_value=np.nan)
    pl.close()
    return image, float(np.isfinite(depth).mean())


@pytest.mark.parametrize('anti_aliasing', [False, True])
def test_a_colour_only_outer_pass_changes_pixels_and_keeps_window_depth(anti_aliasing):
    plain, plain_depth = _depth_scene(outer=False, anti_aliasing=anti_aliasing)
    blurred, blurred_depth = _depth_scene(outer=True, anti_aliasing=anti_aliasing)
    changed = float((np.abs(plain - blurred).max(axis=-1) > 8).mean())
    assert changed > 0.005, changed
    assert plain_depth > 0.05, plain_depth
    assert blurred_depth == pytest.approx(plain_depth, abs=0.01)


def _label_pixels(pl: pv.Plotter) -> int:
    pl.render_window.Render()
    img = np.asarray(pl.screenshot(return_img=True))[..., :3].astype(int)
    grey = (np.abs(img[..., 0] - img[..., 1]) < 8) & (img[..., 0] > 90) & (img[..., 0] < 160)
    return int(grey.sum())


def test_point_labels_are_still_culled_under_an_outer_pass():
    # Labels cull against the window depth; lost depth shows the hidden ones.
    cube = pv.Cube()

    def label_frames(*, outer: bool) -> list[int]:
        pl = pv.Plotter(window_size=(300, 300))
        # Red, so a blurred edge never reads as a grey label box.
        pl.add_mesh(cube, style='wireframe', color='red')
        pl.add_point_labels(
            cube.cell_centers().points, list(range(cube.n_cells)), show_points=False
        )
        if outer:
            prp.register_pass_provider(pl, _OuterBlur)
        frames = [_label_pixels(pl) for _ in range(4)]
        pl.close()
        return frames

    (plain, *_) = label_frames(outer=False)
    frames = label_frames(outer=True)
    assert plain > 500, plain
    assert all(abs(frame - plain) < 0.15 * plain for frame in frames), (plain, frames)


def test_base_provider_wraps_the_scene_base_below_ssao():
    pl = _plotter()
    provider = _Provider('base', vtkEDLShading)
    prp.register_pass_provider(pl, provider)
    rpm = pl.render_passes
    rpm.enable_ssao().apply()
    (delegate,) = provider.calls
    assert isinstance(delegate, vtkCameraPass)
    wrapped = rpm.chain.GetSsaoPass().GetDelegatePass()
    assert isinstance(wrapped, vtkEDLShading)
    assert wrapped.GetDelegatePass() is delegate
    assert rpm.chain.GetSceneBasePass() is wrapped


def test_base_providers_nest_in_registration_order():
    pl = _plotter()
    inner = _Provider('base', vtkEDLShading)
    outer = _Provider('base', vtkGaussianBlurPass)
    prp.register_pass_provider(pl, inner)
    prp.register_pass_provider(pl, outer)
    pl.render_passes.apply()
    top = pl.render_passes.chain.GetScenePass()
    assert isinstance(top, vtkGaussianBlurPass)
    assert isinstance(top.GetDelegatePass(), vtkEDLShading)
    assert outer.calls[0] is top.GetDelegatePass()


def test_base_provider_alone_activates_the_chain_and_manual_peeling():
    pl = _plotter()
    prp.register_pass_provider(pl, _Provider('base', vtkEDLShading))
    rpm = pl.render_passes
    rpm.enable_depth_peeling().apply()
    assert rpm.is_active
    assert pl.renderer.GetUseDepthPeeling() == 0
    assert rpm.chain.GetDepthPeelingPass() is not None


def test_translucent_provider_replaces_depth_peeling():
    pl = _plotter()
    provider = _Provider('translucent', vtkEDLShading)
    prp.register_pass_provider(pl, provider)
    rpm = pl.render_passes
    rpm.enable_depth_peeling().apply()
    assert provider.calls == [None]
    assert rpm.is_active
    assert pl.renderer.GetUseDepthPeeling() == 0
    assert rpm.chain.GetDepthPeelingPass() is None
    assert isinstance(rpm.chain.GetTranslucentPass(), vtkEDLShading)


def test_post_provider_sits_below_ssaa_and_outer_above_it():
    pl = _plotter()
    prp.register_pass_provider(pl, _Provider('post', vtkGaussianBlurPass))
    prp.register_pass_provider(pl, _Provider('outer', vtkGaussianBlurPass))
    rpm = pl.render_passes
    rpm.enable_anti_aliasing().apply()
    top = rpm.chain.GetScenePass()
    assert top is rpm.chain.GetOuterPass()
    assert top.GetDelegatePass() is rpm.chain.GetSsaaPass()
    assert rpm.chain.GetSsaaPass().GetDelegatePass() is rpm.chain.GetPostPass()


@pytest.mark.parametrize('stage', providers_module.STAGES)
def test_apply_asks_every_stage_for_a_pass_and_installs_it(stage):
    pl = _plotter()
    provider = _Provider(stage, vtkGaussianBlurPass)
    prp.register_pass_provider(pl, provider)
    rpm = pl.render_passes
    rpm.apply()
    assert len(provider.calls) == 1
    assert rpm.is_active


def test_providers_that_contribute_nothing_install_no_pass():
    pl = _plotter()
    for stage in providers_module.STAGES:
        prp.register_pass_provider(pl, _Provider(stage))
    rpm = pl.render_passes
    rpm.apply()
    assert not rpm.is_active
    assert pl.renderer.GetPass() is None
    assert not rpm.chain.GetBasePassProvided()
    rpm.enable_depth_peeling().apply()
    assert pl.renderer.GetUseDepthPeeling() == 1  # the built-in path, not a chain


def test_registration_queues_a_rebuild_on_an_existing_component():
    pl = _plotter()
    rpm = pl.render_passes
    pl.screenshot(return_img=True)
    assert not rpm.is_active
    provider = _Provider('post', vtkGaussianBlurPass)
    prp.register_pass_provider(pl, provider)
    pl.render()
    assert rpm.chain.GetScenePass() is rpm.chain.GetPostPass()
    prp.unregister_pass_provider(pl, provider)
    pl.render()
    assert not rpm.is_active


@pytest.mark.parametrize('teardown', ['close', 'deep_clean'])
def test_teardown_drops_provider_passes_from_the_seams(teardown):
    pl = _plotter()
    prp.register_pass_provider(pl, _Provider('translucent', vtkEDLShading))
    prp.register_pass_provider(pl, _Provider('post', vtkGaussianBlurPass))
    prp.register_pass_provider(pl, _Provider('outer', vtkGaussianBlurPass))
    rpm = pl.render_passes.active
    pl.screenshot(return_img=True)
    seams = ('GetTranslucentPass', 'GetPostPass', 'GetOuterPass')
    assert all(getattr(rpm.chain, seam)() is not None for seam in seams)
    getattr(pl, teardown)()
    assert all(getattr(rpm.chain, seam)() is None for seam in seams)


def test_provided_passes_are_released_with_the_chain():
    # vtkEDLShading's destructor complains if it still holds its FBO.
    with vtk_stderr() as log:
        pl = _plotter()
        prp.register_pass_provider(pl, _Provider('base', vtkEDLShading))
        pl.screenshot(return_img=True)
        assert isinstance(pl.render_passes.chain.GetScenePass(), vtkEDLShading)
        for _ in range(3):
            pl.render_passes.invalidate()
            pl.render()
        pl.deep_clean()
        pl.close()
        del pl
        gc.collect()
    assert 'should have been deleted' not in log['text'], log['text']
