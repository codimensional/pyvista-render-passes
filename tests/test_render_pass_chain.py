"""The chain builder: the graph it assembles, the SSAO radius policy, and the pixels."""

from __future__ import annotations

import numpy as np
import pytest
import pyvista as pv

import pyvista_render_passes as prp
from tests.backend import (
    vtkCameraPass,
    vtkDualDepthPeelingPass,
    vtkEDLShading,
    vtkGaussianBlurPass,
    vtkRenderPass,
    vtkRenderPassCollection,
    vtkRenderStepsPass,
    vtkSequencePass,
    vtkSSAOPass,
    vtkTextureObject,
)


def _chain(**settings: object) -> prp.pvRenderPassChain:
    chain = prp.pvRenderPassChain()
    for name, value in settings.items():
        getattr(chain, f'Set{name}')(value)
    return chain


def _delegate_lineage(top: vtkRenderPass) -> list[str]:
    """Class names outermost first, descending into a sequence's first stage."""
    names: list[str] = []
    current: vtkRenderPass | None = top
    while current is not None:
        names.append(current.GetClassName())
        if isinstance(current, vtkSequencePass):
            passes = current.GetPasses()
            current = passes.GetItemAsObject(0) if passes.GetNumberOfItems() else None
            continue
        current = current.GetDelegatePass() if hasattr(current, 'GetDelegatePass') else None
    return names


# -- the graph ---------------------------------------------------------------


def test_no_settings_needs_no_custom_chain():
    chain = prp.pvRenderPassChain()
    assert not chain.GetRequiresCustomPassChain()
    assert chain.Build(pv.Plotter().renderer) is None


def test_ssao_delegates_to_edl_not_the_reverse():
    chain = _chain(SSAO=True, EDL=True, AnnotationBypass=False)
    lineage = _delegate_lineage(chain.Build(pv.Plotter().renderer))
    assert lineage.index('vtkEDLShading') < lineage.index('vtkSSAOPass')


def test_full_chain_order():
    chain = _chain(SSAO=True, EDL=True, AnnotationBypass=True, Blur=True, AntiAliasing=True)
    lineage = _delegate_lineage(chain.Build(pv.Plotter().renderer))
    assert lineage == [
        'vtkSequencePass',
        'pvSSAAVolumePass',
        'vtkGaussianBlurPass',
        'vtkSequencePass',
        'pvPropKeyFilterPass',
        'vtkEDLShading',
        'vtkSequencePass',
        'pvPropKeyFilterPass',
        'vtkSSAOPass',
        'vtkCameraPass',
        'vtkSequencePass',
        'vtkLightsPass',
    ]


def _stage_names(base: vtkRenderPass) -> list[str]:
    passes = base.GetDelegatePass().GetPasses()
    return [passes.GetItemAsObject(i).GetClassName() for i in range(passes.GetNumberOfItems())]


def test_scene_base_lays_out_the_render_stages():
    assert _stage_names(_chain().BuildSceneBase()) == [
        'vtkLightsPass',
        'vtkOpaquePass',
        'vtkTranslucentPass',
        'vtkVolumetricPass',
    ]


def test_the_overlay_stage_runs_last_at_the_window():
    # 2D props render after every framebuffer pass has resolved, so point labels
    # test against the current frame's scene depth (see the class docs).
    chain = _chain(SSAO=True, AntiAliasing=True)
    top = chain.Build(pv.Plotter().renderer)
    stages = top.GetPasses()
    assert [stages.GetItemAsObject(i).GetClassName() for i in range(2)] == [
        'pvSSAAVolumePass',
        'vtkOverlayPass',
    ]
    assert stages.GetNumberOfItems() == 2
    assert stages.GetItemAsObject(0) is chain.GetScenePass()
    assert chain.GetScenePass() is chain.GetSsaaPass()
    assert 'vtkOverlayPass' not in _delegate_lineage(chain.GetScenePass())


def test_ssao_moves_translucent_and_volumetric_stages_after_it():
    # Inside the SSAO delegate, dual depth peeling renders translucent props
    # painted with their normals; docs/design.md "SSAO shades the opaque scene".
    chain = _chain(SSAO=True, DepthPeeling=True)
    chain.Build(pv.Plotter().renderer)
    assert _stage_names(chain.GetSceneBasePass()) == ['vtkLightsPass', 'vtkOpaquePass']
    after_ssao = chain.GetScenePass().GetPasses()
    assert after_ssao.GetNumberOfItems() == 2
    opaque_only = after_ssao.GetItemAsObject(0)
    assert opaque_only.GetDelegatePass() is chain.GetSsaoPass()
    assert opaque_only.GetMode() == prp.pvPropKeyFilterPass.RenderAll
    assert opaque_only.GetExcludeVolumes()
    assert not opaque_only.GetPreserveBuffers()
    late = after_ssao.GetItemAsObject(1)
    assert not late.GetExcludeVolumes()
    assert late.GetMode() == prp.pvPropKeyFilterPass.RenderAll
    assert late.GetPreserveBuffers()
    assert _stage_names(late.GetDelegatePass()) == ['vtkDualDepthPeelingPass', 'vtkVolumetricPass']
    assert late.GetDelegatePass().GetDelegatePass().GetPasses().GetItemAsObject(0) is (
        chain.GetDepthPeelingPass()
    )


def test_shadow_passes_replace_the_opaque_stage():
    # A vtkRenderStepsPass after the shadow pass would clear and redraw the
    # opaque geometry without shadows; the stages are laid out flat instead.
    stages = _stage_names(_chain(Shadows=True, DepthPeeling=True).BuildSceneBase())
    assert stages == [
        'vtkLightsPass',
        'vtkShadowMapBakerPass',
        'vtkShadowMapPass',
        'vtkDualDepthPeelingPass',
        'vtkVolumetricPass',
    ]


def test_annotation_split_only_appears_with_edl():
    chain = _chain(SSAO=True, AnnotationBypass=True)
    chain.Build(pv.Plotter().renderer)
    assert chain.GetScenePass().GetPasses().GetItemAsObject(0).GetDelegatePass() is (
        chain.GetSsaoPass()
    )
    assert chain.GetAnnotationSplitPass() is None
    chain = _chain(EDL=True, AnnotationBypass=True)
    chain.Build(pv.Plotter().renderer)
    assert chain.GetAnnotationSplitPass() is not None
    chain = _chain(EDL=True, AnnotationBypass=False)
    chain.Build(pv.Plotter().renderer)
    assert chain.GetAnnotationSplitPass() is None


def test_annotation_split_second_stage_preserves_buffers():
    chain = _chain(EDL=True, AnnotationBypass=True)
    chain.Build(pv.Plotter().renderer)
    stages = chain.GetAnnotationSplitPass().GetPasses()
    scene_stage = stages.GetItemAsObject(0)
    annotation_stage = stages.GetItemAsObject(1)
    assert not scene_stage.GetPreserveBuffers()
    assert annotation_stage.GetPreserveBuffers()
    assert scene_stage.GetMode() == prp.pvPropKeyFilterPass.RenderNonMatching
    assert annotation_stage.GetMode() == prp.pvPropKeyFilterPass.RenderMatching
    assert scene_stage.GetChannel() == prp.CHANNEL_ANNOTATION
    assert annotation_stage.GetChannel() == prp.CHANNEL_ANNOTATION


def test_supersampling_is_the_outermost_scene_pass():
    chain = _chain(AntiAliasing=True, SSAO=True, EDL=True, Blur=True)
    chain.Build(pv.Plotter().renderer)
    assert chain.GetScenePass().GetClassName() == 'pvSSAAVolumePass'


@pytest.mark.parametrize('flag', ['EDL', 'Blur', 'DepthOfField'])
def test_screen_space_passes_get_a_tile_framebuffer_without_anti_aliasing(flag):
    chain = _chain(**{flag: True}, SsaaFactor=3.0)
    chain.Build(pv.Plotter().renderer)
    scene = chain.GetScenePass()
    assert scene is chain.GetSsaaPass()
    assert scene.GetSupersampleFactor() == pytest.approx(1.0)
    assert scene.GetPrimitiveScaleFactor() == pytest.approx(1.0)
    plain = _chain(SSAO=True, Shadows=True)
    plain.Build(pv.Plotter().renderer)
    assert plain.GetScenePass().GetClassName() != 'pvSSAAVolumePass'


def test_ssaa_factor_reaches_both_pass_knobs():
    chain = _chain(AntiAliasing=True, SsaaFactor=3.0)
    chain.Build(pv.Plotter().renderer)
    ssaa = chain.GetSsaaPass()
    assert ssaa.GetSupersampleFactor() == pytest.approx(3.0)
    assert ssaa.GetPrimitiveScaleFactor() == pytest.approx(3.0)


def test_manual_depth_peeling_only_when_another_pass_needs_a_chain():
    alone = _chain(DepthPeeling=True)
    assert alone.GetUsesBuiltinDepthPeeling()
    assert not alone.GetUsesManualDepthPeeling()
    assert alone.Build(pv.Plotter().renderer) is None

    composed = _chain(DepthPeeling=True, SSAO=True)
    assert composed.GetUsesManualDepthPeeling()
    assert not composed.GetUsesBuiltinDepthPeeling()
    composed.Build(pv.Plotter().renderer)
    assert isinstance(composed.GetDepthPeelingPass(), vtkDualDepthPeelingPass)


def test_caller_base_pass_is_wrapped_not_replaced():
    chain = _chain(SSAO=True, EDL=True, AnnotationBypass=False, DepthPeeling=True)
    base = prp.make_split_pass(chain.BuildSceneBase(), channel=1)
    chain.Build(pv.Plotter().renderer, base)
    assert chain.GetScenePass().GetDelegatePass().GetClassName() == 'vtkEDLShading'
    assert chain.GetSsaoPass().GetDelegatePass() is base
    # The base passes the caller's BuildSceneBase created survive the Build
    # that wraps them, or they are missing from ReleaseGraphicsResources.
    assert isinstance(chain.GetDepthPeelingPass(), vtkDualDepthPeelingPass)
    assert chain.GetSceneBasePass() is base


def test_translucent_pass_replaces_the_stage_and_the_peeling():
    chain = _chain(DepthPeeling=True, TranslucentPass=vtkEDLShading())
    assert chain.GetRequiresCustomPassChain()
    assert not chain.GetUsesManualDepthPeeling()
    assert not chain.GetUsesBuiltinDepthPeeling()
    assert _stage_names(chain.BuildSceneBase()) == [
        'vtkLightsPass',
        'vtkOpaquePass',
        'vtkEDLShading',
        'vtkVolumetricPass',
    ]
    chain.Build(pv.Plotter().renderer)
    assert chain.GetDepthPeelingPass() is None


def test_post_pass_sits_between_blur_and_ssaa():
    chain = _chain(Blur=True, AntiAliasing=True, PostPass=vtkGaussianBlurPass())
    chain.Build(pv.Plotter().renderer)
    lineage = _delegate_lineage(chain.GetScenePass())
    assert lineage[:3] == ['pvSSAAVolumePass', 'vtkGaussianBlurPass', 'vtkGaussianBlurPass']
    assert chain.GetPostPass().GetDelegatePass() is chain.GetBlurPass()


def test_a_provided_base_pass_alone_needs_a_chain():
    chain = _chain(DepthPeeling=True, BasePassProvided=True)
    assert chain.GetRequiresCustomPassChain()
    assert chain.GetUsesManualDepthPeeling()
    assert not _chain(BasePassProvided=False).GetRequiresCustomPassChain()


def test_rebuild_discards_the_previous_passes():
    chain = _chain(SSAO=True, EDL=True)
    chain.Build(pv.Plotter().renderer)
    first = chain.GetSsaoPass()
    chain.Build(pv.Plotter().renderer)
    assert chain.GetSsaoPass() is not first
    chain.SSAOOff()
    chain.EDLOff()
    chain.Build(pv.Plotter().renderer)
    assert chain.GetSsaoPass() is None
    assert chain.GetTopPass() is None


def test_forget_drops_every_retained_pass():
    chain = _chain(SSAO=True, EDL=True, DepthPeeling=True, Shadows=True, AntiAliasing=True)
    chain.Build(pv.Plotter().renderer)
    assert chain.GetTopPass() is not None

    chain.Forget()
    for getter in (
        'GetTopPass',
        'GetSceneBasePass',
        'GetDepthPeelingPass',
        'GetShadowPass',
        'GetSsaoPass',
        'GetEDLPass',
        'GetSsaaPass',
    ):
        assert getattr(chain, getter)() is None, getter
    chain.Forget()


def test_release_refuses_a_null_window():
    chain = _chain(SSAO=True, EDL=True)
    chain.Build(pv.Plotter().renderer)
    ssao = chain.GetSsaoPass()
    chain.ReleaseGraphicsResources(None)
    assert chain.GetSsaoPass() is ssao


# -- the SSAO parameter policy ------------------------------------------------


def _bounded_plotter(scale: float) -> pv.Plotter:
    pl = pv.Plotter(off_screen=True)
    pl.add_mesh(pv.Cube().scale(scale))
    return pl


def test_ssao_radius_derives_from_visible_bounds():
    chain = _chain(SSAO=True)
    pl = _bounded_plotter(1.0)
    assert chain.SyncDerivedSsao(pl.renderer)
    diagonal = chain.GetSsaoBoundsDiagonal()
    assert diagonal == pytest.approx(np.sqrt(3.0), rel=1e-6)
    assert chain.GetSsaoRadius() == pytest.approx(0.03 * diagonal)
    assert chain.GetSsaoBias() == pytest.approx(chain.GetSsaoRadius() / 20.0)


def test_ssao_radius_scales_with_the_scene():
    small = prp.pvRenderPassChain()
    small.SyncDerivedSsao(_bounded_plotter(1.0).renderer)
    large = prp.pvRenderPassChain()
    large.SyncDerivedSsao(_bounded_plotter(100.0).renderer)
    assert large.GetSsaoRadius() == pytest.approx(100.0 * small.GetSsaoRadius(), rel=1e-6)


def test_props_that_opt_out_of_bounds_do_not_inflate_the_radius():
    pl = pv.Plotter(off_screen=True)
    pl.add_mesh(pv.Cube())
    ground = pl.add_mesh(pv.Plane(i_size=40, j_size=40))
    ground.SetUseBounds(False)
    chain = prp.pvRenderPassChain()
    chain.SyncDerivedSsao(pl.renderer)
    assert chain.GetSsaoBoundsDiagonal() == pytest.approx(np.sqrt(3.0), rel=1e-6)


def test_explicit_radius_pins_it():
    chain = prp.pvRenderPassChain()
    chain.SetSsaoRadius(0.5)
    assert chain.GetSsaoRadiusExplicit()
    assert not chain.SyncDerivedSsao(_bounded_plotter(100.0).renderer)
    assert chain.GetSsaoRadius() == pytest.approx(0.5)
    assert chain.GetSsaoBias() == pytest.approx(0.025)

    chain.SetSsaoRadiusToDerived()
    assert not chain.GetSsaoRadiusExplicit()
    assert chain.SyncDerivedSsao(_bounded_plotter(100.0).renderer)
    assert chain.GetSsaoRadius() > 1.0


def test_derivation_declines_a_degenerate_scene():
    chain = prp.pvRenderPassChain()
    empty = pv.Plotter(off_screen=True)
    assert not chain.SyncDerivedSsao(empty.renderer)
    assert chain.GetSsaoRadius() == pytest.approx(0.5)

    single_point = pv.Plotter(off_screen=True)
    single_point.add_mesh(pv.PolyData([[0.0, 0.0, 0.0]]))
    assert not chain.SyncDerivedSsao(single_point.renderer)
    assert chain.GetSsaoRadius() == pytest.approx(0.5)


def test_derivation_reports_whether_anything_moved():
    chain = prp.pvRenderPassChain()
    pl = _bounded_plotter(1.0)
    assert chain.SyncDerivedSsao(pl.renderer)
    assert not chain.SyncDerivedSsao(pl.renderer)


def test_nonsense_radius_is_refused_rather_than_stored():
    chain = prp.pvRenderPassChain()
    for bad in (0.0, -1.0, float('nan'), float('inf')):
        chain.SetSsaoRadius(bad)
        assert chain.GetSsaoRadius() == pytest.approx(0.5)


def test_build_refreshes_the_derived_radius():
    chain = _chain(SSAO=True)
    chain.Build(_bounded_plotter(100.0).renderer)
    assert chain.GetSsaoPass().GetRadius() == pytest.approx(0.03 * 100.0 * np.sqrt(3.0), rel=1e-6)


def test_ssao_knobs_reach_the_pass():
    chain = _chain(SSAO=True, SsaoKernelSize=64, SsaoBlur=False)
    chain.SetSsaoRadius(0.25)
    chain.SetSsaoBias(0.002)
    chain.Build(_bounded_plotter(1.0).renderer)
    ssao = chain.GetSsaoPass()
    assert ssao.GetRadius() == pytest.approx(0.25)
    assert ssao.GetBias() == pytest.approx(0.002)
    assert ssao.GetKernelSize() == 64
    assert not ssao.GetBlur()


def test_depth_format_defaults_to_vtks_own():
    chain = _chain(SSAO=True)
    assert chain.GetSsaoDepthFormat() == -1
    chain.Build(_bounded_plotter(1.0).renderer)
    assert chain.GetSsaoPass().GetDepthFormat() == vtkSSAOPass().GetDepthFormat()


def test_depth_format_override_reaches_the_pass():
    chain = _chain(SSAO=True, SsaoDepthFormat=vtkTextureObject.Fixed32)
    chain.Build(_bounded_plotter(1.0).renderer)
    assert chain.GetSsaoPass().GetDepthFormat() == vtkTextureObject.Fixed32


# -- the pixels ---------------------------------------------------------------


def _scene() -> pv.Plotter:
    pl = pv.Plotter(off_screen=True, window_size=(400, 300))
    pl.background_color = 'white'
    pl.add_mesh(pv.Plane(i_size=6, j_size=6, i_resolution=20, j_resolution=20), color='lightgray')
    for x in (-1.5, 0.0, 1.5):
        pl.add_mesh(pv.Sphere(radius=0.7, center=(x, 0.0, 0.35)), color='tan')
    pl.camera_position = [(6.5, -6.5, 4.5), (0, 0, 0.4), (0, 0, 1)]
    return pl


def _render(pl: pv.Plotter) -> np.ndarray:
    # screenshot() on an already rendered off-screen plotter hands back the
    # cached frame, so a SetPass after the first render needs an explicit one.
    pl.render_window.Render()
    return pl.screenshot(return_img=True).astype(np.float64)


def _render_through(pl: pv.Plotter, chain, base=None) -> np.ndarray:
    pl.renderer.SetPass(
        chain.Build(pl.renderer, base) if base is not None else chain.Build(pl.renderer)
    )
    try:
        return _render(pl)
    finally:
        pl.renderer.SetPass(None)
        chain.ReleaseGraphicsResources(pl.render_window)


def test_built_chain_matches_a_hand_built_chain_of_the_same_shape():
    pl = _scene()
    chain = _chain(SSAO=True, EDL=True, AnnotationBypass=True, Blur=True, AntiAliasing=True)
    chain.SetSsaoRadius(0.5)
    chain.SetSsaoBias(0.005)
    from_chain = _render_through(pl, chain)

    collection = vtkRenderPassCollection()
    collection.AddItem(vtkRenderStepsPass())
    sequence = vtkSequencePass()
    sequence.SetPasses(collection)
    camera = vtkCameraPass()
    camera.SetDelegatePass(sequence)
    ssao = vtkSSAOPass()
    ssao.SetRadius(0.5)
    ssao.SetBias(0.005)
    ssao.SetKernelSize(256)
    ssao.SetBlur(True)
    ssao.SetDelegatePass(camera)
    edl = vtkEDLShading()
    edl.SetDelegatePass(ssao)
    split = prp.make_split_pass(edl)
    blur = vtkGaussianBlurPass()
    blur.SetDelegatePass(split)
    ssaa = prp.pvSSAAVolumePass()
    ssaa.SetSupersampleFactor(np.sqrt(5.0))
    ssaa.SetPrimitiveScaleFactor(np.sqrt(5.0))
    ssaa.SetDelegatePass(blur)

    pl.renderer.SetPass(ssaa)
    try:
        by_hand = _render(pl)
    finally:
        pl.renderer.SetPass(None)
        ssaa.ReleaseGraphicsResources(pl.render_window)

    assert np.array_equal(from_chain, by_hand)


def test_ssao_below_edl_is_what_makes_the_occlusion_visible():
    # With EDL as SSAO's delegate the frame is EDL alone; the inverted order
    # darkens nothing, which is the only number this has to separate from.
    pl = _scene()
    without_ssao = _render_through(pl, _chain(EDL=True, AnnotationBypass=False))
    shipped = _chain(SSAO=True, EDL=True, AnnotationBypass=False)
    shipped.SetSsaoRadius(0.5)
    with_ssao = _render_through(pl, shipped)

    darkened = (without_ssao - with_ssao).max(axis=2) > 2.0
    assert darkened.mean() > 0.02


def test_derived_radius_holds_the_occlusion_steady_across_world_scale():
    shares = {}
    for policy in ('derived', 'fixed'):
        for scale in (1.0, 40.0):
            pl = pv.Plotter(off_screen=True, window_size=(400, 300))
            pl.background_color = 'white'
            pl.add_mesh(
                pv.Plane(i_size=6, j_size=6, i_resolution=20, j_resolution=20).scale(scale),
                color='lightgray',
            )
            for x in (-1.5, 0.0, 1.5):
                pl.add_mesh(pv.Sphere(radius=0.7, center=(x, 0.0, 0.35)).scale(scale), color='tan')
            pl.camera_position = [
                (6.5 * scale, -6.5 * scale, 4.5 * scale),
                (0, 0, 0.4 * scale),
                (0, 0, 1),
            ]

            off = _chain(AntiAliasing=True)
            without = _render_through(pl, off)
            chain = _chain(SSAO=True, AntiAliasing=True)
            if policy == 'fixed':
                chain.SetSsaoRadius(0.5)
            with_ao = _render_through(pl, chain)
            shares[(policy, scale)] = float(((without - with_ao).max(axis=2) > 2.0).mean())
            pl.close()

    derived_1x, derived_40x = shares[('derived', 1.0)], shares[('derived', 40.0)]
    fixed_1x, fixed_40x = shares[('fixed', 1.0)], shares[('fixed', 40.0)]
    assert derived_1x > 0.02
    assert abs(derived_40x - derived_1x) < 0.25 * derived_1x
    # The fixed radius is the control: it must not hold.
    assert abs(fixed_40x - fixed_1x) > 0.25 * fixed_1x


def test_shadows_darken_the_ground_and_keep_translucent_props():
    pl = pv.Plotter(off_screen=True, window_size=(300, 200), lighting='none')
    pl.add_mesh(pv.Sphere(radius=0.5, center=(0, 0, 0.5)), color='white')
    pl.add_mesh(pv.Plane(i_size=6, j_size=6), color='white')
    pl.add_mesh(pv.Sphere(radius=0.4, center=(1.2, 0, 0.4)), color='red', opacity=0.5)
    pl.add_light(pv.Light(position=(3, -1, 4), focal_point=(0, 0, 0), light_type='scene light'))
    pl.camera_position = [(4, -4, 3), (0, 0, 0.3), (0, 0, 1)]

    plain = _render_through(pl, _chain(AntiAliasing=True))
    shadowed = _render_through(pl, _chain(Shadows=True, AntiAliasing=True))
    pl.close()

    def dark(img: np.ndarray) -> int:
        return int((img[..., 0] < 120).sum())

    def red(img: np.ndarray) -> int:
        return int(((img[..., 0] > 150) & (img[..., 1] < 140)).sum())

    assert dark(shadowed) > dark(plain) + 2000
    assert red(shadowed) > 0.8 * red(plain)


def test_ssao_keeps_depth_peeled_translucent_props_their_colour():
    # A grey translucent cube over tan spheres: inside the SSAO delegate the
    # peeled cube came out painted with its normals (saturated red and green).
    pl = _scene()
    pl.add_mesh(pv.Cube(center=(0, 0, 0.6), x_length=5, y_length=5, z_length=1.2), opacity=0.5)
    peeled = _render_through(pl, _chain(DepthPeeling=True, SSAO=True))
    plain = _render_through(pl, _chain(DepthPeeling=True, AntiAliasing=True))
    pl.close()

    def saturated(img: np.ndarray) -> int:
        return int((img.max(axis=2) - img.min(axis=2) > 120).sum())

    assert saturated(plain) < 50, saturated(plain)
    assert saturated(peeled) < 50, saturated(peeled)
    assert np.abs(peeled - plain).mean() < 25.0


def test_release_graphics_resources_then_rebuild_still_renders():
    pl = _scene()
    chain = _chain(SSAO=True, EDL=True, AntiAliasing=True)
    first = _render_through(pl, chain)
    second = _render_through(pl, chain)
    assert np.array_equal(first, second)
