# pyvista-render-passes

Render-pass chain for PyVista (C++ VTK passes wrapped for Python, plus the
`plotter.render_passes` component). Start with `README.md`, `docs/design.md`
and `just --list`. Build and test with `just sync`, then `just test vtk` and
`just test cvista`.

## Anti-patterns specific to this repo

- Importing from `vtk`, `vtkmodules` or `cvista` directly. Go through
  `pyvista_render_passes._backend` (or `tests/backend.py`); the same wheel
  serves both distributions and the choice is made once at import.
- A `vtk` prefix on anything of our own. Our classes are `pv*`, the wrapped
  module is `PyVistaRenderPasses`; `vtk.module` is VTK's required filename.
- Editing `src/` or `extensions/` and re-running tests without `just sync`.
  The install is non-editable, so a stale copy is what pytest sees.
- Closing a plotter that holds a hand-installed pass without releasing it.
  Tests get this from the `_close_plotters` fixture; the component does it in
  `__plotter_close__` and `__plotter_deep_clean__`.
