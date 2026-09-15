"""Passes from other packages, composed into the chain by the component.

A provider registers itself on a plotter; :class:`RenderPassComponent`
composes whatever is registered when it rebuilds, at one of three seams of
:class:`pvRenderPassChain`. ``'translucent'`` replaces the translucent stage
(one provider at most), ``'base'`` wraps the scene base below every
screen-space pass (any number, innermost first), ``'post'`` wraps the shaded
frame below SSAA (one at most). The component releases what a provider builds.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, Protocol, overload, runtime_checkable

if TYPE_CHECKING:
    from pyvista import Renderer
    from pyvista.plotting.plotter import BasePlotter

    from ._backend import pvRenderPassChain, vtkRenderPass

__all__ = [
    'PassProvider',
    'Stage',
    'pass_providers',
    'register_pass_provider',
    'unregister_pass_provider',
]

Stage = Literal['base', 'translucent', 'post']
BuildPass = Callable[
    ['Renderer', 'pvRenderPassChain', 'vtkRenderPass | None'], 'vtkRenderPass | None'
]


@runtime_checkable
class PassProvider(Protocol):
    """A package that contributes one pass to the chain.

    Examples
    --------
    >>> from pyvista_render_passes.providers import PassProvider
    >>> class Passthrough:
    ...     stage = 'base'
    ...
    ...     def build_pass(self, renderer, chain, delegate):
    ...         return delegate
    >>> isinstance(Passthrough(), PassProvider)
    True

    """

    stage: Stage

    def build_pass(
        self, renderer: Renderer, chain: pvRenderPassChain, delegate: vtkRenderPass | None
    ) -> vtkRenderPass | None:
        """Build the pass for one chain rebuild.

        Parameters
        ----------
        renderer : pyvista.Renderer
            Renderer the chain is being built for.

        chain : pvRenderPassChain
            The chain, for its settings (peel count, SSAA factor, ...).

        delegate : vtkRenderPass, optional
            For the ``'base'`` stage, the pass the result must render; ``None``
            for the other stages, whose delegate the chain wires itself.

        Returns
        -------
        vtkRenderPass or None
            The pass, or ``None`` (or ``delegate``) to contribute nothing.

        """
        ...


class _FunctionProvider:
    def __init__(self, stage: Stage, build_pass: BuildPass) -> None:
        self.stage = stage
        self.build_pass = build_pass


def _registry(plotter: BasePlotter) -> list[PassProvider]:
    # The component holds the providers, so a provider that holds its plotter
    # is an ordinary reference cycle rather than a pinned registry key.
    return plotter.render_passes._providers  # noqa: SLF001


def _register(plotter: BasePlotter, provider: PassProvider) -> None:
    registry = _registry(plotter)
    if any(existing is provider for existing in registry):
        return
    if provider.stage != 'base' and any(p.stage == provider.stage for p in registry):
        msg = f'A {provider.stage!r} provider is already registered on this plotter.'
        raise ValueError(msg)
    registry.append(provider)
    plotter.render_passes.invalidate()


@overload
def register_pass_provider[T](
    plotter: BasePlotter, provider: None = None, *, stage: Stage | None = None
) -> Callable[[T], T]: ...  # numpydoc ignore=GL08
@overload
def register_pass_provider[T](
    plotter: BasePlotter, provider: T, *, stage: Stage | None = None
) -> T: ...  # numpydoc ignore=GL08
def register_pass_provider[T](
    plotter: BasePlotter, provider: T | None = None, *, stage: Stage | None = None
) -> T | Callable[[T], T]:
    """Register a provider on ``plotter``; idempotent per object, ordered.

    Takes a provider object, a provider class (instantiated with no
    arguments) or a bare ``build_pass`` function with ``stage`` given, and
    works as a decorator when ``provider`` is omitted.

    Parameters
    ----------
    plotter : pyvista.plotting.plotter.BasePlotter
        Plotter whose active subplot composes the provider.

    provider : PassProvider | type[PassProvider] | callable, optional
        The provider, its class, or a ``build_pass(renderer, chain, delegate)``
        function. Omit to get a decorator.

    stage : {'base', 'translucent', 'post'}, optional
        The stage, required for and only accepted with a function.

    Returns
    -------
    object
        ``provider`` unchanged, or the decorator when it was omitted.

    Raises
    ------
    TypeError
        If ``stage`` is missing for a function or given for anything else.

    ValueError
        If the stage already has a provider and admits only one.

    Examples
    --------
    >>> import pyvista as pv
    >>> from pyvista_render_passes import providers
    >>> pl = pv.Plotter(off_screen=True)
    >>> @providers.register_pass_provider(pl)
    ... class Passthrough:
    ...     stage = 'base'
    ...
    ...     def build_pass(self, renderer, chain, delegate):
    ...         return delegate
    >>> @providers.register_pass_provider(pl, stage='post')
    ... def nothing(renderer, chain, delegate):
    ...     return None
    >>> [p.stage for p in providers.pass_providers(pl)]
    ['base', 'post']

    """
    if provider is None:

        def decorator(target: T) -> T:
            return register_pass_provider(plotter, target, stage=stage)

        return decorator
    registered: PassProvider
    if isinstance(provider, type | PassProvider):
        if stage is not None:
            msg = 'stage applies only to a build_pass function'
            raise TypeError(msg)
        registered = provider() if isinstance(provider, type) else provider
    elif callable(provider):
        if stage is None:
            msg = 'stage is required when registering a build_pass function'
            raise TypeError(msg)
        registered = _FunctionProvider(stage, provider)
    else:
        msg = f'{provider!r} is not a provider, a provider class or a build_pass function'
        raise TypeError(msg)
    _register(plotter, registered)
    return provider


def unregister_pass_provider(plotter: BasePlotter, provider: object) -> None:
    """Remove ``provider`` from ``plotter``; a no-op when absent.

    Parameters
    ----------
    plotter : pyvista.plotting.plotter.BasePlotter
        Plotter the provider was registered on.

    provider : object
        What was passed to :func:`register_pass_provider`: the provider, its
        class, or the ``build_pass`` function.

    Examples
    --------
    >>> import pyvista as pv
    >>> from pyvista_render_passes import providers
    >>> class Passthrough:
    ...     stage = 'base'
    ...
    ...     def build_pass(self, renderer, chain, delegate):
    ...         return delegate
    >>> pl = pv.Plotter(off_screen=True)
    >>> provider = Passthrough()
    >>> providers.register_pass_provider(pl, provider) is provider
    True
    >>> providers.unregister_pass_provider(pl, provider)
    >>> providers.pass_providers(pl)
    ()

    """
    registry = _registry(plotter)
    registry[:] = [p for p in registry if not _registered_as(p, provider)]
    plotter.render_passes.invalidate()


def _registered_as(registered: PassProvider, provider: object) -> bool:
    if registered is provider:
        return True
    if isinstance(provider, type):
        return type(registered) is provider
    return isinstance(registered, _FunctionProvider) and registered.build_pass is provider


def pass_providers(plotter: BasePlotter) -> tuple[PassProvider, ...]:
    """Return the providers on ``plotter`` in registration order.

    Parameters
    ----------
    plotter : pyvista.plotting.plotter.BasePlotter
        Plotter to look up.

    Returns
    -------
    tuple[PassProvider, ...]
        Registered providers; empty when none.

    Examples
    --------
    >>> import pyvista as pv
    >>> from pyvista_render_passes import providers
    >>> providers.pass_providers(pv.Plotter(off_screen=True))
    ()

    """
    return tuple(_registry(plotter))
