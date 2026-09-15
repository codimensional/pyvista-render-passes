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
- **SSAA is outermost.** It supersamples everything below it, including the
  post-processing passes, and resolves to the window size last.
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

Three seams take a pass the chain does not know about, so a package with its
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

On the Python side a `PassProvider` (`stage` plus `build_pass(renderer, chain,
delegate)`) is registered on the active subplot with `register_pass_provider`, given an
instance, a class, or a bare `build_pass` function with its stage, directly or
as a decorator. The component keeps the providers, so one that holds its
plotter is an ordinary reference cycle. The
component composes the registered providers on every rebuild and releases
what they built alongside the chain's own passes, so a provider holds no GL
resources of its own. `'translucent'` and `'post'` admit one provider each;
`'base'` providers nest in registration order, innermost first. Registration
queues a rebuild, so a provider added after the first render takes effect on
the next one.

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
  distribution this package builds against, so the chain has no tone-mapping
  stage. It can be reintroduced above SSAA when the dependency provides it.
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
for the stock build, `cvista-rendering==9.7.0.5.*` for cvista, all four
segments because the fourth is cvista's ABI generation. Bumping either means
rebuilding and re-releasing this package. Neither is a plain dependency, so an
install carries only the distribution asked for; PyVista's own `vtk` pin
still pulls stock VTK in until that becomes an extra upstream.
