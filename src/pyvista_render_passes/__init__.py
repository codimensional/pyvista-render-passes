"""Render pass chain for PyVista."""

from __future__ import annotations

from importlib.metadata import version as _dist_version

from ._backend import VARIANT, backend_module
from .component import RenderPassComponent, RenderPasses, tag_scene_annotations
from .passes import (
    CHANNEL_ANNOTATION,
    DEFAULT_SSAA_FACTOR,
    clear_prop_filter_tag,
    enable_ssaa,
    enable_ssao,
    has_prop_filter_tag,
    make_prop_filter_pass,
    make_split_pass,
    make_ssaa_pass,
    make_ssao_pass,
    prop_filter_tag_is_set,
    pvPropKeyFilterPass,
    pvRenderPassChain,
    pvSSAAVolumePass,
    reserve_prop_filter_channel,
    set_prop_filter_tag,
)
from .providers import (
    ENTRY_POINT_GROUP,
    BasePassProvider,
    PassProvider,
    SettingsVetoedError,
    register_pass_provider,
    unregister_pass_provider,
)

__version__ = _dist_version('pyvista-render-passes')

#: Which build is loaded: ``'cvista'`` or ``'vtk'``.
BACKEND = VARIANT

__all__ = [
    'BACKEND',
    'CHANNEL_ANNOTATION',
    'DEFAULT_SSAA_FACTOR',
    'ENTRY_POINT_GROUP',
    'BasePassProvider',
    'PassProvider',
    'RenderPassComponent',
    'RenderPasses',
    'SettingsVetoedError',
    '__version__',
    'backend_module',
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
    'register_pass_provider',
    'reserve_prop_filter_channel',
    'set_prop_filter_tag',
    'tag_scene_annotations',
    'unregister_pass_provider',
]
