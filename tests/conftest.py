"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType

import pytest
import pyvista as pv

pv.OFF_SCREEN = True

REPO = Path(__file__).resolve().parent.parent


def load_script(name: str) -> ModuleType:
    """Import ``scripts/<name>.py``, which belongs to no importable package."""
    if (loaded := sys.modules.get(name)) is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / f'{name}.py')
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _reset_theme() -> Iterator[None]:
    pv.global_theme.load_theme(pv.plotting.themes._TestingTheme())
    yield
    pv.global_theme.load_theme(pv.plotting.themes._TestingTheme())


@pytest.fixture(autouse=True)
def _close_plotters() -> Iterator[None]:
    yield
    # Passes installed by hand hold GL objects the plotter never releases.
    for pl in list(pv.plotting.plotter._ALL_PLOTTERS.values()):
        if pl.render_window is None:
            continue
        for renderer in pl.renderers:
            if (render_pass := renderer.GetPass()) is not None:
                render_pass.ReleaseGraphicsResources(pl.render_window)
    pv.close_all()


@contextlib.contextmanager
def vtk_stderr() -> Iterator[dict[str, str]]:
    """Capture VTK's C++ log, which bypasses ``capsys``; ``['text']`` fills on exit."""
    sink = {'text': ''}
    with tempfile.TemporaryFile(mode='w+') as tmp:
        saved = os.dup(2)
        sys.stderr.flush()
        os.dup2(tmp.fileno(), 2)
        try:
            yield sink
        finally:
            sys.stderr.flush()
            os.dup2(saved, 2)
            os.close(saved)
            tmp.seek(0)
            sink['text'] = tmp.read()
