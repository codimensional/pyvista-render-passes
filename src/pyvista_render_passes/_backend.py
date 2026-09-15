"""Resolve which VTK distribution PyVista runs on and load the matching build.

The wheel carries the passes compiled against stock ``vtk`` (``_vtk``) and
against ``cvista`` (``_cvista``). The choice mirrors PyVista's own:
``PYVISTA_VTK_BACKEND`` if set, else ``cvista`` when installed, else stock.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any


def _resolve_root() -> str:
    backend = os.environ.get('PYVISTA_VTK_BACKEND')
    if backend:
        return 'vtkmodules' if backend == 'vtk' else backend
    if importlib.util.find_spec('cvista') is not None:
        return 'cvista'
    return 'vtkmodules'


ROOT = _resolve_root()
VARIANT = 'cvista' if ROOT == 'cvista' else 'vtk'


def backend_module(name: str) -> ModuleType:
    """Import a VTK module from the distribution PyVista is running on.

    Parameters
    ----------
    name : str
        Module name below the distribution root, e.g. ``'vtkRenderingOpenGL2'``.

    Returns
    -------
    types.ModuleType
        ``cvista.<name>`` or ``vtkmodules.<name>``, whichever is active.

    """
    return importlib.import_module(f'{ROOT}.{name}')


def _load_extension() -> ModuleType:
    root = importlib.import_module(ROOT)
    if sys.platform == 'win32':  # pragma: no cover
        os.add_dll_directory(os.path.dirname(root.__file__))  # noqa: PTH120
    backend_module('vtkRenderingOpenGL2')
    try:
        return importlib.import_module(f'pyvista_render_passes._{VARIANT}.PyVistaRenderPasses')
    except ImportError as exc:
        msg = (
            f'pyvista_render_passes has no build for the {ROOT!r} backend on this '
            f'platform ({exc}). Install cvista, or set PYVISTA_VTK_BACKEND to a '
            'backend this wheel was built for.'
        )
        raise ImportError(msg) from exc


_ext = _load_extension()
_common = backend_module('vtkCommonCore')
_rendering = backend_module('vtkRenderingCore')
_annotation = backend_module('vtkRenderingAnnotation')
_opengl = backend_module('vtkRenderingOpenGL2')

pvPropKeyFilterPass: Any = _ext.pvPropKeyFilterPass
pvRenderPassChain: Any = _ext.pvRenderPassChain
pvSSAAVolumePass: Any = _ext.pvSSAAVolumePass

if TYPE_CHECKING:
    from vtkmodules.vtkRenderingAnnotation import (
        vtkAnnotatedCubeActor,
        vtkAxesActor,
        vtkCubeAxesActor,
        vtkLegendScaleActor,
    )
    from vtkmodules.vtkRenderingCore import vtkActor2D, vtkProp, vtkRenderPass
    from vtkmodules.vtkRenderingOpenGL2 import (
        vtkCameraPass,
        vtkRenderPassCollection,
        vtkRenderStepsPass,
        vtkSequencePass,
        vtkSSAOPass,
        vtkTextureObject,
    )
else:
    vtkAnnotatedCubeActor = _annotation.vtkAnnotatedCubeActor
    vtkAxesActor = _annotation.vtkAxesActor
    vtkCubeAxesActor = _annotation.vtkCubeAxesActor
    vtkLegendScaleActor = _annotation.vtkLegendScaleActor
    vtkActor2D = _rendering.vtkActor2D
    vtkProp = _rendering.vtkProp
    vtkRenderPass = _rendering.vtkRenderPass
    vtkCameraPass = _opengl.vtkCameraPass
    vtkRenderPassCollection = _opengl.vtkRenderPassCollection
    vtkRenderStepsPass = _opengl.vtkRenderStepsPass
    vtkSequencePass = _opengl.vtkSequencePass
    vtkSSAOPass = _opengl.vtkSSAOPass
    vtkTextureObject = _opengl.vtkTextureObject

__all__ = [
    'ROOT',
    'VARIANT',
    'backend_module',
    'pvPropKeyFilterPass',
    'pvRenderPassChain',
    'pvSSAAVolumePass',
    'vtkActor2D',
    'vtkAnnotatedCubeActor',
    'vtkAxesActor',
    'vtkCameraPass',
    'vtkCubeAxesActor',
    'vtkLegendScaleActor',
    'vtkProp',
    'vtkRenderPass',
    'vtkRenderPassCollection',
    'vtkRenderStepsPass',
    'vtkSSAOPass',
    'vtkSequencePass',
    'vtkTextureObject',
]
