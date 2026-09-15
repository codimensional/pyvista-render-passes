"""Backend selection: the loaded build matches the VTK distribution PyVista uses."""

from __future__ import annotations

import importlib
import os

import pyvista as pv

import pyvista_render_passes as prp
from pyvista_render_passes import _backend


def test_loaded_build_matches_pyvista_backend():
    expected = 'cvista' if pv.vtk_backend() == 'cvista' else 'vtk'
    assert expected == prp.BACKEND
    variant = importlib.import_module(f'pyvista_render_passes._{prp.BACKEND}')
    assert variant.BACKEND == prp.BACKEND


def test_env_override_is_honored():
    requested = os.environ.get('PYVISTA_VTK_BACKEND')
    if requested:
        assert ('vtkmodules' if requested == 'vtk' else requested) == _backend.ROOT


def test_build_and_runtime_agree_on_the_vtk_version():
    variant = importlib.import_module(f'pyvista_render_passes._{prp.BACKEND}')
    running = prp.backend_module('vtkCommonCore').vtkVersion.GetVTKVersion()
    assert variant.VTK_VERSION.split('.')[:2] == running.split('.')[:2]


def test_backend_module_resolves_off_the_active_root():
    module = prp.backend_module('vtkRenderingOpenGL2')
    assert module.__name__ == f'{_backend.ROOT}.vtkRenderingOpenGL2'
    assert isinstance(prp.pvRenderPassChain(), prp.backend_module('vtkCommonCore').vtkObject)
