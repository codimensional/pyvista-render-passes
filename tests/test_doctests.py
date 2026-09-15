"""Run the docstring examples of the installed package."""

from __future__ import annotations

import doctest

import pytest

import pyvista_render_passes
from pyvista_render_passes import component, passes


@pytest.mark.parametrize('module', [pyvista_render_passes, component, passes])
def test_docstring_examples(module):
    result = doctest.testmod(module, optionflags=doctest.NORMALIZE_WHITESPACE)
    assert result.failed == 0, result
    assert result.attempted > 0 or module is pyvista_render_passes
