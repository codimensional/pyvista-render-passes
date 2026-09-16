"""``plotter.render_passes``: one settings surface over the whole pass chain.

:class:`RenderPasses` is registered through PyVista's plotter-component
registry as ``plotter.render_passes`` and hands out the active subplot's
:class:`RenderPassComponent`, one per renderer. The ``enable_*`` / ``disable_*``
methods only set flags; :meth:`RenderPassComponent.apply` rebuilds the chain
from them. Auto-apply (on by default) runs ``apply`` from a renderer
``StartEvent`` observer, so a changed setting takes effect on the next render.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import copy
import functools
import logging
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Self, cast
import warnings

import pyvista as pv

from ._backend import (
    vtkActor2D,
    vtkAnnotatedCubeActor,
    vtkAxesActor,
    vtkCubeAxesActor,
    vtkLegendScaleActor,
    vtkProp,
    vtkRenderPass,
    vtkTextureObject,
)
from .passes import (
    DEFAULT_SSAA_FACTOR,
    prop_filter_tag_is_set,
    pvRenderPassChain,
    set_prop_filter_tag,
)
from .providers import (
    SINGLE_PROVIDER_STAGES,
    STAGES,
    PassProvider,
    SettingsVetoedError,
    Stage,
    iter_entry_point_providers,
)

__all__ = ['ANNOTATION_PROP_TYPES', 'RenderPassComponent', 'RenderPasses', 'tag_scene_annotations']

logger = logging.getLogger(__name__)

#: Prop types that describe a scene rather than being one. The 3D ones write
#: real depth, so a depth-driven pass shades them; the 2D ones (scalar bars,
#: corner text, legends) write none, which does not keep them out either.
ANNOTATION_PROP_TYPES: tuple[type, ...] = (
    vtkCubeAxesActor,
    vtkAxesActor,
    vtkAnnotatedCubeActor,
    vtkLegendScaleActor,
    vtkActor2D,
)


def _iter_props(renderer: pv.Renderer) -> Iterator[vtkProp]:
    # ``renderer.actors`` only carries named actors; annotations are exactly the
    # props PyVista adds without a name.
    props = renderer.GetViewProps()
    props.InitTraversal()
    while (prop := props.GetNextProp()) is not None:
        yield prop


def tag_scene_annotations(target: pv.BasePlotter | pv.Renderer) -> list[vtkProp]:
    """Tag every recognisable annotation prop in a renderer for the EDL bypass.

    Sweeps the renderer for :data:`ANNOTATION_PROP_TYPES` and tags any prop
    with no decision recorded yet, so a prop untagged on purpose stays untagged.
    ``apply`` and the auto-apply observer call this; call it directly to tag
    sooner.

    Parameters
    ----------
    target : pyvista.BasePlotter | pyvista.Renderer
        Plotter (its active renderer is used) or renderer to sweep.

    Returns
    -------
    list[vtkProp]
        Props newly tagged by this call.

    """
    renderer = target if isinstance(target, pv.Renderer) else target.renderer
    tagged: list[vtkProp] = []
    for prop in _iter_props(renderer):
        if isinstance(prop, ANNOTATION_PROP_TYPES) and not prop_filter_tag_is_set(prop):
            set_prop_filter_tag(prop)
            tagged.append(prop)
    return tagged


@functools.cache
def _using_software_renderer() -> bool:
    # An unprobeable backend is treated as software: the fixed-point depth
    # override is only needed on hardware GL, and hardware tolerates its
    # absence better than software tolerates its presence.
    try:
        renderer = pv.GPUInfo().renderer.lower()
    except RuntimeError:
        return True
    return 'llvmpipe' in renderer or 'swrast' in renderer


def _ssao_depth_format(*, is_software_renderer: bool | None = None) -> int | None:
    """Return the SSAO depth-texture format override, or ``None`` for VTK's default.

    VTK's offscreen hardware GL context allocates a fixed-point window depth
    attachment, so the blit feeding ``vtkSSAOPass`` its scene depth is a format
    mismatch against the pass's ``Float32`` default; a strict driver rejects it
    and the pass reads a zeroed depth texture, crushing the whole frame toward
    full occlusion. Software GL tolerates the mismatch, so it keeps the default.
    """
    if is_software_renderer is None:
        is_software_renderer = _using_software_renderer()
    return None if is_software_renderer else vtkTextureObject.Fixed32


# Attributes whose mutation queues a rebuild. Bookkeeping attributes and the
# live SSAA factor (written through to the pass without a rebuild) are absent.
_PASS_STATE_ATTRS = frozenset(
    {
        '_depth_peeling',
        '_depth_peeling_max_peels',
        '_depth_peeling_occlusion_ratio',
        '_edl',
        '_annotation_bypass',
        '_ssao',
        '_ssao_radius',
        '_ssao_bias',
        '_ssao_kernel_size',
        '_ssao_blur',
        '_anti_aliasing',
        '_msaa',
        '_msaa_samples',
        '_dof',
        '_dof_auto_focal',
        '_shadows',
        '_blur',
        '_hidden_line_removal',
    }
)

# Everything a refused transaction puts back.
_SNAPSHOT_ATTRS = (*sorted(_PASS_STATE_ATTRS), '_ssaa_factor', '_dirty')

# Setter-shaped methods that are not transactions: the auto-apply toggles change
# no setting, and a frame-time governor calls set_ssaa_factor every frame.
_NOT_TRANSACTIONS = frozenset({'enable_auto_apply', 'disable_auto_apply', 'set_ssaa_factor'})
_TRANSACTION_PREFIXES = ('enable_', 'disable_', 'preset_', 'set_')

# Frames a warning skips so it points at the caller's code.
_INTERNAL_PREFIXES = (str(Path(pv.__file__).parent), str(Path(__file__).parent))


def _warn_and_log(message: str) -> None:
    # Logged every time; the warning is deduplicated per call site by Python.
    logger.error(message)
    warnings.warn(message, RuntimeWarning, skip_file_prefixes=_INTERNAL_PREFIXES)


def _vetoable[F: Callable[..., Any]](method: F) -> F:
    # Commits the setter only if every provider accepts the result; otherwise,
    # and on any other exception, restores the snapshot and re-raises.
    @functools.wraps(method)
    def wrapper(  # numpydoc ignore=GL08
        self: RenderPassComponent, *args: object, **kwargs: object
    ) -> object:
        if self._transaction_depth:
            return method(self, *args, **kwargs)
        snapshot = self._snapshot()
        self._transaction_depth += 1
        try:
            result = method(self, *args, **kwargs)
            self._check_vetoes()
        except BaseException as exc:
            try:
                self._restore(snapshot)
            except Exception as restore_exc:
                msg = (
                    f'{method.__name__} failed ({type(exc).__name__}: {exc}) and the previous '
                    f'settings could not be restored; the component state is inconsistent.'
                )
                raise RuntimeError(msg) from restore_exc
            raise
        finally:
            self._transaction_depth -= 1
        return result

    # The marker is how the test that no setter escapes a transaction finds them.
    wrapper.__vetoable__ = True  # type: ignore[attr-defined]
    return cast('F', wrapper)


def _wrap_setters(cls: type) -> None:
    # Derived from the names, so a new setter, or a subclass override, cannot
    # skip the transaction.
    for name, method in list(vars(cls).items()):
        if (
            name.startswith(_TRANSACTION_PREFIXES)
            and name not in _NOT_TRANSACTIONS
            and callable(method)
            and not getattr(method, '__vetoable__', False)
        ):
            setattr(cls, name, _vetoable(method))


class RenderPassComponent:
    """Render-pass settings for one renderer; ``plotter.render_passes`` hands out the active one.

    Owns the renderer's pass slot, its depth-peeling and FXAA flags, the
    window's multisample count, and hidden-line removal. Setters only record
    flags; :meth:`apply` rebuilds the chain, and auto-apply calls it before the
    next render. Not thread-safe.

    Parameters
    ----------
    plotter : pyvista.BasePlotter
        Plotter whose render window is managed.

    renderer : pyvista.Renderer, optional
        The renderer whose pass slot is owned; the plotter's active one by default.

    Examples
    --------
    >>> import pyvista as pv
    >>> pl = pv.Plotter(off_screen=True)
    >>> _ = pl.add_mesh(pv.Sphere(), opacity=0.5)
    >>> _ = pl.render_passes.enable_depth_peeling().enable_ssao().enable_anti_aliasing()
    >>> pl.render_passes.describe()
    'DepthPeeling(peels=8) → SSAO(r=0.5, derived) → AntiAliasing'

    """

    _depth_peeling: bool
    _depth_peeling_max_peels: int
    _depth_peeling_occlusion_ratio: float
    _edl: bool
    _annotation_bypass: bool
    _ssao: bool
    _ssao_radius: float | None
    _ssao_bias: float | None
    _ssao_kernel_size: int
    _ssao_blur: bool
    _anti_aliasing: bool
    _ssaa_factor: float
    _msaa: bool
    _msaa_samples: int
    _dof: bool
    _dof_auto_focal: bool
    _shadows: bool
    _blur: bool
    _hidden_line_removal: bool

    _dirty: bool
    _auto_apply: bool
    _start_event_tag: int | None
    _top: Any
    _provided: list[vtkRenderPass]
    _providers: dict[str, PassProvider]
    _provider_errors: dict[str, Exception]
    _pending_provider_states: dict[str, Any]
    _transaction_depth: int

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Make a subclass's own setters transactions as well."""
        super().__init_subclass__(**kwargs)
        _wrap_setters(cls)

    def __setattr__(self, name: str, value: object) -> None:
        """Set the attribute; a pass-setting attribute also marks the component dirty."""
        super().__setattr__(name, value)
        if name in _PASS_STATE_ATTRS:
            super().__setattr__('_dirty', True)

    def __init__(self, plotter: pv.BasePlotter, renderer: pv.Renderer | None = None) -> None:
        self._plotter = plotter
        self._renderer = plotter.renderer if renderer is None else renderer
        if plotter.render_window is None:
            msg = 'render_passes needs a plotter with a render window'
            raise RuntimeError(msg)
        self._render_window = plotter.render_window

        self._auto_apply = True
        self._start_event_tag = None
        self._top = None
        self._provided = []
        self._providers = {}
        self._provider_errors = {}
        self._pending_provider_states = {}
        self._transaction_depth = 0
        # Kept across applies: it holds the built passes for release and the
        # derived/explicit split of the SSAO radius.
        self._chain = pvRenderPassChain()
        self._ssaa_factor = DEFAULT_SSAA_FACTOR

        self._render_window.SetMultiSamples(0)
        self.set_state(self.default_state())
        self._register_entry_point_providers()
        # A provider's passes need a build even though no setting changed.
        self._dirty = bool(self._providers)
        self._install_auto_apply()

    # -- Lifecycle ----------------------------------------------------------

    def __plotter_deep_clean__(self) -> None:
        """Release and drop the built chain, then reset every setting.

        After ``close`` the window is gone and there is nothing to release;
        the chain is only forgotten.
        """
        chain = getattr(self, '_chain', None)
        if chain is not None:
            if self._top is not None and self._plotter.render_window is not None:
                self._renderer.SetPass(None)
                chain.ReleaseGraphicsResources(self._plotter.render_window)
                self._release_provided(self._plotter.render_window)
            chain.Forget()
            self._clear_seams()
        self._top = None
        self._provided = []
        self._ssaa_factor = DEFAULT_SSAA_FACTOR
        self._pending_provider_states = {}
        self.set_state(self.default_state())
        self._dirty = bool(self._providers)

    def __plotter_close__(self) -> None:
        """Release the built chain's GPU resources and the auto-apply observer.

        Runs while the render window is still alive, so the passes are
        released with their context rather than destructed holding it.
        """
        self._remove_auto_apply()
        if self._top is not None:
            self._renderer.SetPass(None)
            self._chain.ReleaseGraphicsResources(self._render_window)
            self._release_provided(self._render_window)
        self._chain.Forget()
        self._clear_seams()
        self._top = None
        self._provided = []

    def _clear_seams(self) -> None:
        # The chain holds provider passes in its seams; drop them with the chain.
        self._chain.SetTranslucentPass(None)
        self._chain.SetPostPass(None)
        self._chain.SetOuterPass(None)
        self._chain.SetBasePassProvided(False)

    def _release_provided(self, window: Any) -> None:  # noqa: ANN401
        # Provider-built passes are the component's to release, like the chain's own.
        for pass_ in self._provided:
            pass_.ReleaseGraphicsResources(window)
        self._provided = []

    def _build_provided(
        self, provider: PassProvider, stage: Stage, delegate: vtkRenderPass | None
    ) -> vtkRenderPass | None:
        built = provider.build_pass(stage, self._renderer, self._chain, delegate)
        if built is None or built is delegate:
            return None
        self._provided.append(built)
        return built

    def _provided_pass(self, stage: Stage) -> vtkRenderPass | None:
        provider = next((p for p in self._providers.values() if stage in p.stages), None)
        return None if provider is None else self._build_provided(provider, stage, None)

    def _provided_base(self) -> vtkRenderPass | None:
        # The scene base wrapped by each 'base' provider, innermost first. The
        # base is built as if one is coming, since the build decisions depend on
        # it, and the claim is withdrawn when no provider wrapped anything.
        chain = self._chain
        providers = [p for p in self._providers.values() if 'base' in p.stages]
        chain.SetBasePassProvided(bool(providers))
        if not providers:
            return None
        scene = base = chain.BuildSceneBase()
        for provider in providers:
            base = self._build_provided(provider, 'base', base) or base
        if base is scene:
            chain.SetBasePassProvided(False)
            return None
        return base

    # -- Providers ----------------------------------------------------------

    @property
    def providers(self) -> Mapping[str, PassProvider]:
        """The registered providers by name, in registration order; read-only."""
        return MappingProxyType(self._providers)

    @property
    def provider_errors(self) -> Mapping[str, Exception]:
        """Entry points skipped at creation, keyed ``'name = value'``; read-only."""
        return MappingProxyType(self._provider_errors)

    def add_provider(self, provider: PassProvider) -> Self:
        """Register a provider on this subplot; idempotent per object.

        State restored under the provider's name before it registered is
        applied to it first. Queues a rebuild.

        Parameters
        ----------
        provider : PassProvider
            The provider instance.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        Raises
        ------
        TypeError
            If ``provider`` does not implement ``PassProvider``.

        ValueError
            If its name is empty or taken, a stage is unknown, or a
            single-provider stage already has a provider.

        SettingsVetoedError
            If it refuses the current settings; nothing is registered.

        """
        if not self._accepts_new_provider(provider):
            return self
        name = provider.name
        previous = copy.deepcopy(provider.get_state())
        adopted = False
        if name in self._pending_provider_states:
            try:
                provider.set_state(copy.deepcopy(self._pending_provider_states[name]))
                adopted = True
            except Exception as exc:  # noqa: BLE001  third-party code raises anything
                provider.set_state(previous)
                _warn_and_log(
                    f'Render-pass provider {name!r} refused its restored state, which is kept '
                    f'for a later set_state: {type(exc).__name__}: {exc}'
                )
        if reason := provider.veto(MappingProxyType(self._settings())):
            provider.set_state(previous)
            raise SettingsVetoedError(name, reason)
        if adopted:
            del self._pending_provider_states[name]
        self._providers[name] = provider
        provider.bind(self.invalidate)
        return self.invalidate()

    def _accepts_new_provider(self, provider: object) -> bool:
        # False for a provider already registered; raises for one that cannot be.
        if not isinstance(provider, PassProvider):
            msg = f'{provider!r} does not implement PassProvider.'
            raise TypeError(msg)
        name = provider.name
        if self._providers.get(name) is provider:
            return False
        if not isinstance(name, str) or not name:
            msg = f'A provider name must be a non-empty string, got {name!r}.'
            raise ValueError(msg)
        if name in self._providers:
            msg = f'A render-pass provider named {name!r} is already registered.'
            raise ValueError(msg)
        stages = tuple(provider.stages)
        if unknown := [stage for stage in stages if stage not in STAGES]:
            msg = f'Provider {name!r} names unknown stages {unknown}; expected any of {STAGES}.'
            raise ValueError(msg)
        for stage in stages:
            if stage in SINGLE_PROVIDER_STAGES and any(
                stage in other.stages for other in self._providers.values()
            ):
                msg = f'A {stage!r} provider is already registered; {name!r} cannot add another.'
                raise ValueError(msg)
        return True

    def remove_provider(self, provider: object) -> Self:
        """Unregister a provider; a no-op when absent. Queues a rebuild.

        Its last state stays in :meth:`get_state` and returns to a provider
        registered under the same name.

        Parameters
        ----------
        provider : object
            The provider, its class, or its name.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        for name, registered in list(self._providers.items()):
            by_class = isinstance(provider, type) and type(registered) is provider
            by_name = isinstance(provider, str) and provider == name
            if registered is provider or by_class or by_name:
                self._pending_provider_states[name] = copy.deepcopy(registered.get_state())
                del self._providers[name]
                registered.bind(None)
                self.invalidate()
        return self

    def _register_entry_point_providers(self) -> None:
        # One broken extension must not make every plotter unconstructible, and
        # must not be silent either: log, warn, record, skip.
        for label, provider in iter_entry_point_providers():
            error = provider if isinstance(provider, Exception) else None
            if error is None:
                try:
                    self.add_provider(provider)  # type: ignore[arg-type]
                except Exception as exc:  # noqa: BLE001
                    error = exc
            if error is not None:
                self._provider_errors[label] = error
                _warn_and_log(
                    f'Render-pass provider entry point {label!r} was skipped: '
                    f'{type(error).__name__}: {error}'
                )

    def _live_provider_states(self) -> dict[str, Any]:
        return {name: copy.deepcopy(p.get_state()) for name, p in self._providers.items()}

    def _check_vetoes(self) -> None:
        settings = MappingProxyType(self._settings())
        for name, provider in self._providers.items():
            if reason := provider.veto(settings):
                raise SettingsVetoedError(name, reason)

    def _snapshot(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        attrs = {name: self.__dict__[name] for name in _SNAPSHOT_ATTRS if name in self.__dict__}
        return attrs, self._live_provider_states(), copy.deepcopy(self._pending_provider_states)

    def _restore(self, snapshot: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]) -> None:
        attrs, provider_states, pending = snapshot
        # Straight into __dict__, so the dirty flag comes back as it was.
        self.__dict__.update(attrs)
        if (ssaa := self._chain.GetSsaaPass()) is not None:
            ssaa.SetSupersampleFactor(self._ssaa_factor)
            ssaa.SetPrimitiveScaleFactor(self._ssaa_factor)
        for name, state in provider_states.items():
            if (provider := self._providers.get(name)) is not None:
                provider.set_state(state)
        self._pending_provider_states = pending

    @property
    def plotter(self) -> pv.BasePlotter:
        """The plotter this component operates on."""
        return self._plotter

    @property
    def renderer(self) -> pv.Renderer:
        """The renderer this component manages: the one active at construction."""
        return self._renderer

    @property
    def chain(self) -> pvRenderPassChain:
        """The chain builder; its ``Get*Pass`` accessors expose the built passes."""
        return self._chain

    @property
    def is_active(self) -> bool:
        """Whether the chain this component last built owns the renderer's pass slot."""
        return self._top is not None and self._renderer.GetPass() is self._top

    # -- Auto-apply ---------------------------------------------------------

    def enable_auto_apply(self) -> Self:
        """Rebuild the chain on the next render after a setting changes (the default).

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._auto_apply = True
        return self

    def disable_auto_apply(self) -> Self:
        """Leave rebuilds to explicit :meth:`apply` calls.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._auto_apply = False
        return self

    def invalidate(self) -> Self:
        """Queue a rebuild without changing a setting.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._dirty = True
        return self

    def resync_derived_ssao(self) -> bool:
        """Re-measure the derived SSAO radius against the renderer's bounds now.

        ``apply`` measures it only when a setting changes, and adding data
        changes none, so call this after the scene's bounds settle. A pinned
        radius is never re-derived.

        Returns
        -------
        bool
            Whether a value moved. ``True`` means the chain is stale and wants
            an :meth:`apply`; nothing is rebuilt here.

        """
        return bool(self._chain.SyncDerivedSsao(self._renderer))

    def _install_auto_apply(self) -> None:
        # StartEvent fires before the pass runs, so a chain rebuilt here is
        # honored in the same frame.
        if self._start_event_tag is not None:
            return
        self._start_event_tag = self._renderer.AddObserver('StartEvent', self._on_start_event)

    def _remove_auto_apply(self) -> None:
        tag = self._start_event_tag
        if tag is not None and self._renderer is not None:
            self._renderer.RemoveObserver(tag)
        self._start_event_tag = None

    def _on_start_event(self, *_args: object) -> None:
        # Per render rather than per apply: a prop added after the last apply
        # would otherwise stay untagged and be shaded by EDL.
        if self._annotation_bypass and self._edl:
            tag_scene_annotations(self._renderer)

        if not self._auto_apply or not self._dirty:
            return
        # Retried on every render while it fails, and reported every time.
        try:
            self.apply()
        except Exception as exc:  # noqa: BLE001  must not escape into the render loop
            _warn_and_log(
                f'render_passes auto-apply failed; the renderer runs without the chain until '
                f'it succeeds: {type(exc).__name__}: {exc}. Fix the settings, or call apply() '
                'to see the traceback.'
            )

    # -- Settings -----------------------------------------------------------

    def enable_depth_peeling(self, max_peels: int = 8, occlusion_ratio: float = 0.0) -> Self:
        """Enable order-independent transparency.

        :meth:`apply` picks the implementation: VTK's built-in path when no
        other pass is active, otherwise a manual ``vtkDualDepthPeelingPass``
        in the chain, because the built-in path does not compose with a custom
        pass.

        Parameters
        ----------
        max_peels : int, default: 8
            Maximum number of peels.

        occlusion_ratio : float, default: 0.0
            Stop early once a peel changes fewer than this fraction of the
            pixels. Anything above zero leaves the deepest layers unsorted
            where many surfaces overlap.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._depth_peeling = True
        self._depth_peeling_max_peels = max_peels
        self._depth_peeling_occlusion_ratio = occlusion_ratio
        return self

    def disable_depth_peeling(self) -> Self:
        """Disable depth peeling.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._depth_peeling = False
        return self

    def enable_edl(self) -> Self:
        """Enable eye dome lighting.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._edl = True
        return self

    def disable_edl(self) -> Self:
        """Disable eye dome lighting.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._edl = False
        return self

    def enable_annotation_bypass(self) -> Self:
        """Render tagged annotation props in their own stage after EDL (the default).

        Props tagged on :data:`~pyvista_render_passes.CHANNEL_ANNOTATION`
        (:func:`tag_scene_annotations` tags the recognisable ones) are neither
        shaded by EDL nor read into its depth image. They still depth-test
        against the scene. No effect unless EDL is enabled.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._annotation_bypass = True
        return self

    def disable_annotation_bypass(self) -> Self:
        """Let EDL shade annotation props with the scene.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._annotation_bypass = False
        return self

    def enable_ssao(
        self,
        radius: float | None = None,
        bias: float | None = None,
        kernel_size: int = 256,
        *,
        blur: bool = True,
    ) -> Self:
        """Enable screen-space ambient occlusion. Incompatible with depth of field.

        Parameters
        ----------
        radius : float, optional
            Sampling hemisphere radius in world units, finite and positive.
            ``None`` derives it from the visible scene bounds on every apply,
            which holds the occlusion steady across dataset scales.

        bias : float, optional
            Self-occlusion guard in world units, finite and non-negative.
            ``None`` derives it as a hundredth of the radius in force.

        kernel_size : int, default: 256
            Number of sampling directions.

        blur : bool, default: True
            Blur the occlusion result.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        Raises
        ------
        ValueError
            If depth of field is enabled, or ``radius`` or ``bias`` is unusable.

        """
        if self._dof:
            msg = 'SSAO is incompatible with depth of field; call disable_dof() first.'
            raise ValueError(msg)
        # The chain refuses an unusable radius and keeps its previous one, which
        # would leave get_state() and the render disagreeing. Refuse it here.
        if radius is not None and not (math.isfinite(radius) and radius > 0.0):
            msg = f'SSAO radius must be finite and positive, got {radius!r}.'
            raise ValueError(msg)
        if bias is not None and not (math.isfinite(bias) and bias >= 0.0):
            msg = f'SSAO bias must be finite and non-negative, got {bias!r}.'
            raise ValueError(msg)
        self._ssao = True
        self._ssao_radius = radius
        self._ssao_bias = bias
        self._ssao_kernel_size = kernel_size
        self._ssao_blur = blur
        return self

    def disable_ssao(self) -> Self:
        """Disable screen-space ambient occlusion.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._ssao = False
        return self

    def enable_anti_aliasing(self, factor: float | None = None) -> Self:
        """Enable supersampled anti-aliasing.

        Parameters
        ----------
        factor : float, optional
            Supersample factor, as :meth:`set_ssaa_factor`. ``None`` keeps the
            current one.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._anti_aliasing = True
        if factor is not None:
            self.set_ssaa_factor(factor)
        return self

    def disable_anti_aliasing(self) -> Self:
        """Disable supersampled anti-aliasing.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._anti_aliasing = False
        return self

    def set_ssaa_factor(self, factor: float) -> Self:
        """Set the SSAA supersample factor on the live pass without a rebuild.

        Cheap enough to call every frame. ``PrimitiveScaleFactor`` tracks it so
        point and line widths stay visually constant.

        Parameters
        ----------
        factor : float
            Linear supersample factor per axis, clamped to ``[1.0, 4.0]``.
            A non-finite value is ignored.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        factor = float(factor)
        if not math.isfinite(factor):
            return self
        factor = min(4.0, max(1.0, factor))
        self._ssaa_factor = factor
        ssaa = self._chain.GetSsaaPass()
        if ssaa is not None:
            ssaa.SetSupersampleFactor(factor)
            ssaa.SetPrimitiveScaleFactor(factor)
        return self

    def enable_msaa(self, samples: int = 8) -> Self:
        """Enable hardware multisampling on the render window.

        MSAA corrupts the depth buffer that picking, depth peeling, SSAO and
        EDL read, and has no effect while a custom pass chain is active, so it
        is off by default and :meth:`apply` warns in both cases.

        Parameters
        ----------
        samples : int, default: 8
            Samples per pixel.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._msaa = True
        self._msaa_samples = samples
        return self

    def disable_msaa(self) -> Self:
        """Disable hardware multisampling.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._msaa = False
        return self

    def enable_dof(self, *, automatic_focal_distance: bool = True) -> Self:
        """Enable depth of field. Incompatible with SSAO.

        Parameters
        ----------
        automatic_focal_distance : bool, default: True
            Derive the focal distance from the scene.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        Raises
        ------
        ValueError
            If SSAO is enabled.

        """
        if self._ssao:
            msg = 'Depth of field is incompatible with SSAO; call disable_ssao() first.'
            raise ValueError(msg)
        self._dof = True
        self._dof_auto_focal = automatic_focal_distance
        return self

    def disable_dof(self) -> Self:
        """Disable depth of field.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._dof = False
        return self

    def enable_shadows(self) -> Self:
        """Enable shadow mapping.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._shadows = True
        return self

    def disable_shadows(self) -> Self:
        """Disable shadow mapping.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._shadows = False
        return self

    def enable_blur(self) -> Self:
        """Enable Gaussian blur post-processing.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._blur = True
        return self

    def disable_blur(self) -> Self:
        """Disable Gaussian blur.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._blur = False
        return self

    def enable_hidden_line_removal(self) -> Self:
        """Enable hidden line removal (a renderer property, not a pass).

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._hidden_line_removal = True
        return self

    def disable_hidden_line_removal(self) -> Self:
        """Disable hidden line removal.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._hidden_line_removal = False
        return self

    # -- Apply --------------------------------------------------------------

    def _push_settings(self, chain: pvRenderPassChain) -> None:
        chain.SetDepthPeeling(self._depth_peeling)
        chain.SetDepthPeelingMaximumNumberOfPeels(self._depth_peeling_max_peels)
        chain.SetDepthPeelingOcclusionRatio(self._depth_peeling_occlusion_ratio)
        chain.SetShadows(self._shadows)
        chain.SetEDL(self._edl)
        chain.SetAnnotationBypass(self._annotation_bypass)

        chain.SetSSAO(self._ssao)
        chain.SetSsaoKernelSize(self._ssao_kernel_size)
        chain.SetSsaoBlur(self._ssao_blur)
        if self._ssao_radius is None:
            chain.SetSsaoRadiusToDerived()
        else:
            chain.SetSsaoRadius(self._ssao_radius)
        if self._ssao_bias is None:
            chain.SetSsaoBiasToDerived()
        else:
            chain.SetSsaoBias(self._ssao_bias)
        # The probe renders a frame on a cold context, so only pay for it when
        # something reads the answer.
        if self._ssao:
            depth_format = _ssao_depth_format()
            chain.SetSsaoDepthFormat(-1 if depth_format is None else depth_format)

        chain.SetDepthOfField(self._dof)
        chain.SetDepthOfFieldAutomaticFocalDistance(self._dof_auto_focal)
        chain.SetBlur(self._blur)
        chain.SetAntiAliasing(self._anti_aliasing)
        chain.SetSsaaFactor(self._ssaa_factor)

    def apply(self) -> None:
        """Rebuild the pass chain from the current settings and install it.

        Safe to call repeatedly; the chain is rebuilt from scratch each time
        and the previous one's GPU resources are released first, so it must
        run with the GL context current. Resets the renderer's depth-peeling
        and FXAA flags and the window's multisample count to what the settings
        say.

        Raises
        ------
        SettingsVetoedError
            If a provider refuses the current settings, which happens when a
            provider was changed directly into a state that conflicts with
            them. Nothing is rebuilt.

        """
        self._check_vetoes()
        renderer = self._renderer
        rw = self._render_window
        # Release before SetPass(None) drops the last reference: a pass
        # destructed with live GL handles leaks them until the context dies.
        self._chain.ReleaseGraphicsResources(rw)
        self._release_provided(rw)

        renderer.SetPass(None)
        renderer.SetUseDepthPeeling(False)
        renderer.SetUseFXAA(False)

        chain = self._chain
        self._push_settings(chain)
        # The single-pass seams are set before the build decisions are read.
        chain.SetTranslucentPass(self._provided_pass('translucent'))
        chain.SetPostPass(self._provided_pass('post'))
        chain.SetOuterPass(self._provided_pass('outer'))
        base = self._provided_base()
        has_custom_passes = chain.GetRequiresCustomPassChain()

        if chain.GetUsesBuiltinDepthPeeling():
            renderer.SetUseDepthPeeling(True)
            renderer.SetMaximumNumberOfPeels(self._depth_peeling_max_peels)
            renderer.SetOcclusionRatio(self._depth_peeling_occlusion_ratio)
            rw.SetAlphaBitPlanes(True)
        elif chain.GetUsesManualDepthPeeling():
            rw.SetAlphaBitPlanes(True)

        if self._annotation_bypass and self._edl:
            tag_scene_annotations(renderer)
            if self._shadows:
                logger.warning(
                    'Shadows are not applied to annotation props while EDL is enabled: '
                    'they render in a separate stage with no shadow-map pass. Disable '
                    'EDL, or call disable_annotation_bypass().'
                )

        top = chain.Build(renderer, base)
        self._top = top
        if top is not None:
            renderer.SetPass(top)

        msaa_samples = self._msaa_samples if self._msaa else 0
        if msaa_samples and has_custom_passes:
            logger.warning(
                'MSAA has no effect while custom render passes are active; they render '
                'into their own framebuffers.'
            )
        if msaa_samples and self._depth_peeling:
            logger.warning(
                'MSAA + depth peeling: MSAA corrupts the depth buffer, so transparency '
                'ordering will be wrong.'
            )
        rw.SetMultiSamples(msaa_samples)

        if self._hidden_line_removal:
            renderer.enable_hidden_line_removal()
        else:
            renderer.disable_hidden_line_removal()

        renderer.Modified()
        self._dirty = False
        logger.debug('Render pass pipeline applied: %s', self.describe())

    # -- Presets ------------------------------------------------------------

    def preset_interactive(self) -> Self:
        """Depth peeling and SSAA only; every screen-space effect off.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._depth_peeling = True
        self._anti_aliasing = True
        self._msaa = False
        self._edl = False
        self._ssao = False
        self._dof = False
        self._shadows = False
        self._blur = False
        return self

    def preset_still(self) -> Self:
        """Depth peeling and SSAA on top of the current settings.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._depth_peeling = True
        self._anti_aliasing = True
        return self

    def preset_photo_real(self) -> Self:
        """Depth peeling, SSAO, shadows and SSAA.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        """
        self._depth_peeling = True
        self._ssao = True
        self._anti_aliasing = True
        self._shadows = True
        self._dof = False
        return self

    # -- State --------------------------------------------------------------

    def describe(self) -> str:  # noqa: C901
        """Return a one-line description of the enabled passes.

        Returns
        -------
        str
            Pass names joined by arrows, or ``'(default pipeline)'``.

        """
        parts = []
        if self._depth_peeling:
            parts.append(f'DepthPeeling(peels={self._depth_peeling_max_peels})')
        if self._shadows:
            parts.append('Shadows')
        if self._edl:
            parts.append('EDL(annotations bypassed)' if self._annotation_bypass else 'EDL')
        if self._dof:
            parts.append('DepthOfField')
        if self._blur:
            parts.append('GaussianBlur')
        if self._ssao:
            radius = self._ssao_radius
            suffix = ''
            if radius is None:
                radius = self._chain.GetSsaoRadius()
                suffix = ', derived'
            parts.append(f'SSAO(r={radius:.3g}{suffix})')
        if self._anti_aliasing:
            parts.append('AntiAliasing')
        if self._msaa:
            parts.append(f'MSAA({self._msaa_samples}x)')
        if self._hidden_line_removal:
            parts.append('HiddenLineRemoval')
        return ' → '.join(parts) if parts else '(default pipeline)'

    def get_state(self) -> dict[str, Any]:
        """Return every setting as a JSON-serializable dict.

        ``ssao_radius`` and ``ssao_bias`` are ``None`` while derived, so a
        restored state cannot pin a value nobody chose. ``'providers'`` maps
        each provider's name to its own state, including state restored for a
        provider that is not registered here, so a save cycle does not lose it.

        Returns
        -------
        dict[str, Any]
            Settings keyed as :meth:`default_state`.

        """
        providers = copy.deepcopy(self._pending_provider_states) | self._live_provider_states()
        return {**self._settings(), 'providers': providers}

    def _settings(self) -> dict[str, Any]:
        return {
            'depth_peeling': self._depth_peeling,
            'depth_peeling_max_peels': self._depth_peeling_max_peels,
            'depth_peeling_occlusion_ratio': self._depth_peeling_occlusion_ratio,
            'edl': self._edl,
            'annotation_bypass': self._annotation_bypass,
            'ssao': self._ssao,
            'ssao_radius': self._ssao_radius,
            'ssao_bias': self._ssao_bias,
            'ssao_kernel_size': self._ssao_kernel_size,
            'ssao_blur': self._ssao_blur,
            'anti_aliasing': self._anti_aliasing,
            'ssaa_factor': self._ssaa_factor,
            'msaa': self._msaa,
            'msaa_samples': self._msaa_samples,
            'dof': self._dof,
            'dof_auto_focal': self._dof_auto_focal,
            'shadows': self._shadows,
            'blur': self._blur,
            'hidden_line_removal': self._hidden_line_removal,
        }

    @staticmethod
    def _coerce_ssao_number(value: object, key: str, *, strictly_positive: bool) -> float | None:
        # Unlike enable_ssao this drops a bad number instead of raising: one
        # bad field must not make a whole saved state unloadable.
        if value is None:
            return None
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            number = math.nan
        floor_ok = number > 0.0 if strictly_positive else number >= 0.0
        if not (math.isfinite(number) and floor_ok):
            logger.warning(
                'Ignoring unusable %s=%r in restored render-pass state; deriving it instead.',
                key,
                value,
            )
            return None
        return number

    def set_state(self, state: dict[str, Any]) -> Self:  # noqa: C901, PLR0912
        """Restore settings from a dict, atomically. Unknown keys are ignored.

        State under ``'providers'`` for a name with no registered provider is
        kept rather than dropped or refused: :meth:`get_state` reports it, and
        a provider registering under that name later receives it. A saved
        state stays loadable on a machine without the extension that wrote it.

        Parameters
        ----------
        state : dict[str, Any]
            Dict as returned by :meth:`get_state`; missing keys keep their
            current value.

        Returns
        -------
        RenderPassComponent
            Self for chaining.

        Raises
        ------
        ValueError
            If the state enables both SSAO and depth of field.

        TypeError
            If ``state['providers']`` is not a mapping.

        SettingsVetoedError
            If a provider refuses the resulting settings.

        """
        if 'depth_peeling_max_peels' in state:
            self._depth_peeling_max_peels = int(state['depth_peeling_max_peels'])
        if 'depth_peeling_occlusion_ratio' in state:
            self._depth_peeling_occlusion_ratio = float(state['depth_peeling_occlusion_ratio'])
        if 'ssao_radius' in state:
            self._ssao_radius = self._coerce_ssao_number(
                state['ssao_radius'], 'ssao_radius', strictly_positive=True
            )
        if 'ssao_bias' in state:
            self._ssao_bias = self._coerce_ssao_number(
                state['ssao_bias'], 'ssao_bias', strictly_positive=False
            )
        if 'ssao_kernel_size' in state:
            self._ssao_kernel_size = int(state['ssao_kernel_size'])
        if 'ssao_blur' in state:
            self._ssao_blur = bool(state['ssao_blur'])
        if 'ssaa_factor' in state:
            self.set_ssaa_factor(state['ssaa_factor'])
        if 'msaa_samples' in state:
            self._msaa_samples = int(state['msaa_samples'])
        if 'dof_auto_focal' in state:
            self._dof_auto_focal = bool(state['dof_auto_focal'])

        for key in (
            'depth_peeling',
            'edl',
            'annotation_bypass',
            'anti_aliasing',
            'msaa',
            'dof',
            'shadows',
            'blur',
            'hidden_line_removal',
        ):
            if key in state:
                setattr(self, f'_{key}', bool(state[key]))

        # Through enable_ssao so the DOF check and the number validation run.
        if 'ssao' in state:
            if state['ssao']:
                self.enable_ssao(
                    radius=self._ssao_radius,
                    bias=self._ssao_bias,
                    kernel_size=self._ssao_kernel_size,
                    blur=self._ssao_blur,
                )
            else:
                self._ssao = False

        provider_states = state.get('providers')
        if provider_states:
            if not isinstance(provider_states, Mapping):
                msg = (
                    "state['providers'] must map provider names to states, "
                    f'got {type(provider_states).__name__}.'
                )
                raise TypeError(msg)
            for name, provider_state in provider_states.items():
                if (provider := self._providers.get(name)) is None:
                    self._pending_provider_states[name] = copy.deepcopy(provider_state)
                else:
                    # Deep-copied like the pending branch: a provider that keeps
                    # the reference must not alias the caller's dict.
                    provider.set_state(copy.deepcopy(provider_state))
            self.invalidate()
        return self

    def default_state(self) -> dict[str, Any]:
        """Return the default settings; the key set :meth:`get_state` uses.

        Returns
        -------
        dict[str, Any]
            Defaults for every setting, with each registered provider's
            defaults under ``'providers'``.

        """
        providers = {name: p.default_state() for name, p in self._providers.items()}
        return {**self._default_settings(), 'providers': providers}

    @staticmethod
    def _default_settings() -> dict[str, Any]:
        return {
            'depth_peeling': False,
            'depth_peeling_max_peels': 8,
            'depth_peeling_occlusion_ratio': 0.0,
            'edl': False,
            'annotation_bypass': True,
            'ssao': False,
            'ssao_radius': None,
            'ssao_bias': None,
            'ssao_kernel_size': 256,
            'ssao_blur': True,
            'anti_aliasing': False,
            'ssaa_factor': DEFAULT_SSAA_FACTOR,
            'msaa': False,
            'msaa_samples': 8,
            'dof': False,
            'dof_auto_focal': True,
            'shadows': False,
            'blur': False,
            'hidden_line_removal': False,
        }


# Every setter is a transaction a provider can refuse.
_wrap_setters(RenderPassComponent)


@pv.register_plotter_component('render_passes')
class RenderPasses:
    """``plotter.render_passes``: the component of whichever subplot is active.

    Every attribute lookup forwards to the :class:`RenderPassComponent` of
    ``plotter.renderer``. Every subplot's component is built with this object,
    so ``pl.subplot(0, 1)`` followed by ``pl.render_passes.enable_edl()``
    configures that subplot and no other. Each subplot keeps its own settings
    and chain.

    Parameters
    ----------
    plotter : pyvista.BasePlotter
        Plotter whose subplots are managed.

    Examples
    --------
    >>> import pyvista as pv
    >>> pl = pv.Plotter(off_screen=True, shape=(1, 2))
    >>> _ = pl.render_passes.enable_edl()
    >>> pl.subplot(0, 1)
    >>> _ = pl.render_passes.enable_ssao()
    >>> [c.describe() for c in pl.render_passes.components]
    ['EDL(annotations bypassed)', 'SSAO(r=0.5, derived)']

    """

    def __init__(self, plotter: pv.BasePlotter) -> None:
        self._plotter = plotter
        self._components: dict[pv.Renderer, RenderPassComponent] = {}
        # Every subplot's component exists as soon as this one does, so the
        # window-level effects (multisamples off) hold from first touch and an
        # installed provider composes into subplots nobody configured.
        for renderer in plotter.renderers:
            self._components[renderer] = RenderPassComponent(plotter, renderer)

    @property
    def active(self) -> RenderPassComponent:
        """The component of the active subplot."""
        renderer = self._plotter.renderer
        component = self._components.get(renderer)
        if component is None:
            component = self._components[renderer] = RenderPassComponent(self._plotter, renderer)
        return component

    @property
    def components(self) -> tuple[RenderPassComponent, ...]:
        """One component per subplot, in creation order, configured or not."""
        return tuple(self._components.values())

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        """Forward to the active subplot's component."""
        if name.startswith('__'):
            raise AttributeError(name)
        return getattr(self.active, name)

    def __plotter_close__(self) -> None:
        """Close every subplot's component."""
        for component in self._components.values():
            component.__plotter_close__()

    def __plotter_deep_clean__(self) -> None:
        """Reset every subplot's component."""
        for component in self._components.values():
            component.__plotter_deep_clean__()
