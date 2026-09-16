"""The prop-subset filter pass and its channel tags."""

from __future__ import annotations

import numpy as np
import pytest
import pyvista as pv

import pyvista_render_passes as prp
from pyvista_render_passes import (
    CHANNEL_ANNOTATION,
    clear_prop_filter_tag,
    has_prop_filter_tag,
    make_prop_filter_pass,
    make_split_pass,
    prop_filter_tag_is_set,
    pvPropKeyFilterPass,
    reserve_prop_filter_channel,
    set_prop_filter_tag,
)
from tests.backend import (
    vtkCameraPass,
    vtkCommand,
    vtkEDLShading,
    vtkInformation,
    vtkInformationIntegerKey,
    vtkProp,
    vtkRenderPass,
    vtkRenderPassCollection,
    vtkRenderStepsPass,
    vtkSequencePass,
    vtkVolumetricPass,
)

OTHER_CHANNEL = reserve_prop_filter_channel('tests.other', channel=1)

_SCENE_CENTER = (-0.8, 0.0, 0.0)
_TAGGED_CENTER = (0.8, 0.0, 0.0)

# Untagged red, channel-0 green, channel-1 blue: one sphere per third of the frame.
_THIRD_CENTERS = ((-1.2, 0.0, 0.0), (0.0, 0.0, 0.0), (1.2, 0.0, 0.0))
_THIRD_COLORS = ((255, 0, 0), (0, 255, 0), (0, 0, 255))
_THIRD_CHANNELS = (None, CHANNEL_ANNOTATION, OTHER_CHANNEL)
_RED, _GREEN, _BLUE = 0, 1, 2


def _camera_pass() -> vtkCameraPass:
    steps = vtkRenderStepsPass()
    collection = vtkRenderPassCollection()
    collection.AddItem(steps)
    sequence = vtkSequencePass()
    sequence.SetPasses(collection)
    camera = vtkCameraPass()
    camera.SetDelegatePass(sequence)
    return camera


def _two_sphere_plotter() -> tuple[pv.Plotter, pv.Actor, pv.Actor]:
    pl = pv.Plotter(off_screen=True, window_size=(300, 200))
    pl.set_background('black')
    scene = pl.add_mesh(pv.Sphere(radius=0.5, center=_SCENE_CENTER), color='red', lighting=False)
    tagged = pl.add_mesh(
        pv.Sphere(radius=0.5, center=_TAGGED_CENTER), color='blue', lighting=False
    )
    set_prop_filter_tag(tagged)
    pl.camera_position = 'xy'
    pl.camera.zoom(1.2)
    return pl, scene, tagged


def _halves(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    width = image.shape[1]
    return image[:, : width // 2], image[:, width // 2 :]


def _colour_max(image: np.ndarray, colour: int) -> int:
    return int(image[..., colour].max())


def _three_sphere_plotter() -> pv.Plotter:
    pl = pv.Plotter(off_screen=True, window_size=(360, 200))
    pl.set_background('black')
    for center, color, channel in zip(_THIRD_CENTERS, _THIRD_COLORS, _THIRD_CHANNELS, strict=True):
        actor = pl.add_mesh(pv.Sphere(radius=0.45, center=center), color=color, lighting=False)
        if channel is not None:
            set_prop_filter_tag(actor, channel=channel)
    pl.enable_parallel_projection()
    pl.camera_position = [(0.0, 0.0, 6.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    pl.camera.parallel_scale = 1.0
    return pl


def _thirds(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    width = image.shape[1]
    edge = width // 3
    return image[:, :edge], image[:, edge : 2 * edge], image[:, 2 * edge :]


def _lit_pixels(image: np.ndarray, colour: int, threshold: int = 150) -> int:
    return int(np.count_nonzero(image[..., colour] > threshold))


def _stages(sequence: vtkSequencePass) -> list[pvPropKeyFilterPass]:
    passes = sequence.GetPasses()
    passes.InitTraversal()
    return [passes.GetNextRenderPass() for _ in range(passes.GetNumberOfItems())]


def _sequence(*passes: vtkRenderPass) -> vtkSequencePass:
    collection = vtkRenderPassCollection()
    for item in passes:
        collection.AddItem(item)
    sequence = vtkSequencePass()
    sequence.SetPasses(collection)
    return sequence


# -- object surface -----------------------------------------------------------


def test_pass_is_render_pass():
    assert isinstance(pvPropKeyFilterPass(), vtkRenderPass)
    assert prp.pvPropKeyFilterPass is pvPropKeyFilterPass


def test_mode_defaults_and_setters():
    p = pvPropKeyFilterPass()
    assert p.GetMode() == pvPropKeyFilterPass.RenderMatching
    p.SetModeToRenderNonMatching()
    assert p.GetMode() == pvPropKeyFilterPass.RenderNonMatching
    p.SetModeToRenderMatching()
    assert p.GetMode() == pvPropKeyFilterPass.RenderMatching


def test_mode_is_clamped_to_the_valid_values():
    p = pvPropKeyFilterPass()
    p.SetMode(99)
    assert p.GetMode() == pvPropKeyFilterPass.RenderAll
    p.SetMode(-5)
    assert p.GetMode() == pvPropKeyFilterPass.RenderMatching
    p.SetModeToRenderAll()
    assert p.GetMode() == pvPropKeyFilterPass.RenderAll


def test_exclude_volumes_drops_volume_props_from_the_subset():
    pl = pv.Plotter(off_screen=True, window_size=(120, 120))
    pl.add_mesh(pv.Cube(), color='tan')
    pl.add_volume(pv.Wavelet(), opacity='sigmoid', show_scalar_bar=False)
    counting = vtkVolumetricPass()
    p = pvPropKeyFilterPass()
    p.SetModeToRenderAll()
    p.SetDelegatePass(counting)
    camera = vtkCameraPass()
    camera.SetDelegatePass(p)
    pl.renderer.SetPass(camera)
    pl.screenshot(return_img=True)
    with_volumes = p.GetNumberOfRenderedProps()
    p.ExcludeVolumesOn()
    pl.render_window.Render()
    without_volumes = p.GetNumberOfRenderedProps()
    pl.renderer.SetPass(None)
    pl.close()
    assert (with_volumes, without_volumes) == (1, 0)


def test_delegate_pass_roundtrip():
    p = pvPropKeyFilterPass()
    delegate = vtkRenderStepsPass()
    p.SetDelegatePass(delegate)
    assert p.GetDelegatePass() is delegate


def test_keys_are_stable_distinct_information_keys():
    assert isinstance(pvPropKeyFilterPass.FILTER_KEY(), vtkInformationIntegerKey)
    assert pvPropKeyFilterPass.FILTER_KEY() is pvPropKeyFilterPass.FILTER_KEY()
    assert isinstance(pvPropKeyFilterPass.CONSIDERED_KEY(), vtkInformationIntegerKey)
    assert pvPropKeyFilterPass.CONSIDERED_KEY() is pvPropKeyFilterPass.CONSIDERED_KEY()
    assert pvPropKeyFilterPass.CONSIDERED_KEY() is not pvPropKeyFilterPass.FILTER_KEY()


def test_print_self_reports_mode_channel_and_delegate():
    p = pvPropKeyFilterPass()
    p.SetModeToRenderNonMatching()
    p.SetChannel(OTHER_CHANNEL)
    text = str(p)
    assert 'RenderNonMatching' in text
    assert f'Channel: {OTHER_CHANNEL}' in text
    assert '(none)' in text


# -- channels -----------------------------------------------------------------


def test_default_channel_is_the_annotation_channel():
    assert CHANNEL_ANNOTATION == 0
    assert pvPropKeyFilterPass().GetChannel() == CHANNEL_ANNOTATION


def test_channel_is_clamped_to_the_representable_range():
    p = pvPropKeyFilterPass()
    p.SetChannel(99)
    assert p.GetChannel() == pvPropKeyFilterPass.ChannelMaximum
    p.SetChannel(-5)
    assert p.GetChannel() == CHANNEL_ANNOTATION


def test_channel_bit_is_one_shifted_by_the_channel():
    assert pvPropKeyFilterPass.ChannelBit(CHANNEL_ANNOTATION) == 1
    assert pvPropKeyFilterPass.ChannelBit(OTHER_CHANNEL) == 2
    assert pvPropKeyFilterPass.ChannelBit(pvPropKeyFilterPass.ChannelMaximum) == 1 << 30


def test_an_out_of_range_channel_bit_aliases_nothing():
    assert pvPropKeyFilterPass.ChannelBit(-1) == 0
    assert pvPropKeyFilterPass.ChannelBit(31) == 0


@pytest.mark.parametrize('channel', [-1, 31, 99])
def test_helpers_reject_an_out_of_range_channel(channel):
    actor = pv.Actor()
    for call in (
        lambda: set_prop_filter_tag(actor, channel=channel),
        lambda: has_prop_filter_tag(actor, channel=channel),
        lambda: prop_filter_tag_is_set(actor, channel=channel),
        lambda: clear_prop_filter_tag(actor, channel=channel),
        lambda: make_prop_filter_pass(channel=channel),
        lambda: make_split_pass(_camera_pass(), channel=channel),
    ):
        with pytest.raises(ValueError, match='channel must be in'):
            call()


def test_channels_are_independent_on_one_prop():
    actor = pv.Actor()
    set_prop_filter_tag(actor, channel=CHANNEL_ANNOTATION)
    set_prop_filter_tag(actor, channel=OTHER_CHANNEL)
    assert has_prop_filter_tag(actor, channel=CHANNEL_ANNOTATION)
    assert has_prop_filter_tag(actor, channel=OTHER_CHANNEL)

    set_prop_filter_tag(actor, tagged=False, channel=OTHER_CHANNEL)
    assert has_prop_filter_tag(actor, channel=CHANNEL_ANNOTATION)
    assert not has_prop_filter_tag(actor, channel=OTHER_CHANNEL)
    assert prop_filter_tag_is_set(actor, channel=OTHER_CHANNEL)

    clear_prop_filter_tag(actor, channel=OTHER_CHANNEL)
    assert has_prop_filter_tag(actor, channel=CHANNEL_ANNOTATION)
    assert prop_filter_tag_is_set(actor, channel=CHANNEL_ANNOTATION)
    assert not prop_filter_tag_is_set(actor, channel=OTHER_CHANNEL)


def test_tagging_one_channel_leaves_the_others_undecided():
    actor = pv.Actor()
    set_prop_filter_tag(actor, channel=OTHER_CHANNEL)
    assert not prop_filter_tag_is_set(actor, channel=CHANNEL_ANNOTATION)
    assert not has_prop_filter_tag(actor, channel=CHANNEL_ANNOTATION)
    for channel in (2, 7, pvPropKeyFilterPass.ChannelMaximum):
        assert not prop_filter_tag_is_set(actor, channel=channel)


def test_every_channel_is_undecided_on_an_untouched_prop():
    actor = pv.Actor()
    for channel in (CHANNEL_ANNOTATION, OTHER_CHANNEL, pvPropKeyFilterPass.ChannelMaximum):
        assert not has_prop_filter_tag(actor, channel=channel)
        assert not prop_filter_tag_is_set(actor, channel=channel)


def test_clearing_an_untouched_prop_creates_no_property_keys():
    actor = pv.Actor()
    clear_prop_filter_tag(actor)
    assert actor.GetPropertyKeys() is None


def test_channel_zero_tag_values_are_plain_one_and_zero():
    actor = pv.Actor()
    set_prop_filter_tag(actor)
    assert actor.GetPropertyKeys().Get(pvPropKeyFilterPass.FILTER_KEY()) == 1
    set_prop_filter_tag(actor, tagged=False)
    assert actor.GetPropertyKeys().Get(pvPropKeyFilterPass.FILTER_KEY()) == 0


def test_a_raw_filter_key_reads_as_a_channel_zero_decision():
    tagged = pv.Actor()
    keys = vtkInformation()
    keys.Set(pvPropKeyFilterPass.FILTER_KEY(), 1)
    tagged.SetPropertyKeys(keys)
    assert has_prop_filter_tag(tagged)
    assert prop_filter_tag_is_set(tagged)
    assert not has_prop_filter_tag(tagged, channel=OTHER_CHANNEL)
    assert not prop_filter_tag_is_set(tagged, channel=OTHER_CHANNEL)

    untagged = pv.Actor()
    keys = vtkInformation()
    keys.Set(pvPropKeyFilterPass.FILTER_KEY(), 0)
    untagged.SetPropertyKeys(keys)
    assert not has_prop_filter_tag(untagged)
    assert prop_filter_tag_is_set(untagged)


def test_tagging_a_raw_key_prop_on_another_channel_keeps_channel_zero():
    actor = pv.Actor()
    keys = vtkInformation()
    keys.Set(pvPropKeyFilterPass.FILTER_KEY(), 1)
    actor.SetPropertyKeys(keys)
    set_prop_filter_tag(actor, channel=OTHER_CHANNEL)
    assert has_prop_filter_tag(actor)
    assert has_prop_filter_tag(actor, channel=OTHER_CHANNEL)
    assert keys.Get(pvPropKeyFilterPass.FILTER_KEY()) == 0b11


def test_static_mutators_are_null_and_range_safe():
    pvPropKeyFilterPass.SetPropMatching(None, True)
    pvPropKeyFilterPass.ClearPropDecision(None)
    assert not pvPropKeyFilterPass.IsPropMatching(None)
    actor = pv.Actor()
    pvPropKeyFilterPass.SetPropMatching(actor, True, 99)
    assert actor.GetPropertyKeys() is None


def test_untagging_records_an_explicit_decision():
    actor = pv.Actor()
    assert not prop_filter_tag_is_set(actor)
    set_prop_filter_tag(actor, tagged=False)
    assert prop_filter_tag_is_set(actor)
    assert not has_prop_filter_tag(actor)


def test_tagging_preserves_other_property_keys():
    # A stock VTK key rather than MakeKey(): a Python-created key is collected
    # out from under the C++ side.
    other_key = vtkProp.GENERAL_TEXTURE_UNIT()
    actor = pv.Actor()
    keys = vtkInformation()
    keys.Set(other_key, 7)
    actor.SetPropertyKeys(keys)
    set_prop_filter_tag(actor)
    set_prop_filter_tag(actor, tagged=False)
    assert actor.GetPropertyKeys().Get(other_key) == 7
    assert not has_prop_filter_tag(actor)


# -- factories ----------------------------------------------------------------


def test_make_prop_filter_pass_defaults():
    p = make_prop_filter_pass()
    assert p.GetMode() == pvPropKeyFilterPass.RenderMatching
    assert p.GetChannel() == CHANNEL_ANNOTATION
    assert isinstance(p.GetDelegatePass(), vtkCameraPass)


def test_make_prop_filter_pass_accepts_a_delegate_mode_and_channel():
    delegate = vtkRenderStepsPass()
    p = make_prop_filter_pass(delegate, tagged=False, channel=OTHER_CHANNEL)
    assert p.GetDelegatePass() is delegate
    assert p.GetMode() == pvPropKeyFilterPass.RenderNonMatching
    assert p.GetChannel() == OTHER_CHANNEL


def test_make_split_pass_orders_scene_then_tagged():
    edl = vtkEDLShading()
    scene_stage, tagged_stage = _stages(make_split_pass(edl))
    assert scene_stage.GetMode() == pvPropKeyFilterPass.RenderNonMatching
    assert scene_stage.GetDelegatePass() is edl
    assert not scene_stage.GetPreserveBuffers()
    assert tagged_stage.GetMode() == pvPropKeyFilterPass.RenderMatching
    assert tagged_stage.GetPreserveBuffers()
    assert isinstance(tagged_stage.GetDelegatePass(), vtkCameraPass)


def test_make_split_pass_puts_both_stages_on_one_channel():
    for stage in _stages(make_split_pass(vtkRenderStepsPass(), channel=OTHER_CHANNEL)):
        assert stage.GetChannel() == OTHER_CHANNEL


def test_a_split_nests_inside_a_split_on_another_channel():
    inner = make_split_pass(vtkRenderStepsPass())
    outer = make_split_pass(inner, channel=OTHER_CHANNEL)
    scene_stage, _ = _stages(outer)
    assert scene_stage.GetDelegatePass() is inner
    assert all(stage.GetChannel() == CHANNEL_ANNOTATION for stage in _stages(inner))


# -- rendering ----------------------------------------------------------------


def test_matching_mode_renders_only_tagged_props():
    pl, _, _ = _two_sphere_plotter()
    pl.renderer.SetPass(make_prop_filter_pass(tagged=True))
    left, right = _halves(pl.screenshot(return_img=True))
    assert _colour_max(left, _RED) < 10
    assert _colour_max(right, _BLUE) > 200


def test_non_matching_mode_renders_only_untagged_props():
    pl, _, _ = _two_sphere_plotter()
    pl.renderer.SetPass(make_prop_filter_pass(tagged=False))
    left, right = _halves(pl.screenshot(return_img=True))
    assert _colour_max(left, _RED) > 200
    assert _colour_max(right, _BLUE) < 10


def test_split_pass_renders_both_halves():
    pl, _, _ = _two_sphere_plotter()
    pl.renderer.SetPass(make_split_pass(_camera_pass()))
    left, right = _halves(pl.screenshot(return_img=True))
    assert _colour_max(left, _RED) > 200
    assert _colour_max(right, _BLUE) > 200


def test_an_empty_subset_renders_the_rest_of_the_scene():
    pl, scene, _ = _two_sphere_plotter()
    set_prop_filter_tag(scene)
    pl.renderer.SetPass(make_split_pass(_camera_pass()))
    left, right = _halves(pl.screenshot(return_img=True))
    assert _colour_max(left, _RED) > 200
    assert _colour_max(right, _BLUE) > 200


def test_render_without_a_delegate_errors_and_draws_nothing():
    pl, _, _ = _two_sphere_plotter()
    p = pvPropKeyFilterPass()
    errors: list[str] = []
    p.AddObserver(vtkCommand.ErrorEvent, lambda *args: errors.append(str(args)))
    pl.renderer.SetPass(p)
    image = pl.screenshot(return_img=True)
    assert errors
    assert int(image.max()) < 10


def test_render_survives_releasing_the_delegate_through_the_filter_pass():
    pl, _, _ = _two_sphere_plotter()
    delegate = prp.pvSSAAVolumePass()
    delegate.SetDelegatePass(_camera_pass())
    p = make_prop_filter_pass(delegate, tagged=False)
    pl.renderer.SetPass(p)
    before = pl.screenshot(return_img=True).astype(float)
    p.ReleaseGraphicsResources(pl.render_window)
    after = pl.screenshot(return_img=True).astype(float)
    assert before.max() > 0
    assert np.abs(after - before).max() == 0.0


def test_tagged_props_are_occluded_by_scene_geometry_under_edl():
    pl = pv.Plotter(off_screen=True, window_size=(300, 200))
    pl.set_background('black')
    pl.add_mesh(pv.Sphere(radius=0.6), color='red', lighting=False)
    behind = pl.add_mesh(
        pv.Plane(center=(0, 0, -2.0), direction=(0, 0, 1), i_size=4, j_size=4),
        color='blue',
        lighting=False,
    )
    set_prop_filter_tag(behind)
    pl.camera_position = 'xy'
    pl.renderer.SetPass(make_split_pass(_edl_pass()))
    image = pl.screenshot(return_img=True)
    center = image[image.shape[0] // 2, image.shape[1] // 2]
    assert center[_RED] > 100, center
    assert center[_BLUE] < 60, center


def _edl_pass() -> vtkEDLShading:
    edl = vtkEDLShading()
    edl.SetDelegatePass(_camera_pass())
    return edl


def _render(*, pass_factory=None, tag: bool = True) -> np.ndarray:
    pl, _, tagged = _two_sphere_plotter()
    if not tag:
        set_prop_filter_tag(tagged, tagged=False)
    if pass_factory is not None:
        pl.renderer.SetPass(pass_factory())
    return pl.screenshot(return_img=True).astype(float)


def test_split_is_bit_identical_to_the_bare_pass_when_nothing_is_tagged():
    split = _render(pass_factory=lambda: make_split_pass(_edl_pass()), tag=False)
    bare = _render(pass_factory=_edl_pass, tag=False)
    assert np.abs(split - bare).max() == 0.0


def test_edl_shades_the_scene_but_not_the_tagged_props():
    plain_left, plain_right = _halves(_render())
    _edl_left, edl_right = _halves(_render(pass_factory=_edl_pass))
    split_left, split_right = _halves(_render(pass_factory=lambda: make_split_pass(_edl_pass())))
    assert np.abs(edl_right - plain_right).max() > 20
    assert np.abs(split_right - plain_right).max() == 0.0
    assert np.abs(split_left - plain_left).max() > 20


def test_tagging_changes_the_scene_shading_because_edl_reads_the_whole_depth_image():
    tagged_split = _render(pass_factory=lambda: make_split_pass(_edl_pass()))
    untagged_split = _render(pass_factory=lambda: make_split_pass(_edl_pass()), tag=False)
    scene_box = (slice(60, 140), slice(20, 120))
    delta = np.abs(tagged_split[scene_box] - untagged_split[scene_box]).max()
    assert 0.0 < delta < 100.0, delta


def test_alpha_emitting_annotation_stays_blended_after_a_screen_space_pass():
    # An actor whose fragment shader emits alpha is classified opaque by VTK and
    # relies on the blending the second stage restores.
    pl = pv.Plotter(off_screen=True, window_size=(200, 200))
    pl.set_background('black')
    quarter_blue = pl.add_mesh(pv.Plane(i_size=8, j_size=8), color='blue', lighting=False)
    quarter_blue.add_shader_replacement(
        'fragment',
        '//VTK::Light::Impl',
        'fragOutput0 = vec4(0.0, 0.0, 1.0, 0.25);',
        _feature_name='test_alpha_blend',
    )
    set_prop_filter_tag(quarter_blue)
    pl.add_mesh(pv.Plane(center=(0, 0, -1), i_size=8, j_size=8), color='white', lighting=False)
    pl.camera_position = 'xy'
    pl.renderer.SetPass(make_split_pass(_edl_pass()))
    center = pl.screenshot(return_img=True)[100, 100]
    assert center[_BLUE] > 200, center
    assert center[_RED] > 100, center


def test_two_channels_partition_one_renderer_independently():
    pl = _three_sphere_plotter()
    pl.renderer.SetPass(
        _sequence(
            make_prop_filter_pass(tagged=True, channel=OTHER_CHANNEL),
            make_prop_filter_pass(tagged=True, channel=CHANNEL_ANNOTATION, preserve_buffers=True),
        )
    )
    untagged, channel_0, channel_1 = _thirds(pl.screenshot(return_img=True))
    assert _lit_pixels(channel_0, _GREEN) > 500
    assert _lit_pixels(channel_1, _BLUE) > 500
    assert _lit_pixels(untagged, _RED) == 0
    assert _lit_pixels(channel_0, _BLUE) == 0
    assert _lit_pixels(channel_1, _GREEN) == 0


def test_a_stage_ignores_a_prop_tagged_only_on_another_channel():
    pl = _three_sphere_plotter()
    pl.renderer.SetPass(make_prop_filter_pass(tagged=False, channel=CHANNEL_ANNOTATION))
    untagged, channel_0, channel_1 = _thirds(pl.screenshot(return_img=True))
    assert _lit_pixels(untagged, _RED) > 500
    assert _lit_pixels(channel_1, _BLUE) > 500
    assert _lit_pixels(channel_0, _GREEN) == 0


def test_nested_splits_run_two_channels_together():
    pl = _three_sphere_plotter()
    plain = pl.screenshot(return_img=True).astype(float)
    pl.close()

    pl = _three_sphere_plotter()
    annotation_split = make_split_pass(_edl_pass())
    pl.renderer.SetPass(make_split_pass(annotation_split, channel=OTHER_CHANNEL))
    composed = pl.screenshot(return_img=True).astype(float)

    plain_untagged, plain_0, plain_1 = _thirds(plain)
    untagged, channel_0, channel_1 = _thirds(composed)
    assert _lit_pixels(untagged, _RED, threshold=30) > 500
    assert _lit_pixels(channel_0, _GREEN) > 500
    assert _lit_pixels(channel_1, _BLUE) > 500
    assert np.abs(channel_0 - plain_0).max() == 0.0
    assert np.abs(channel_1 - plain_1).max() == 0.0
    assert np.abs(untagged - plain_untagged).max() > 20
