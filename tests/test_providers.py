"""Pass providers: the registry and how the component composes them."""

from __future__ import annotations

import gc
import weakref

import pytest
import pyvista as pv

import pyvista_render_passes as prp
from tests.backend import vtkCameraPass, vtkEDLShading, vtkGaussianBlurPass
from tests.conftest import vtk_stderr


class _Provider:
    def __init__(self, stage: str, factory=None) -> None:
        self.stage = stage
        self.factory = factory
        self.calls: list[object] = []

    def build_pass(self, renderer, chain, delegate):
        self.calls.append(delegate)
        if self.factory is None:
            return None
        pass_ = self.factory()
        if delegate is not None:
            pass_.SetDelegatePass(delegate)
        return pass_


def _plotter() -> pv.Plotter:
    pl = pv.Plotter(off_screen=True, window_size=(200, 150))
    pl.add_mesh(pv.Sphere(), opacity=0.5)
    return pl


# -- registry ------------------------------------------------------------------


def test_protocol_is_structural():
    assert isinstance(_Provider('base'), prp.PassProvider)
    assert not isinstance(object(), prp.PassProvider)


def test_register_is_idempotent_and_ordered():
    pl = _plotter()
    first, second = _Provider('base'), _Provider('base')
    prp.register_pass_provider(pl, second)
    prp.register_pass_provider(pl, first)
    prp.register_pass_provider(pl, second)
    assert prp.pass_providers(pl) == (second, first)


@pytest.mark.parametrize('stage', ['translucent', 'post'])
def test_single_pass_stages_refuse_a_second_provider(stage):
    pl = _plotter()
    first = _Provider(stage)
    prp.register_pass_provider(pl, first)
    with pytest.raises(ValueError, match=f'{stage!r} provider is already registered'):
        prp.register_pass_provider(pl, _Provider(stage))


def test_unregister_is_a_no_op_when_absent():
    pl = _plotter()
    provider = _Provider('base')
    prp.unregister_pass_provider(pl, provider)
    prp.register_pass_provider(pl, provider)
    prp.unregister_pass_provider(pl, provider)
    prp.unregister_pass_provider(pl, provider)
    assert prp.pass_providers(pl) == ()


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


def test_register_returns_what_it_was_given():
    pl = _plotter()
    provider = _Provider('base')
    assert prp.register_pass_provider(pl, provider) is provider


def test_class_decorator_registers_an_instance():
    pl = _plotter()

    @prp.register_pass_provider(pl)
    class Provider:
        stage = 'post'

        def build_pass(self, renderer, chain, delegate):
            return vtkGaussianBlurPass()

    (registered,) = prp.pass_providers(pl)
    assert isinstance(registered, Provider)
    pl.screenshot(return_img=True)
    assert pl.render_passes.chain.GetScenePass() is pl.render_passes.chain.GetPostPass()
    prp.unregister_pass_provider(pl, Provider)
    assert prp.pass_providers(pl) == ()


def test_function_decorator_needs_a_stage():
    pl = _plotter()
    calls = []

    @prp.register_pass_provider(pl, stage='base')
    def wrap(renderer, chain, delegate):
        calls.append(delegate)

    assert wrap.__name__ == 'wrap'
    (registered,) = prp.pass_providers(pl)
    assert registered.stage == 'base'
    pl.screenshot(return_img=True)
    assert len(calls) == 1
    prp.unregister_pass_provider(pl, wrap)
    assert prp.pass_providers(pl) == ()

    with pytest.raises(TypeError, match='stage is required'):
        prp.register_pass_provider(pl, lambda r, c, d: None)
    with pytest.raises(TypeError, match='stage applies only'):
        prp.register_pass_provider(pl, _Provider('base'), stage='base')
    with pytest.raises(TypeError, match='not a provider'):
        prp.register_pass_provider(pl, 3)


def test_registry_is_per_plotter():
    a, b = _plotter(), _plotter()
    provider = _Provider('translucent')
    prp.register_pass_provider(a, provider)
    assert prp.pass_providers(b) == ()


# -- composition ---------------------------------------------------------------


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
    provider = _Provider('base', vtkEDLShading)
    prp.register_pass_provider(pl, provider)
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


def test_post_provider_sits_below_ssaa():
    pl = _plotter()
    provider = _Provider('post', vtkGaussianBlurPass)
    prp.register_pass_provider(pl, provider)
    rpm = pl.render_passes
    rpm.enable_anti_aliasing().apply()
    top = rpm.chain.GetScenePass()
    assert top is rpm.chain.GetSsaaPass()
    assert top.GetDelegatePass() is rpm.chain.GetPostPass()


def test_a_provider_returning_nothing_contributes_nothing():
    pl = _plotter()
    silent = [_Provider(stage) for stage in ('base', 'translucent', 'post')]
    for provider in silent:
        prp.register_pass_provider(pl, provider)
    rpm = pl.render_passes
    rpm.apply()
    assert rpm.is_active  # a base provider still claims the chain
    assert rpm.chain.GetTranslucentPass() is None
    assert rpm.chain.GetPostPass() is None
    assert rpm.chain.GetSceneBasePass() is rpm.chain.GetScenePass()


def test_unregistering_takes_effect_on_the_next_apply():
    pl = _plotter()
    provider = _Provider('post', vtkGaussianBlurPass)
    prp.register_pass_provider(pl, provider)
    rpm = pl.render_passes
    rpm.enable_anti_aliasing().apply()
    prp.unregister_pass_provider(pl, provider)
    rpm.apply()
    assert rpm.chain.GetPostPass() is None


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


def test_provided_passes_are_released_with_the_chain():
    # vtkEDLShading's destructor complains if it still holds its FBO.
    with vtk_stderr() as log:
        pl = _plotter()
        provider = _Provider('base', vtkEDLShading)
        prp.register_pass_provider(pl, provider)
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
