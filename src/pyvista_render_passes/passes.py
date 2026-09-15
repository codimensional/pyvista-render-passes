"""Pass factories and prop-tag helpers.

:func:`make_ssaa_pass` and :func:`enable_ssaa` build the volume-safe
:class:`pvSSAAVolumePass`. :func:`make_ssao_pass` and :func:`enable_ssao`
configure a stock ``vtkSSAOPass`` with PyVista's defaults.
:class:`pvPropKeyFilterPass` splits a renderer's props in two so one half can
take a different chain; the tag is a channel bitmask and every helper defaults
to :data:`CHANNEL_ANNOTATION`. :class:`pvRenderPassChain` assembles the whole
chain from one settings surface; the helpers here stay for one-pass use and for
composing a chain by hand.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ._backend import (
    pvPropKeyFilterPass,
    pvRenderPassChain,
    pvSSAAVolumePass,
    vtkCameraPass,
    vtkRenderPassCollection,
    vtkRenderStepsPass,
    vtkSequencePass,
    vtkSSAOPass,
)

if TYPE_CHECKING:
    import pyvista as pv

    from ._backend import vtkProp, vtkRenderPass

__all__ = [
    'CHANNEL_ANNOTATION',
    'clear_prop_filter_tag',
    'enable_ssaa',
    'enable_ssao',
    'has_prop_filter_tag',
    'make_prop_filter_pass',
    'make_split_pass',
    'make_ssaa_pass',
    'make_ssao_pass',
    'prop_filter_tag_is_set',
    'pvPropKeyFilterPass',
    'pvRenderPassChain',
    'pvSSAAVolumePass',
    'set_prop_filter_tag',
]

#: Prop-filter channel for annotation geometry that must bypass a screen-space
#: pass (bounds axes, orientation widgets, 2D actors). The default everywhere.
CHANNEL_ANNOTATION: int = pvPropKeyFilterPass.ChannelAnnotation

_MAX_CHANNEL = pvPropKeyFilterPass.ChannelMaximum

_MIN_SSAA_FACTOR = 1.0
_MAX_SSAA_FACTOR = 4.0
DEFAULT_SSAA_FACTOR = math.sqrt(5.0)

# PyVista's ``Renderer.enable_ssao`` defaults rather than VTK's own.
_DEFAULT_SSAO_RADIUS = 0.5
_DEFAULT_SSAO_BIAS = 0.005
_DEFAULT_SSAO_KERNEL_SIZE = 256
# vtkSSAOPass silently clamps KernelSize to this range.
_MIN_SSAO_KERNEL_SIZE = 1
_MAX_SSAO_KERNEL_SIZE = 1000


def _validate_channel(channel: int) -> int:
    channel = int(channel)
    if not CHANNEL_ANNOTATION <= channel <= _MAX_CHANNEL:
        msg = f'channel must be in [{CHANNEL_ANNOTATION}, {_MAX_CHANNEL}], got {channel}.'
        raise ValueError(msg)
    return channel


def _validate_ssaa_factor(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or not _MIN_SSAA_FACTOR <= value <= _MAX_SSAA_FACTOR:
        msg = f'factor must be finite and in [{_MIN_SSAA_FACTOR}, {_MAX_SSAA_FACTOR}].'
        raise ValueError(msg)
    return value


def _validate_ssao_settings(
    radius: float, bias: float, kernel_size: int
) -> tuple[float, float, int]:
    radius = float(radius)
    bias = float(bias)
    kernel_size = int(kernel_size)
    if not math.isfinite(radius) or radius <= 0.0:
        msg = f'radius must be finite and positive, got {radius}.'
        raise ValueError(msg)
    if not math.isfinite(bias) or bias < 0.0:
        msg = f'bias must be finite and non-negative, got {bias}.'
        raise ValueError(msg)
    if not _MIN_SSAO_KERNEL_SIZE <= kernel_size <= _MAX_SSAO_KERNEL_SIZE:
        msg = (
            f'kernel_size must be in [{_MIN_SSAO_KERNEL_SIZE}, {_MAX_SSAO_KERNEL_SIZE}], '
            f'got {kernel_size}.'
        )
        raise ValueError(msg)
    return radius, bias, kernel_size


def _camera_pass() -> vtkCameraPass:
    steps = vtkRenderStepsPass()
    collection = vtkRenderPassCollection()
    collection.AddItem(steps)
    sequence = vtkSequencePass()
    sequence.SetPasses(collection)
    camera = vtkCameraPass()
    camera.SetDelegatePass(sequence)
    return camera


def make_ssaa_pass(*, factor: float = DEFAULT_SSAA_FACTOR) -> pvSSAAVolumePass:
    """Create a volume-safe SSAA pass over the default camera/render-steps delegate.

    Parameters
    ----------
    factor : float, default: sqrt(5)
        Linear supersample factor per axis, in ``[1.0, 4.0]``.
        ``PrimitiveScaleFactor`` is set to the same value so point and line
        widths stay visually constant.

    Returns
    -------
    pvSSAAVolumePass
        Configured pass.

    Raises
    ------
    ValueError
        If ``factor`` is out of range.

    """
    factor = _validate_ssaa_factor(factor)
    ssaa = pvSSAAVolumePass()
    ssaa.SetSupersampleFactor(factor)
    ssaa.SetPrimitiveScaleFactor(factor)
    ssaa.SetDelegatePass(_camera_pass())
    return ssaa


def enable_ssaa(plotter: pv.BasePlotter, *, factor: float = DEFAULT_SSAA_FACTOR) -> None:
    """Install volume-safe SSAA on every renderer of a plotter.

    Replaces each renderer's pass; it does not compose with a pass already
    installed. Use :class:`pvRenderPassChain` or ``plotter.render_passes`` to
    combine passes.

    Parameters
    ----------
    plotter : pyvista.BasePlotter
        Plotter whose renderers receive the pass.

    factor : float, default: sqrt(5)
        Linear supersample factor per axis, in ``[1.0, 4.0]``.

    Raises
    ------
    ValueError
        If ``factor`` is out of range.

    """
    factor = _validate_ssaa_factor(factor)
    for renderer in plotter.renderers:
        renderer.SetPass(make_ssaa_pass(factor=factor))


def make_ssao_pass(
    *,
    radius: float = _DEFAULT_SSAO_RADIUS,
    bias: float = _DEFAULT_SSAO_BIAS,
    kernel_size: int = _DEFAULT_SSAO_KERNEL_SIZE,
    blur: bool = True,
) -> vtkSSAOPass:
    """Create an SSAO pass over the default camera/render-steps delegate.

    The depth-texture format is left at VTK's default. On a hardware GL context
    with a fixed-point window depth attachment the pass reads a zeroed depth
    texture and crushes the frame toward full occlusion unless
    ``SetDepthFormat(vtkTextureObject.Fixed32)`` is applied; ``plotter.render_passes``
    probes the renderer and sets it.

    Parameters
    ----------
    radius : float, default: 0.5
        Sampling hemisphere radius in world units.

    bias : float, default: 0.005
        Angle bias that suppresses self-occlusion on flat surfaces.

    kernel_size : int, default: 256
        Number of sampling directions.

    blur : bool, default: True
        Blur the occlusion result before combining it.

    Returns
    -------
    vtkSSAOPass
        Configured pass.

    Raises
    ------
    ValueError
        If ``radius``, ``bias``, or ``kernel_size`` is out of range.

    """
    radius, bias, kernel_size = _validate_ssao_settings(radius, bias, kernel_size)
    ssao = vtkSSAOPass()
    ssao.SetRadius(radius)
    ssao.SetBias(bias)
    ssao.SetKernelSize(kernel_size)
    ssao.SetBlur(bool(blur))
    ssao.SetDelegatePass(_camera_pass())
    return ssao


def enable_ssao(
    plotter: pv.BasePlotter,
    *,
    radius: float = _DEFAULT_SSAO_RADIUS,
    bias: float = _DEFAULT_SSAO_BIAS,
    kernel_size: int = _DEFAULT_SSAO_KERNEL_SIZE,
    blur: bool = True,
) -> None:
    """Install SSAO on every renderer of a plotter.

    Replaces each renderer's pass, so it does not compose with
    :func:`enable_ssaa` or :meth:`pyvista.Plotter.enable_ssao`; whichever runs
    last wins.

    Parameters
    ----------
    plotter : pyvista.BasePlotter
        Plotter whose renderers receive the pass.

    radius : float, default: 0.5
        Sampling hemisphere radius in world units.

    bias : float, default: 0.005
        Angle bias that suppresses self-occlusion on flat surfaces.

    kernel_size : int, default: 256
        Number of sampling directions.

    blur : bool, default: True
        Blur the occlusion result before combining it.

    Raises
    ------
    ValueError
        If ``radius``, ``bias``, or ``kernel_size`` is out of range.

    """
    radius, bias, kernel_size = _validate_ssao_settings(radius, bias, kernel_size)
    for renderer in plotter.renderers:
        renderer.SetPass(
            make_ssao_pass(radius=radius, bias=bias, kernel_size=kernel_size, blur=blur)
        )


def set_prop_filter_tag(
    prop: vtkProp, *, tagged: bool = True, channel: int = CHANNEL_ANNOTATION
) -> None:
    """Tag (or untag) a prop for :class:`pvPropKeyFilterPass` on one channel.

    Only the given channel's bit moves. Either value counts as a decision, so
    :func:`prop_filter_tag_is_set` reports ``True`` afterwards;
    :func:`clear_prop_filter_tag` undoes the decision itself.

    Parameters
    ----------
    prop : vtkProp
        Prop to tag. Its property keys are created on demand.

    tagged : bool, default: True
        ``True`` tags the prop; ``False`` records the opposite decision.

    channel : int, default: CHANNEL_ANNOTATION
        Channel to tag on.

    Raises
    ------
    ValueError
        If ``channel`` is out of range.

    """
    pvPropKeyFilterPass.SetPropMatching(prop, tagged, _validate_channel(channel))


def clear_prop_filter_tag(prop: vtkProp, *, channel: int = CHANNEL_ANNOTATION) -> None:
    """Return a prop to "never considered" on one channel.

    Parameters
    ----------
    prop : vtkProp
        Prop to reset.

    channel : int, default: CHANNEL_ANNOTATION
        Channel to clear.

    Raises
    ------
    ValueError
        If ``channel`` is out of range.

    """
    pvPropKeyFilterPass.ClearPropDecision(prop, _validate_channel(channel))


def has_prop_filter_tag(prop: vtkProp, *, channel: int = CHANNEL_ANNOTATION) -> bool:
    """Return whether a prop is tagged on one channel.

    Parameters
    ----------
    prop : vtkProp
        Prop to test.

    channel : int, default: CHANNEL_ANNOTATION
        Channel to test on.

    Returns
    -------
    bool
        ``True`` when the prop is tagged on that channel.

    Raises
    ------
    ValueError
        If ``channel`` is out of range.

    """
    return bool(pvPropKeyFilterPass.IsPropMatching(prop, _validate_channel(channel)))


def prop_filter_tag_is_set(prop: vtkProp, *, channel: int = CHANNEL_ANNOTATION) -> bool:
    """Return whether a channel has been decided either way.

    An auto-tagging sweep uses this so it does not re-tag a prop that was
    untagged on purpose.

    Parameters
    ----------
    prop : vtkProp
        Prop to test.

    channel : int, default: CHANNEL_ANNOTATION
        Channel to test on.

    Returns
    -------
    bool
        ``True`` when :func:`set_prop_filter_tag` has run for that channel.

    Raises
    ------
    ValueError
        If ``channel`` is out of range.

    """
    return bool(pvPropKeyFilterPass.IsPropConsidered(prop, _validate_channel(channel)))


def make_prop_filter_pass(
    delegate: vtkRenderPass | None = None,
    *,
    tagged: bool = True,
    preserve_buffers: bool = False,
    channel: int = CHANNEL_ANNOTATION,
) -> pvPropKeyFilterPass:
    """Create a pass that renders its delegate over one half of the props.

    Parameters
    ----------
    delegate : vtkRenderPass, optional
        Pass rendered over the filtered subset. Defaults to a camera pass over
        the standard render steps.

    tagged : bool, default: True
        ``True`` renders only tagged props; ``False`` renders every other prop.

    preserve_buffers : bool, default: False
        Composite onto the buffers an earlier stage left rather than clearing
        them. Required for any stage after the first.

    channel : int, default: CHANNEL_ANNOTATION
        Channel to partition on. Passes on different channels split the same
        prop array independently.

    Returns
    -------
    pvPropKeyFilterPass
        Configured pass.

    Raises
    ------
    ValueError
        If ``channel`` is out of range.

    """
    filter_pass = pvPropKeyFilterPass()
    filter_pass.SetMode(
        pvPropKeyFilterPass.RenderMatching if tagged else pvPropKeyFilterPass.RenderNonMatching
    )
    filter_pass.SetChannel(_validate_channel(channel))
    filter_pass.SetPreserveBuffers(preserve_buffers)
    filter_pass.SetDelegatePass(delegate if delegate is not None else _camera_pass())
    return filter_pass


def make_split_pass(
    scene_pass: vtkRenderPass,
    *,
    tagged_delegate: vtkRenderPass | None = None,
    channel: int = CHANNEL_ANNOTATION,
) -> vtkSequencePass:
    """Compose a scene pass with a separate stage for tagged props.

    The sequence renders ``scene_pass`` over every untagged prop, then the
    tagged props with ``tagged_delegate`` against the depth the first stage
    wrote. A split nests inside another split on a different channel by passing
    the inner sequence as ``scene_pass``.

    Parameters
    ----------
    scene_pass : vtkRenderPass
        Pass applied to the untagged scene, e.g. a ``vtkEDLShading`` wrapping a
        camera pass.

    tagged_delegate : vtkRenderPass, optional
        Pass for the tagged props. Defaults to a plain camera pass over the
        standard render steps. It must end in a camera pass and must not rely
        on the renderer's erase flag to clear a framebuffer of its own: the
        stage suppresses that flag, so an FBO-owning pass that clears through
        ``vtkRenderer::Clear`` (SSAA, SSAO, blur) would render into
        uninitialised memory. Wrap the whole sequence in such a pass instead.

    channel : int, default: CHANNEL_ANNOTATION
        Channel both stages filter on.

    Returns
    -------
    vtkSequencePass
        Sequence of the two filtered stages.

    Raises
    ------
    ValueError
        If ``channel`` is out of range.

    Examples
    --------
    Shade the scene with EDL while a tagged plane renders unshaded on top.

    >>> import pyvista as pv
    >>> from pyvista_render_passes import (
    ...     backend_module,
    ...     make_split_pass,
    ...     pvRenderPassChain,
    ...     set_prop_filter_tag,
    ... )
    >>> pl = pv.Plotter(off_screen=True)
    >>> _ = pl.add_mesh(pv.Sphere())
    >>> plane = pl.add_mesh(pv.Plane())
    >>> set_prop_filter_tag(plane)
    >>> edl = backend_module('vtkRenderingOpenGL2').vtkEDLShading()
    >>> edl.SetDelegatePass(pvRenderPassChain().BuildSceneBase())
    >>> pl.renderer.SetPass(make_split_pass(edl))
    >>> pl.renderer.GetPass().GetPasses().GetNumberOfItems()
    2

    """
    channel = _validate_channel(channel)
    scene_stage = make_prop_filter_pass(scene_pass, tagged=False, channel=channel)
    tagged_stage = make_prop_filter_pass(
        tagged_delegate, tagged=True, preserve_buffers=True, channel=channel
    )

    collection = vtkRenderPassCollection()
    collection.AddItem(scene_stage)
    collection.AddItem(tagged_stage)
    sequence = vtkSequencePass()
    sequence.SetPasses(collection)
    return sequence
