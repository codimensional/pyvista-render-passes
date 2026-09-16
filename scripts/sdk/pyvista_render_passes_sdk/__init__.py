r"""C++ SDK for pyvista-render-passes.

One install prefix per variant the matching ``pyvista-render-passes`` wheel
carries, under ``prefix/<backend>/``: headers, the ``PyVistaRenderPasses``
CMake package, the shared library to link and the wrapping hierarchy file::

    cmake -S . -B build -DVTK_DIR=<an SDK of the same backend and generation> \
        -DPyVistaRenderPasses_DIR="$(python -m pyvista_render_passes_sdk --cmake-dir)"

Nothing here is importable VTK; at run time a consumer loads the library from
the ``pyvista-render-passes`` wheel of the same version.
"""

from __future__ import annotations

from pathlib import Path

from ._build_info import BACKENDS, VERSION

__version__ = VERSION


def cmake_dir(backend: str | None = None) -> Path:
    """Return the ``PyVistaRenderPasses_DIR`` of one variant.

    Parameters
    ----------
    backend : str, optional
        ``'cvista'`` or ``'vtk'``; required when the SDK carries both.

    Returns
    -------
    pathlib.Path
        The directory holding ``PyVistaRenderPassesConfig.cmake``.

    Raises
    ------
    ValueError
        If ``backend`` is ambiguous or not carried.

    """
    if backend is None:
        if len(BACKENDS) != 1:
            msg = f'this SDK carries {list(BACKENDS)}; pass a backend'
            raise ValueError(msg)
        (backend,) = BACKENDS
    if backend not in BACKENDS:
        msg = f'no {backend!r} build in this SDK, which carries {list(BACKENDS)}'
        raise ValueError(msg)
    root = Path(__file__).resolve().parent / 'prefix' / backend
    return root / 'lib' / 'cmake' / 'PyVistaRenderPasses'
