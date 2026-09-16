<p align="center">
  <a href="https://codimensional.com">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://codimensional.com/partners/pyvista-codim-banner-dark.png">
      <img src="https://codimensional.com/partners/pyvista-codim-banner-light.png" width="516" alt="PyVista and CoDimensional">
    </picture>
  </a>
</p>

<h1 align="center">pyvista-render-passes</h1>

<p align="center">
  SSAA, SSAO, EDL, depth peeling and shadows for <a href="https://pyvista.org">PyVista</a>, composed in the one order that works.
</p>

<p align="center">
  <a href="https://pypi.org/project/pyvista-render-passes/"><img src="https://img.shields.io/pypi/v/pyvista-render-passes" alt="PyPI"></a>
  <a href="https://github.com/codimensional/pyvista-render-passes/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/codimensional/pyvista-render-passes/ci.yml?branch=main" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license"></a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/photo_real/on.png" width="560" alt="Marble bust with depth peeling, SSAO, shadows and SSAA">
</p>

Volume-safe supersampling (SSAA), screen-space ambient occlusion (SSAO), eye-dome lighting (EDL), depth peeling, shadow maps, depth of field and Gaussian blur, driven from `plotter.render_passes`.

Built and maintained by [CoDimensional PBC](https://codimensional.com).

The passes are VTK render passes written in C++ and wrapped for Python. Each wheel carries two builds, one against the stock [`vtk`](https://pypi.org/project/vtk/) wheel and one against [`cvista`](https://pypi.org/project/cvista/), and loads the one matching the distribution PyVista is running on.

## Install

```sh
pip install "pyvista-render-passes[cvista]"  # recommended
pip install "pyvista-render-passes[vtk]"     # stock VTK
```

Each wheel carries a build for both distributions and loads the one that is installed, preferring cvista when both are; the extra pins the version the build was compiled against. PyVista itself still requires stock `vtk`, so `[cvista]` installs both unless the resolver is told otherwise; with uv:

```toml
[project]
dependencies = ["pyvista-render-passes[cvista]"]

[tool.uv]
exclude-dependencies = ["vtk"]
```

or, one-off, `uv pip install --excludes <(echo vtk) "pyvista-render-passes[cvista]"`. See the [PyVista install docs](https://docs.pyvista.org/getting-started/installation.html) for the details and the caveats. Wheels are published for CPython 3.12 to 3.14 on Linux (x86_64, aarch64) and Windows (AMD64), and for macOS (arm64). The macOS wheel carries the cvista build only, since Kitware ships no arm64 wheel SDK; the Windows wheel carries the stock build only, since the cvista wheel's DLL names are mangled and cannot be linked against, so install `[vtk]` there. The stock build targets VTK 9.7. `PYVISTA_VTK_BACKEND=vtk` or `=cvista` forces the choice, as it does for PyVista; `pyvista_render_passes.BACKEND` reports it.

## Quickstart

```python
import pyvista as pv
import pyvista_render_passes  # registers plotter.render_passes

pl = pv.Plotter()
pl.add_mesh(pv.Sphere(radius=8, center=(8, 0, 0)), opacity=0.5)
pl.add_volume(pv.Wavelet(), opacity='sigmoid')
pl.render_passes.enable_depth_peeling().enable_ssao().enable_anti_aliasing()
pl.show()
```

Every `enable_*` / `disable_*` call only records a setting; the chain is rebuilt before the next render. Call `apply()` to rebuild immediately, or `describe()` to see what is enabled:

```python
>>> pl.render_passes.describe()
'DepthPeeling(peels=8) → SSAO(r=1.04, derived) → AntiAliasing'
```

The SSAO radius is derived from the scene bounds unless one is passed to `enable_ssao(radius=...)`.

`get_state()` / `set_state()` round-trip the settings as a plain dict, and `preset_interactive()`, `preset_still()` and `preset_photo_real()` set common combinations.

## Gallery

PyVista's example datasets, rendered by `scripts/render_gallery.py` into `docs/images/<example>/off.png` and `on.png`. The angel statue is by Ivan Nikolov (CC BY 4.0); the Washington bust is a Smithsonian CC0 scan.

<table>
  <tr><th width="50%">Off</th><th width="50%">On</th></tr>
  <tr><th colspan="2">EDL on a lidar point cloud: <code>enable_edl()</code></th></tr>
  <tr><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/edl/off.png" width="440"></td><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/edl/on.png" width="440"></td></tr>
  <tr><th colspan="2">SSAO on a CAD enclosure: <code>enable_ssao()</code></th></tr>
  <tr><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/ssao/off.png" width="440"></td><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/ssao/on.png" width="440"></td></tr>
  <tr><th colspan="2">Shadow maps on a statue: <code>enable_shadows()</code></th></tr>
  <tr><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/shadows/off.png" width="440"></td><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/shadows/on.png" width="440"></td></tr>
  <tr><th colspan="2">SSAA on a finite element mesh: <code>enable_anti_aliasing()</code></th></tr>
  <tr><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/ssaa/off.png" width="440"></td><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/ssaa/on.png" width="440"></td></tr>
  <tr><th colspan="2">Depth peeling on a translucent floor plan: <code>enable_depth_peeling()</code></th></tr>
  <tr><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/depth_peeling/off.png" width="440"></td><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/depth_peeling/on.png" width="440"></td></tr>
  <tr><th colspan="2">EDL annotation bypass keeps the cube axes and orientation axes out of EDL (text and scalar bars always are), on by default; off is <code>disable_annotation_bypass()</code></th></tr>
  <tr><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/edl_annotation_bypass/off.png" width="440"></td><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/edl_annotation_bypass/on.png" width="440"></td></tr>
  <tr><th colspan="2">CT volume under an opaque slice with SSAA: off is PyVista's <code>enable_anti_aliasing('ssaa')</code>, which draws the volume through the slice; on is <code>enable_anti_aliasing()</code></th></tr>
  <tr><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/volume_ssaa/off.png" width="440"></td><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/volume_ssaa/on.png" width="440"></td></tr>
  <tr><th colspan="2"><code>preset_photo_real()</code>: peeling, SSAO, shadows, SSAA</th></tr>
  <tr><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/photo_real/off.png" width="440"></td><td><img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/photo_real/on.png" width="440"></td></tr>
</table>

Depth of field and Gaussian blur are VTK's passes unchanged and are not shown; depth of field is driver-sensitive.

## Subplots

`plotter.render_passes` is the active subplot's settings, so each subplot gets its own chain:

```python
pl = pv.Plotter(shape=(1, 3))
grid = pv.ImageData(dimensions=(5, 5, 5)).explode(0.2)

pl.subplot(0, 0)
pl.add_mesh(grid)
pl.add_text('plain')

pl.subplot(0, 1)
pl.add_mesh(grid)
pl.add_text('EDL')
pl.render_passes.enable_edl()

pl.subplot(0, 2)
pl.add_mesh(grid)
pl.add_text('SSAO + SSAA')
pl.render_passes.enable_ssao().enable_anti_aliasing()

pl.link_views()
pl.show()
```

<p align="center">
  <img src="https://raw.githubusercontent.com/codimensional/pyvista-render-passes/main/docs/images/subplots.png" width="880" alt="Three linked subplots: plain, EDL, SSAO with SSAA">
</p>

Eye-dome lighting, blur and depth of field composite over the whole window from inside one subplot in VTK ([#18849](https://gitlab.kitware.com/vtk/vtk/-/issues/18849)), which blanks or whitens the others; the chain confines them to their own tile. `pl.render_passes.components` lists the subplots configured so far.

## Passes without the component

The passes are usable directly on any `vtkRenderer`:

```python
from pyvista_render_passes import enable_ssaa, enable_ssao, make_split_pass

enable_ssaa(pl, factor=2.0)  # SSAA on every renderer of a plotter
```

`pvRenderPassChain` builds the whole graph from a set of flags; `pvSSAAVolumePass` supersamples while keeping GPU volumes correct; `pvPropKeyFilterPass` renders a delegate over a tagged subset of the props (used to keep axes and other annotations out of EDL). See [docs/design.md](docs/design.md) for the reasoning behind the chain.

## Your own passes in the chain

An installed package extends `plotter.render_passes` through providers. A provider is a named unit that contributes passes at any of four stages, owns settings saved and restored with the component's, and can refuse component settings it cannot work with. Expose it through the `pyvista_render_passes.providers` entry-point group and it composes into every subplot of any plotter whose `render_passes` component exists. PyVista creates that component on first access to `pl.render_passes`, so a plotter no code touches it on renders with VTK's default pipeline and without the extension.

```toml
[project.entry-points."pyvista_render_passes.providers"]
tone_mapping = "my_package.passes:ToneMapping"
```

```python
from pyvista_render_passes import BasePassProvider


class ToneMapping(BasePassProvider):
    name = 'tone_mapping'
    stages = ('outer',)

    def __init__(self):
        self.state = self.default_state()

    def default_state(self):
        return {'enabled': True, 'exposure': 1.0}

    def get_state(self):
        return dict(self.state)

    def set_state(self, state):
        self.state |= state

    def build_pass(self, stage, renderer, chain, delegate):
        return make_tone_mapping_pass(**self.state) if self.state['enabled'] else None

    def veto(self, settings):
        return 'reads back depth, so MSAA must stay off' if settings['msaa'] else None
```

| Stage           | Providers                   | Where the pass sits                                                                                                                        |
| --------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `'base'`        | any number, innermost first | Wraps the scene base below SSAO; receives the pass to wrap as `delegate`.                                                                  |
| `'translucent'` | one                         | Replaces the translucent stage and takes over depth peeling.                                                                               |
| `'post'`        | one                         | Wraps the shaded frame below SSAA, so it is supersampled.                                                                                  |
| `'outer'`       | one                         | Wraps the SSAA-resolved frame at window resolution, below the overlay. For passes that size themselves from the window, like tone mapping. |

- `pl.render_passes.providers['tone_mapping']` is the live provider, one instance per subplot. Its state is `get_state()['providers']['tone_mapping']`. `set_state` restores it, and state for a provider that is not installed, or was removed, is kept and written back out. State is plain JSON. A provider that changes a setting other than through `set_state` calls `self.invalidate()` (the handle `add_provider` binds) to rebuild on the next render. `build_pass` returns a new pass on every call.
- A refused setting raises `SettingsVetoedError` from `enable_*`, `disable_*`, `preset_*` and `set_state` alike, and leaves `get_state()` unchanged. `set_ssaa_factor` is not vetoable, so a frame-time governor can call it every frame.
- An entry point that fails to import, instantiate or register, and every failed auto-apply, is logged at `ERROR` and warned as a `RuntimeWarning` pointing at your code. A broken entry point is skipped and listed in `pl.render_passes.provider_errors`.
- A pass that filters props on a channel of its own reserves one with `reserve_prop_filter_channel('my_package.overlay')`. `set_prop_filter_tag` refuses a channel nobody reserved; `'annotation'` is pre-reserved.
- An `'outer'` pass gets SSAA underneath it (at 1x when anti-aliasing is off), whose depth is restored into the window after the outer pass, so depth reads and point-label culling behave as without it.
- `register_pass_provider(pl, Provider)` adds a provider to one plotter's active subplot by hand; `unregister_pass_provider(pl, 'tone_mapping')` removes it. The component releases every pass a provider builds.

## Why a chain

VTK's render passes compose by delegation: each pass renders its delegate and post-processes the result. The order they are nested in decides whether they work at all, and a few pairs do not compose. `plotter.render_passes` owns that order so callers only set flags. Innermost first:

| Stage                                   | Setting                         | Where it sits and why                                                                                                                                                                                                                                                                                                                                                                |
| --------------------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Lights, opaque, translucent, volumetric | always                          | The scene base. Laid out flat rather than through `vtkRenderStepsPass`, whose own camera pass clears the buffers and would erase anything rendered ahead of it.                                                                                                                                                                                                                      |
| Shadow maps                             | `enable_shadows()`              | Replace the opaque stage, so opaque geometry is drawn once, with shadows. Needs a scene light away from the camera; the default headlight casts none.                                                                                                                                                                                                                                |
| Dual depth peeling                      | `enable_depth_peeling()`        | Replaces the translucent stage, but only when another pass is on. Alone, the renderer's built-in peeling is used and no pass is installed at all.                                                                                                                                                                                                                                    |
| SSAO                                    | `enable_ssao()`                 | Directly above the opaque base. SSAO reads the positions and normals of the props its delegate renders; put above a pass that composites through a full-screen quad (EDL, blur) it sees nothing and does nothing. Translucent props and volumes render after it, over the shaded opaque scene: inside its delegate, dual depth peeling paints translucent geometry with its normals. |
| EDL                                     | `enable_edl()`                  | Above SSAO. With annotation bypass (the default), tagged props (axes, cube axes, legend scales) render in a second stage after EDL, so their lines are not read as depth discontinuities and painted dark; 2D text and scalar bars sit in the overlay stage and never pass through EDL.                                                                                              |
| Depth of field, Gaussian blur           | `enable_dof()`, `enable_blur()` | Colour post-processing over the shaded frame.                                                                                                                                                                                                                                                                                                                                        |
| SSAA                                    | `enable_anti_aliasing()`        | Outermost scene pass: supersamples everything below and resolves colour and depth to the window. Point and line widths are scaled to stay visually constant. Also installed at 1x under EDL, blur and depth of field, which otherwise wipe the other subplots ([VTK #18849](https://gitlab.kitware.com/vtk/vtk/-/issues/18849)).                                                     |
| Overlay                                 | always                          | Last, at the window: text, scalar bars, legends and point labels are drawn after every pass has resolved, at window resolution. Point labels test against the window depth, which inside a pass's framebuffer is a frame stale and makes them flicker ([pyvista #4831](https://github.com/pyvista/pyvista/issues/4831)).                                                             |

Rules the component enforces or warns about:

| Combination            | Result                                                                                                                                                    |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SSAO + depth of field  | Refused: `enable_ssao()` and `enable_dof()` raise `ValueError` while the other is on.                                                                     |
| MSAA + any custom pass | Warning; MSAA has no effect once the scene renders into a pass's framebuffer. Use SSAA.                                                                   |
| MSAA + depth peeling   | Warning; multisampling corrupts the depth buffer peeling relies on.                                                                                       |
| Shadows + EDL          | Warning; the annotation stage has no shadow-map pass, so annotations are not shadowed.                                                                    |
| FXAA                   | Turned off on every apply; SSAA replaces it.                                                                                                              |
| SSAO + translucency    | Translucent props and volumes are not occlusion-shaded; they composite over the SSAO-shaded opaque scene.                                                 |
| SSAO radius            | A world-space length, so it is derived from the visible bounds unless passed to `enable_ssao(radius=...)`; `get_state()` reports a derived one as `None`. |

## Pixel correctness

The test suite runs on software GL (llvmpipe) in CI against both distributions and checks the state machine, the chain graph, the lifecycle, the prop filter and rendered pixels. The pixel tests assert measured properties (coverage, thickness, occlusion, depth) rather than comparing against image baselines, so none are shipped.

## Development

```sh
just sync         # fetch the VTK wheel SDK, build both variants, install with the dev extras
just test vtk     # pytest against stock VTK
just test cvista  # pytest against cvista
just lint         # pre-commit
```

A C++17 compiler and CMake are required. `cvista-sdk` supplies the headers and CMake config for the cvista build; `scripts/fetch_vtk_sdk.py` downloads Kitware's wheel SDK for the stock build into `build/vtk-sdk/`. Without that SDK the package still builds, carrying the cvista variant only.

The passes themselves are backend-neutral C++; `pyvista_render_passes.backend_module('vtkRenderingOpenGL2')` returns the active distribution's module for code that needs VTK classes without choosing one.

## Building against the C++ SDK

A VTK module of your own can link the passes and take them as arguments, from C++ and from Python. Every wheel build also produces a `pyvista-render-passes-sdk` wheel (headers, the `PyVistaRenderPasses` CMake package and the wrapping hierarchy file, per variant), which CI keeps as a build artifact; `just sdk` packages one locally.

```cmake
# -DVTK_DIR=<same backend and generation> -DPyVistaRenderPasses_DIR="$(python -m pyvista_render_passes_sdk --cmake-dir --backend vtk)"
find_package(VTK REQUIRED COMPONENTS CommonCore WrappingPythonCore)
find_package(PyVistaRenderPasses REQUIRED)
pyvista_render_passes_runtime_rpath(runtime_rpath DESTINATION my_package)  # relative to site-packages
list(APPEND CMAKE_INSTALL_RPATH "$ORIGIN" ${runtime_rpath})
# vtk.module: DEPENDS PyVistaRenderPasses::RenderPasses; wrapping then covers methods taking pvRenderPassChain*
```

To build against another VTK SDK, select one variant and the wheel's runtime pin follows that SDK:

```sh
PVRP_BACKEND=cvista PVRP_VTK_DIR=/path/to/cvista_sdk/cmake uv build --wheel
cmake -S extensions -B build/static -DPVRP_STATIC=ON -DPVRP_BACKEND=vtk -DVTK_DIR=<dir>  # C++-only archives
```

`tests/sdk_consumer/` is a complete consumer; [docs/design.md](docs/design.md) covers the pin mechanism and the SDK layout.
