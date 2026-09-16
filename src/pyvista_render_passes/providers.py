"""Passes, settings and setting vetoes from other packages, composed by the component.

A provider is one named unit of an extension: passes at any of the stages in
:data:`STAGES`, settings nested under ``get_state()['providers'][name]``, and
an optional veto over component settings. Installed packages expose providers
through the :data:`ENTRY_POINT_GROUP` entry-point group; every component
instantiates its own when it is created. A failing entry point warns and is
skipped. ``docs/design.md`` covers the seams and the failure policy.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, overload, runtime_checkable

if TYPE_CHECKING:
    from pyvista import Renderer
    from pyvista.plotting.plotter import BasePlotter

    from ._backend import pvRenderPassChain, vtkRenderPass

__all__ = [
    'ENTRY_POINT_GROUP',
    'SINGLE_PROVIDER_STAGES',
    'STAGES',
    'BasePassProvider',
    'PassProvider',
    'SettingsVetoedError',
    'Stage',
    'iter_entry_point_providers',
    'register_pass_provider',
    'unregister_pass_provider',
]

#: Entry-point group scanned when a component is created.
ENTRY_POINT_GROUP = 'pyvista_render_passes.providers'

Stage = Literal['base', 'translucent', 'post', 'outer']

#: Every stage, innermost first.
STAGES: tuple[Stage, ...] = ('base', 'translucent', 'post', 'outer')

#: Stages whose chain seam holds one pass, so one provider.
SINGLE_PROVIDER_STAGES: frozenset[Stage] = frozenset({'translucent', 'post', 'outer'})


class SettingsVetoedError(ValueError):
    """A provider refused a component setting; the component state is unchanged.

    Parameters
    ----------
    provider : str
        Name of the provider that refused.

    reason : str
        The provider's explanation.

    """

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f'Render-pass provider {provider!r} refused the settings: {reason}')


@runtime_checkable
class PassProvider(Protocol):
    """One named unit of an extension: passes, settings and setting vetoes.

    :class:`BasePassProvider` implements every member but ``name`` as a no-op.

    Examples
    --------
    >>> from pyvista_render_passes import BasePassProvider, PassProvider
    >>> class Passthrough(BasePassProvider):
    ...     name = 'passthrough'
    ...     stages = ('base',)
    ...
    ...     def build_pass(self, stage, renderer, chain, delegate):
    ...         return delegate
    >>> isinstance(Passthrough(), PassProvider)
    True

    """

    name: str
    stages: tuple[Stage, ...]

    def build_pass(
        self,
        stage: Stage,
        renderer: Renderer,
        chain: pvRenderPassChain,
        delegate: vtkRenderPass | None,
    ) -> vtkRenderPass | None:
        """Build the pass for one stage of one chain rebuild.

        Parameters
        ----------
        stage : {'base', 'translucent', 'post', 'outer'}
            One of ``stages``; called once for each per rebuild.

        renderer : pyvista.Renderer
            Renderer the chain is being built for.

        chain : pvRenderPassChain
            The chain, for its settings (peel count, SSAA factor, ...).

        delegate : vtkRenderPass, optional
            For ``'base'``, the pass the result must render; ``None`` for the
            other stages, whose delegate the chain wires itself.

        Returns
        -------
        vtkRenderPass or None
            A new pass on every call, or ``None`` (or ``delegate``) to
            contribute nothing. Never a cached instance: the component releases
            it on the next rebuild, and a pass shared across subplots is wired
            into two chains at once. ``'post'`` and ``'outer'`` need a
            ``vtkImageProcessingPass``.

        """
        ...

    def bind(self, invalidate: Callable[[], object] | None) -> None:
        """Receive the handle that queues a rebuild, or ``None`` when removed.

        A setting changed through the component's ``set_state`` rebuilds on
        its own; call the handle after changing one any other way.

        Parameters
        ----------
        invalidate : callable or None
            The owning component's ``invalidate``.

        """
        ...

    def default_state(self) -> dict[str, Any]:
        """Return the provider's default settings.

        State is plain JSON (dicts, lists, strings, finite numbers, booleans,
        ``None``): the component deep-copies it and the host saves it.

        Returns
        -------
        dict[str, Any]
            JSON-serializable settings.

        """
        ...

    def get_state(self) -> dict[str, Any]:
        """Return the provider's current settings.

        Returns
        -------
        dict[str, Any]
            JSON-serializable settings, keyed as ``default_state``.

        """
        ...

    def set_state(self, state: Mapping[str, Any]) -> None:
        """Restore settings; missing keys keep their current value.

        Parameters
        ----------
        state : Mapping[str, Any]
            Settings as returned by ``get_state``.

        """
        ...

    def veto(self, settings: Mapping[str, Any]) -> str | None:
        """Refuse component settings before they take effect.

        Parameters
        ----------
        settings : Mapping[str, Any]
            The component settings about to be committed, keyed as
            ``RenderPassComponent.get_state()`` without ``'providers'``.

        Returns
        -------
        str or None
            Why the settings are refused, or ``None`` to accept them.

        """
        ...


class BasePassProvider:
    """No-op implementation of every :class:`PassProvider` member but ``name``.

    Examples
    --------
    A settings-only provider that needs the depth buffer single-sampled.

    >>> import pyvista as pv
    >>> from pyvista_render_passes import BasePassProvider, register_pass_provider
    >>> class DepthReader(BasePassProvider):
    ...     name = 'depth_reader'
    ...
    ...     def veto(self, settings):
    ...         return 'reads back depth, so MSAA must stay off' if settings['msaa'] else None
    >>> pl = pv.Plotter(off_screen=True)
    >>> _ = register_pass_provider(pl, DepthReader)
    >>> pl.render_passes.enable_msaa()
    Traceback (most recent call last):
    ...
    pyvista_render_passes.providers.SettingsVetoedError: Render-pass provider 'depth_reader' refused the settings: reads back depth, so MSAA must stay off

    """  # noqa: E501

    name: ClassVar[str]
    stages: tuple[Stage, ...] = ()
    _invalidate: Callable[[], object] | None = None

    def build_pass(
        self,
        stage: Stage,  # noqa: ARG002
        renderer: Renderer,  # noqa: ARG002
        chain: pvRenderPassChain,  # noqa: ARG002
        delegate: vtkRenderPass | None,  # noqa: ARG002
    ) -> vtkRenderPass | None:
        """Contribute nothing.

        Parameters
        ----------
        stage : {'base', 'translucent', 'post', 'outer'}
            Unused.

        renderer : pyvista.Renderer
            Unused.

        chain : pvRenderPassChain
            Unused.

        delegate : vtkRenderPass, optional
            Unused.

        Returns
        -------
        None
            Always.

        """
        return None

    def bind(self, invalidate: Callable[[], object] | None) -> None:
        """Store the rebuild handle for :meth:`invalidate`.

        Parameters
        ----------
        invalidate : callable or None
            The owning component's ``invalidate``.

        """
        self._invalidate = invalidate

    def invalidate(self) -> None:
        """Queue a rebuild of the owning component; a no-op while unregistered."""
        if self._invalidate is not None:
            self._invalidate()

    def default_state(self) -> dict[str, Any]:
        """Return no settings.

        Returns
        -------
        dict[str, Any]
            An empty dict.

        """
        return {}

    def get_state(self) -> dict[str, Any]:
        """Return no settings.

        Returns
        -------
        dict[str, Any]
            An empty dict.

        """
        return {}

    def set_state(self, state: Mapping[str, Any]) -> None:
        """Ignore the state.

        Parameters
        ----------
        state : Mapping[str, Any]
            Unused.

        """

    def veto(self, settings: Mapping[str, Any]) -> str | None:  # noqa: ARG002
        """Accept every setting.

        Parameters
        ----------
        settings : Mapping[str, Any]
            Unused.

        Returns
        -------
        None
            Always.

        """
        return None


def iter_entry_point_providers() -> Iterator[tuple[str, PassProvider | Exception]]:
    """Instantiate every provider in :data:`ENTRY_POINT_GROUP`.

    Yields
    ------
    tuple[str, PassProvider | Exception]
        ``('name = value', provider)`` per entry point, with the exception in
        place of the provider for one that failed to load or instantiate.

    """
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        label = f'{entry_point.name} = {entry_point.value}'
        try:
            yield label, entry_point.load()()
        except Exception as exc:  # noqa: BLE001  third-party import code raises anything
            yield label, exc


@overload
def register_pass_provider[T](
    plotter: BasePlotter, provider: None = None
) -> Callable[[T], T]: ...  # numpydoc ignore=GL08
@overload
def register_pass_provider[T](plotter: BasePlotter, provider: T) -> T: ...  # numpydoc ignore=GL08
def register_pass_provider[T](
    plotter: BasePlotter, provider: T | None = None
) -> T | Callable[[T], T]:
    """Register a provider on the active subplot by hand; idempotent per object.

    For a provider scoped to one plotter; an installed extension uses the
    :data:`ENTRY_POINT_GROUP` entry points instead. State restored under the
    provider's name before it was registered is applied to it now.

    Parameters
    ----------
    plotter : pyvista.plotting.plotter.BasePlotter
        Plotter whose active subplot composes the provider.

    provider : PassProvider | type[PassProvider], optional
        The provider, or its class (instantiated with no arguments). Omit to
        get a class decorator.

    Returns
    -------
    object
        ``provider`` unchanged, or the decorator when it was omitted.

    Raises
    ------
    TypeError
        If ``provider`` does not implement :class:`PassProvider`.

    ValueError
        If its name is taken, a stage is unknown, or a single-provider stage
        already has a provider.

    SettingsVetoedError
        If it refuses the component's current settings.

    Examples
    --------
    >>> import pyvista as pv
    >>> from pyvista_render_passes import BasePassProvider, register_pass_provider
    >>> pl = pv.Plotter(off_screen=True)
    >>> @register_pass_provider(pl)
    ... class Passthrough(BasePassProvider):
    ...     name = 'passthrough'
    ...     stages = ('base',)
    ...
    ...     def build_pass(self, stage, renderer, chain, delegate):
    ...         return delegate
    >>> list(pl.render_passes.providers)
    ['passthrough']

    """
    if provider is None:

        def decorator(target: T) -> T:
            return register_pass_provider(plotter, target)

        return decorator
    instance = provider() if isinstance(provider, type) else provider
    plotter.render_passes.add_provider(instance)
    return provider


def unregister_pass_provider(plotter: BasePlotter, provider: object) -> None:
    """Remove a provider from the active subplot; a no-op when absent.

    Parameters
    ----------
    plotter : pyvista.plotting.plotter.BasePlotter
        Plotter the provider was registered on.

    provider : object
        The provider, its class, or its name.

    Examples
    --------
    >>> import pyvista as pv
    >>> from pyvista_render_passes import (
    ...     BasePassProvider,
    ...     register_pass_provider,
    ...     unregister_pass_provider,
    ... )
    >>> class Passthrough(BasePassProvider):
    ...     name = 'passthrough'
    >>> pl = pv.Plotter(off_screen=True)
    >>> _ = register_pass_provider(pl, Passthrough)
    >>> unregister_pass_provider(pl, 'passthrough')
    >>> 'passthrough' in pl.render_passes.providers
    False

    """
    plotter.render_passes.remove_provider(provider)
