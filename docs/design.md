# Design notes

The class headers under `extensions/render_passes/` describe each pass's mechanism;
this file covers the decisions that span them.

## Chain order

`pvRenderPassChain::Build` composes the passes delegate-first:

```
camera -> lights -> [opaque | shadow maps]
       -> [translucent | dual depth peeling | caller's translucent pass]
       -> volumetric
       -> (caller's base pass, optional)
       -> SSAO [-> translucent and volumetric stages, when SSAO is on]
       -> EDL [-> annotation split]
       -> depth of field
       -> gaussian blur
       -> (caller's post pass, optional)
       -> SSAA
       -> (caller's outer pass, optional)
       -> overlay
```

- **SSAO delegates to EDL, never the reverse.** `vtkSSAOPass` fills its position
  and normal attachments through shader replacements installed on the props of
  its delegate render. `vtkEDLShading` composites through a full-screen quad
  that writes colour only, so an SSAO pass above EDL sees no props and resolves
  to "unoccluded" everywhere. The same holds for any caller-supplied base pass
  that composites through a quad, which is why the base sits below SSAO.
- **SSAO shades the opaque scene only.** With SSAO on, the translucent (or
  depth-peeling) and volumetric stages leave the base and run after the SSAO
  pass, through a camera pass that preserves the buffers, over its composited
  output and against the depth it wrote. Inside the SSAO delegate, the shader
  replacements that route positions and normals to the pass's extra colour
  attachments also apply to the props `vtkDualDepthPeelingPass` renders into a
  framebuffer of its own, which has no such attachments, so translucent
  geometry comes out painted with its normals. Volumes are not
  occlusion-shaded either, and no longer need `shade=True` to survive the pass.
- **The base is laid out stage by stage, not through `vtkRenderStepsPass`.**
  That pass wraps the stages in a camera pass of its own, and a camera pass
  clears the buffers when the renderer's erase flag is on. Placing the shadow
  passes before it in a sequence erased their output; placing them after it
  (PyVista's own layout) overdraws translucent props with shadowed opaque
  geometry. With the stages flat, the shadow pass takes the opaque stage's
  slot and everything renders once.
- **Depth peeling is built-in unless another pass is active.** With only
  depth peeling enabled the component sets `vtkRenderer::UseDepthPeeling` and
  installs no pass at all. As soon as any other pass is on, the renderer's
  built-in peeling is bypassed by the custom pass, so the chain inserts a
  `vtkDualDepthPeelingPass` of its own.
- **SSAA is the outermost built-in pass.** It supersamples everything below it,
  including the post-processing passes, and resolves to the window size. Only
  a caller's outer pass wraps it.
  It is also built at 1x whenever EDL, blur or depth of field is on and
  anti-aliasing is off: those passes clear and composite the whole window
  from inside their renderer's tile, which blanks or whitens every other
  subplot ([VTK #18849](https://gitlab.kitware.com/vtk/vtk/-/issues/18849)).
  Inside a tile-sized framebuffer they only see their own tile, and the pass
  copies it back to the tile's origin.
- **The annotation split.** With EDL and annotation bypass on, the EDL pass is
  wrapped in two `pvPropKeyFilterPass` stages: the first renders EDL over the
  untagged props; the second renders the tagged props with a plain camera pass
  against the depth the first wrote, with `PreserveBuffers` on so the frame is
  not cleared between them. Axes, cube axes, legend scales and 2D actors are
  tagged by `tag_scene_annotations` on every render. This keeps depth-driven
  shading off geometry that describes the scene rather than being part of it.
  2D actors render in the overlay stage after EDL regardless; the tag matters
  for the 3D annotation props.
- **The overlay stage runs last, at the window.** Text, scalar bars, legends
  and point labels render after the outermost pass has resolved to the window,
  at window resolution, so they are not supersampled, blurred or shaded with
  the scene. Point labels need this: `add_point_labels` culls through
  `vtkSelectVisiblePoints`, which reads the window depth, and inside a pass's
  framebuffer nothing has written that depth yet in the current frame. The
  previous frame's resolve carried the labels' own depth, so the labels
  culled themselves every other frame and flickered
  ([pyvista #4831](https://github.com/pyvista/pyvista/issues/4831)); stock
  VTK's `vtkSSAAPass` and `vtkSSAOPass` do the same. `TopPass` is the sequence
  of the outermost scene pass and the overlay; `ScenePass` is the former alone.
  Blur and depth of field write colour only, so under them the resolved depth
  is the cleared value and labels are never culled. The SSAA depth resolve
  takes the nearest supersample, so a label anchored on a silhouette or a
  line can be culled that a single-sampled render would show.

## Passes from other packages

Four seams take a pass the chain does not know about, so a package with its
own translucency, point splatting or tone mapping composes into the same graph
instead of rebuilding it:

- **`TranslucentPass`** replaces the translucent stage. Depth peeling is left
  to it entirely: the built-in peeling flag stays off and no
  `vtkDualDepthPeelingPass` is built.
- **The `base` argument of `Build`** wraps the scene base below SSAO. Anything
  that draws props with real depth goes here; a pass that composites through
  a quad hides the props from SSAO (see above). `BasePassProvided` tells the
  build decisions a base is coming, so a base alone forces a chain and manual
  peeling.
- **`PostPass`** wraps the shaded frame between the blur and SSAA, so it is
  supersampled with everything else.
- **`OuterPass`** wraps the SSAA-resolved frame, below the overlay stage. Its
  delegate has already resolved to the window, so a pass that sizes its
  framebuffer from the logical window (tone mapping) covers the whole frame;
  as a `PostPass` it would cover `1/factor²` of the SSAA framebuffer. Wrapping
  the scene directly loses the window depth: a colour-only outer pass
  (`vtkGaussianBlurPass`) left 0.0% of the window depth finite against 11.0%
  without it, because the SSAA depth resolve landed in the outer pass's
  framebuffer. `Build` therefore forces SSAA at 1x under an outer pass and
  appends a window depth restore that re-runs
  `pvSSAAVolumePass::RenderDepthResolve` after it. Without the 1x SSAA there
  is no depth to restore, and the depth and point-label tests fail with
  anti-aliasing off. An outer pass does not composite over a neighbouring
  subplot tile even without that SSAA. An earlier probe reported a 1.3%
  change in the other tile, but it came from the component setting the
  window's multisamples to 0 under the default theme's 8. With the component
  created in both renders the other tile changes by 0 pixels, so there is no
  tile test.

### Providers

A `PassProvider` is one named unit of an extension: `name`, `stages`,
`build_pass(stage, renderer, chain, delegate)`, `default_state` / `get_state` /
`set_state`, and `veto(settings)`. `BasePassProvider` makes every member but
`name` a no-op.

- **Discovery.** Every `RenderPassComponent`, one per subplot, instantiates the
  `pyvista_render_passes.providers` entry points when it is created, and
  `plotter.render_passes` creates the component of every subplot at once. Each
  subplot owns its provider's state. The entry point names a zero-argument
  callable. PyVista 0.49 creates a plotter component only on first attribute
  access (`_CachedComponent.__get__`), with no creation or first-render hook,
  so a plotter on which nothing touches `render_passes` composes no provider
  (`test_an_installed_provider_applies_without_touching_render_passes` is a
  strict xfail). Patching `Plotter.__init__` was rejected. The clean fix is
  upstream: an `eager=True` flag on `register_plotter_component` that
  `BasePlotter.__init__` honours once its renderers exist, or a
  `__plotter_first_render__` hook called from `_on_first_render_request`.
  `register_pass_provider` stays for a provider scoped to one plotter and goes
  through the same `add_provider` checks.
- **A broken entry point warns and is skipped.** A failure to import,
  instantiate or register (not a provider, duplicate name, taken
  single-provider stage, veto of the defaults) is logged at `ERROR`, warned as
  a `RuntimeWarning` naming `name = value` whose stack level skips PyVista and
  this package, and recorded in `provider_errors`. A failed auto-apply is
  reported the same way on every render it is retried, with no latch that
  silences it. Raising was rejected: one broken extension would make every
  `pv.Plotter` in the environment unusable. A warning alone was rejected
  because Python shows it once per call site.
- **State.** `get_state()['providers'][name]` is each provider's own state.
  `default_state()` is an instance method because its key set depends on what
  is installed. State for a name with no provider is kept, written back out by
  `get_state`, and handed to a provider that registers under that name later.
  Raising was rejected because a saved scene would not load on a machine
  without the extension. Dropping was rejected because one save there would
  erase the extension's settings. A removed provider's last state is kept the
  same way. State is plain JSON. `add_provider` binds the component's
  `invalidate` through `provider.bind`, and a provider calls it after changing
  a setting other than through `set_state`. Comparing `get_state()` on every
  render was rejected: NaN never compares equal, so it rebuilt every frame;
  a numpy array raised from inside the observer; and a live uniform change
  forced a rebuild. A provider whose `set_state` refuses its pending state is
  put back to its previous state, registered, and the state stays pending
  with a warning.
- **Vetoes.** Every `enable_*`, `disable_*`, `preset_*` and `set_*` method is
  a transaction. The component snapshots its settings and provider states,
  runs the setter, and asks each provider's `veto` about the result. On a
  refusal, or any other exception, it restores the snapshot and re-raises, so
  `set_state` is atomic too. A restore that itself fails raises `RuntimeError`
  from the restore error. The wrapping is derived from the method names and
  reapplied in `__init_subclass__`, so neither a new setter nor a subclass
  override skips it, and a test fails on any public method outside a named
  allowlist that is not wrapped. `set_ssaa_factor` is on that allowlist,
  because a frame-time governor calls it every frame. Vetoing only in `apply`
  was rejected: the
  refused value would already be in `get_state()` and would fail every
  auto-apply. `apply` still checks, for a provider changed directly into a
  conflict, and registration refuses a provider that vetoes the current
  settings.
- **Contributing nothing claims nothing.** `apply` builds the provider passes
  before reading the build decisions, and only marks a base as provided when a
  `'base'` provider wrapped something. Providers that all return `None` leave
  the renderer on VTK's default pipeline (or built-in depth peeling). Close
  and deep clean clear the translucent, post and outer seams.
- **Stages.** `'translucent'`, `'post'` and `'outer'` admit one provider each;
  `'base'` providers nest in registration order, innermost first. The
  component releases what a provider builds alongside the chain's own passes,
  so a provider holds no GL resources, and one that holds its plotter is an
  ordinary reference cycle.

### Prop-filter channels

`pvPropKeyFilterPass` partitions props on one bit of a per-prop bitmask
(channels 0 to 30). Two packages that pick a bit by hand can split each
other's props, so `reserve_prop_filter_channel(name)` hands bits out from a
process-wide registry. It is idempotent per name, takes the lowest free bit by
default, and raises when an explicit `channel=` is held under another name or
when every bit is taken. `'annotation'` is pre-reserved for
`CHANNEL_ANNOTATION`, which the annotation split filters on. The registry is
process-wide, not per plotter, because the tag lives on the prop and a prop can
be added to any plotter. Reservation is enforced: `set_prop_filter_tag` raises
on a channel nobody reserved. Queries and pass construction do not check,
because reading a channel cannot clash with another package.

## SSAO radius

`vtkSSAOPass::Radius` and `Bias` are world-space lengths, so one fixed pair
suits only one dataset scale. Unless set explicitly, the chain derives them
from the renderer's visible prop bounds: radius is `SsaoRadiusScale` (0.03) of
the bounding-box diagonal, bias a twentieth of the radius. Props with
`UseBounds` off are excluded by VTK's own bounds computation.

`RenderPassComponent.get_state()` reports a derived radius as `None`, so a
saved state re-derives on load rather than pinning a value computed for a
different scene.

## SSAO depth format

On a hardware GL context VTK's offscreen window allocates a fixed-point depth
attachment. The blit that feeds `vtkSSAOPass` its scene depth then mismatches
the pass's `Float32` default; strict drivers reject the blit and the pass reads
zeros, crushing the frame toward full occlusion. The component sets the depth
format to `Fixed32` on hardware renderers and leaves VTK's default on
software GL, where the mismatch is tolerated and the override is not.

## SSAA and GPU volumes

Stock `vtkSSAAPass` derives from `vtkImageProcessingPass`, so the
`vtkOpenGLRenderPass::RenderPasses()` information key is never set during its
delegate render. `vtkOpenGLGPUVolumeRayCastMapper` keys `PreserveViewport` off
that key; without it the mapper uses the logical window size instead of the
enlarged framebuffer's viewport and its depth blit and `gl_FragCoord` uniforms
are wrong. `pvSSAAVolumePass` derives from `vtkOpenGLRenderPass` so the key is
present, and adds a runtime supersample factor and a min-reduction depth resolve
(on by default) so window depth reads (depth images, picking, the overlay
stage's point labels) stay valid under supersampling.

## What is not here

- **Tone mapping.** `vtkToneMappingPass` is not part of the cvista
  distribution this package builds against, so the chain builds no
  tone-mapping pass of its own. A provider supplies one at the `'outer'` stage.
- **FXAA.** The component turns `vtkRenderer::UseFXAA` off on every apply.
  SSAA replaces it; enabling both blurs twice.
- **Image baselines.** The pixel tests measure properties of the frame
  (coverage, line thickness, occlusion, depth) and hold on llvmpipe, so the
  suite runs in full on CI and no baselines are kept.

## Two builds in one wheel

PyVista runs on either the stock `vtk` wheel or on `cvista`, and objects from
one cannot be handed to the other: each has its own wrapping runtime and its
own copies of every VTK library. A wheel that works for both therefore
carries the passes compiled twice, under `pyvista_render_passes/_vtk/` and
`_cvista/`, and `_backend.py` imports the one matching PyVista's choice
(`PYVISTA_VTK_BACKEND`, else cvista when installed, else stock).

The root `CMakeLists.txt` builds `extensions/` once per available SDK
through `ExternalProject`, because two `find_package(VTK)` calls cannot share
one CMake project. The cvista SDK comes from the `cvista-sdk` build
dependency; the stock SDK is Kitware's `vtk-wheel-sdk` tarball, unpacked by
`scripts/fetch_vtk_sdk.py` and found under `build/vtk-sdk/`. Kitware's
manylinux2014 x86_64 wheels use the pre-C++11 libstdc++ ABI, so the variant
build probes which ABI links and compiles to match.

Stock VTK wheels are not abi3, so the wheel is tagged per CPython. cvista is
abi3, and on macOS arm64, where Kitware publishes no wheel SDK, the wheel
carries only the cvista build and is tagged `cp312-abi3`. On Windows the
cvista wheel's DLLs carry delvewheel's hashed names, which the import
libraries in `cvista-sdk` do not reference, so the build dependency is
skipped there (`sys_platform != 'win32'`) and the Windows wheel carries only
the stock build. The stock Windows wheel keeps its DLL names, and
`vtkmodules` puts its `vtk.libs` directory on the DLL search path at import.

Each build finds its distribution's libraries through an rpath of
`$ORIGIN/../../<vtkmodules|cvista>` (Linux), `@loader_path/../../cvista`
(macOS) or `os.add_dll_directory` (Windows). Nothing from either
distribution is bundled, so every repair step is told to exclude them.

Each distribution is an extra pinned to what its build links: `vtk>=9.7,<9.8`
for the stock build, `cvista[rendering]==9.7.0.5.*` for cvista, all four
segments because the fourth is cvista's ABI generation. The pins are derived
from the SDKs at build time (next section), so bumping an SDK means rebuilding
and re-releasing, not editing a pin. Neither is a plain dependency, so an
install carries only the distribution asked for; PyVista's own `vtk` pin
still pulls stock VTK in until that becomes an extra upstream.

## Building against another SDK

`scripts/build_variants.py` is the one place that decides what a build
compiles against. The root `CMakeLists.txt` runs it for the variant list, and
scikit-build-core loads the same module as a `[[tool.dynamic-metadata]]`
provider for `optional-dependencies`. The extras
in the wheel are therefore computed from the SDKs that were compiled against:
`vtk>=M.m,<M.(m+1)` from the SDK's `vtk-config-version.cmake`, and
`cvista[rendering]==<generation>.*` from the `cvista-sdk` version, whose
fourth release segment is the ABI generation. A wheel carries an extra only
for a variant it has a build of.

By default every SDK found is built. `PVRP_BACKEND` and `PVRP_VTK_DIR` (the
directory holding `vtk-config.cmake`) select one:

```sh
PVRP_BACKEND=cvista PVRP_VTK_DIR=/path/to/cvista_sdk/cmake uv build --wheel
```

That wheel carries `_cvista` only and its `cvista` extra requires that SDK's
generation. Both variables are uv cache keys, so changing them rebuilds, and
so is the fetched SDK's `vtk-config-version.cmake`. Two fetched SDKs for one
interpreter are refused rather than one picked.

An sdist compiles nothing, so the provider looks for no SDK there: its
`build_state` hook sees `sdist` and returns no extra at all, and its
`dynamic_wheel` hook marks the field `Dynamic: Provides-Extra` in `PKG-INFO`,
so a wheel built from the sdist computes its own extras.
`tests/test_build_variants.py` builds an sdist under a selection that would fail
discovery and reads both back.

The consumer tests need `cvista-sdk` importable; it is the `sdk` dependency
group, equal to the `[build-system]` pin and separate from `dev`, so a lint
install does not pull an SDK.

The pin names `cvista[rendering]`, not `cvista-rendering`: the `cvista`
distribution carries the import package in every generation, and its
`rendering` extra pulls the split-out rendering wheel where one exists. A
generation with a single monolithic wheel installs it and the resolver warns
about the missing extra.

Rejected alternatives:

- **A pin written in `pyproject.toml`.** It is what made every SDK change a
  hand edit, and nothing checked it against what was linked.
- **A requirements file generated by CMake.** scikit-build-core resolves the
  metadata before CMake configures, so nothing CMake writes reaches `METADATA`.
- **Pinning from the `cvista-sdk` build requirement alone.** A caller-supplied
  SDK bypasses it, which is the case this exists for.
- **scikit-build-core's `regex` or `template` providers.** They read one file;
  the pin has to come from the same discovery that picks what CMake builds.

## The C++ SDK

Every variant build installs a CMake package into its staging tree next to the
runtime files: headers and the generated `PyVistaRenderPassesModule.h` under
`include/pyvista-render-passes/`, `PyVistaRenderPassesConfig.cmake` with its
version and VTK-identity files, the targets and module-properties files (which
`vtk_module_build` writes only when `INSTALL_EXPORT` is set; without it the
install succeeds and `find_package` imports no targets), the Python module
properties naming `pyvista_render_passes._<backend>`, and the wrapping
hierarchy file. The runtime wheel installs only `pyvista_render_passes/`.

`scripts/build_sdk.py` compiles nothing. It copies each staging tree, minus the
wrapped Python module and `__init__.py`, into `prefix/<backend>/` of a
`pyvista-render-passes-sdk` wheel, so the SDK carries the library the runtime
wheel was built from rather than a second build that can drift from it.
cibuildwheel runs it at the end of every repair step with the wheel it built
(whose tag names the build tree) and the repaired wheel (whose platform tag the
SDK takes, such as `manylinux_2_28_x86_64`). On Linux it writes into the
container's `/output`, which cibuildwheel copies out whole; natively into
`wheelhouse/`. Each CPython build writes the same SDK file name and the last
wins: the C++ is the same, only the interpreter that drove the build differs.
`just sdk` packages the tree `just sync` left, with the unrepaired tag, for local
use only.

The exported target is `PyVistaRenderPasses::RenderPasses`, which a dependent
`vtk.module` names under `DEPENDS`. The library and the Python module keep the
name `PyVistaRenderPasses`.

```cmake
# -DVTK_DIR=<same backend and generation>
# -DPyVistaRenderPasses_DIR="$(python -m pyvista_render_passes_sdk --cmake-dir --backend vtk)"
find_package(VTK REQUIRED COMPONENTS CommonCore WrappingPythonCore)
find_package(PyVistaRenderPasses REQUIRED)
pyvista_render_passes_runtime_rpath(runtime_rpath DESTINATION my_package)
list(APPEND CMAKE_INSTALL_RPATH "$ORIGIN" ${runtime_rpath})
```

**What the package refuses.** `PyVistaRenderPassesVTKIdentity.cmake` tells the
build and the config the same thing about the VTK that `VTK_DIR` resolved: its
backend (a cvista SDK keeps `_version.py` beside its `cmake/`) and its
generation, cvista's four release segments or stock VTK's major.minor, which is
what the runtime pins accept. The config refuses another backend or generation,
and when the library was built with the pre-C++11 libstdc++ ABI it also
`try_compile`s that VTK links with it. Each refusal is a `find_package` failure
naming both sides, since a mismatched VTK links and then misbehaves.
`tests/test_sdk_consumer.py` checks a cvista package against Kitware's 9.7.0 SDK,
a stock package against cvista-sdk 9.7.0.5, and a cvista package against the
next generation. A fourth exercises the probe itself: the stock package with the
backend and generation it records rewritten to cvista's passes both earlier
gates, and the probe then refuses cvista's VTK for its libstdc++ ABI.

**Run time.** A consumer links the SDK's copy of `libPyVistaRenderPasses` and
loads the runtime wheel's, the same build under the same SONAME (on macOS an
`@rpath` install name), so one copy is in the process.
`pyvista_render_passes_runtime_rpath(<var> DESTINATION <dir>)` returns the
`$ORIGIN`- or `@loader_path`-relative rpath from `<dir>`, relative to
site-packages, to `pyvista_render_passes/_<backend>` and to the backend's own
library directory (`vtkmodules` or `cvista`), so the consumer imports first in a
fresh process. Windows has no rpath; import `pyvista_render_passes` first there.
The consumer test installs with only that rpath, imports the consumer before
anything else, and shows the import failing without it.

**Hierarchy.** VTK's wrappers wrap a method taking an object only when they
resolve its class through a hierarchy file, and fall back to a literal `vtk`
name prefix otherwise, which `pv*` does not match. Without the file, a
dependent module's `SetChain(pvRenderPassChain*)` compiles and is silently
missing from Python. The consumer test asserts the wrapped round trip, and that
the same build drops both methods when the file is withheld.

**libstdc++ ABI.** Against Kitware's manylinux2014 SDK the library is built with
`_GLIBCXX_USE_CXX11_ABI=0`, exported as a public compile definition of the
target, so a consumer's own `std::string` call into VTK resolves. The consumer's
`DescribeChain` fails to load when that definition is stripped from the targets
file.

One SDK wheel carries every variant its runtime wheel carries, rather than one
SDK distribution per variant: it mirrors the runtime wheel, one pinned version
resolves the pair, and a consumer picks a prefix by backend the way the runtime
picks a build. It depends on `cvista-sdk==<generation>.*` when it carries
cvista; Kitware's SDK is not a published distribution, and the config records
the generation instead. Both workflows keep the SDK wheel as a build artifact:
publishing it would need a publisher for a second project name, which this
repository does not configure.

**Static and Emscripten.** `cmake -S extensions -DPVRP_STATIC=ON
-DPVRP_BACKEND=vtk -DVTK_DIR=<dir>` builds archives with no Python wrapping. The
CI `wasm` job compiles it with `emcmake` inside Kitware's `vtk-wasm-sdk` image,
pinned by digest. It is compile-only: nothing links a program against the
archive, runs it or renders with it.

`pvPropKeyFilterPass::FILTER_KEY()` is a static `vtkInformationIntegerKey`
defined in the library, and a key is compared by address. Linked statically,
every final binary that contains the archive gets its own key: two plugins
loaded `RTLD_LOCAL`, each linking the archive, tag props with keys the other's
pass never sees, so a prop tagged through one is unfiltered by the other. Link
the archive into exactly one binary per process, or use the shared library.
