/**
 * @class   pvRenderPassChain
 * @brief   Assembles a render pass chain from one settings surface.
 *
 * Written delegate-first (innermost rendered first, outermost composes last):
 *
 * ```
 *   camera -> lights -> [opaque | shadow maps]
 *          -> [translucent | dual depth peeling | caller's TranslucentPass]
 *          -> volumetric
 *          -> (caller's base pass, optional)
 *          -> SSAO [-> translucent and volumetric stages, when SSAO is on]
 *          -> EDL [-> annotation split]
 *          -> depth of field
 *          -> gaussian blur
 *          -> (caller's PostPass, optional)
 *          -> SSAA
 *          -> (caller's OuterPass, optional)
 *          -> overlay
 * ```
 *
 * Four seams take a pass from another package: `TranslucentPass` replaces the
 * translucent stage, the `base` argument of `Build` wraps the scene base below
 * every screen-space pass, `PostPass` wraps the shaded frame below SSAA, and
 * `OuterPass` wraps the SSAA-resolved frame at window resolution. Whoever
 * supplies such a pass releases it.
 *
 * @section outer-stage The outer stage sees the resolved frame
 *
 * A pass that sizes its framebuffer from the logical window (tone mapping is
 * the usual one) covers only a fraction of an SSAA framebuffer, which is
 * larger by the supersample factor on each axis. `OuterPass` runs outside SSAA,
 * so its delegate has already resolved to the window and the two agree.
 *
 * An outer pass renders its delegate into a framebuffer of its own, so the depth
 * SSAA resolves lands there rather than in the window. `Build` therefore builds
 * SSAA under an outer pass, at 1x when anti-aliasing is off, and resolves that
 * pass's depth into the window again after the outer pass, whatever the outer
 * pass does with depth.
 *
 * @section overlay-last The overlay stage runs last, at the window
 *
 * 2D props (text, scalar bars, legends, point labels) render after every
 * framebuffer pass has resolved to the window, at window resolution. Inside a
 * pass's framebuffer they would be supersampled, blurred or shaded like the
 * scene, and `vtkSelectVisiblePoints` (behind point labels) tests each label
 * against the window depth, which nothing has written yet in that frame; the
 * previous frame's resolve then carries the labels' own depth, so the labels
 * cull themselves every other frame. Rendered after the resolve they test
 * against the current frame's scene depth and are stable.
 *
 * @section ssao-opaque SSAO shades the opaque scene only
 *
 * With SSAO on, the translucent (or depth-peeling) and volumetric stages leave
 * the scene base and run after the SSAO pass, over its composited output and
 * against the depth it wrote. Inside the SSAO delegate, the shader replacements
 * that route positions and normals to the pass's extra colour attachments also
 * apply to the props `vtkDualDepthPeelingPass` renders into a framebuffer of
 * its own, which has no such attachments: translucent geometry comes out
 * painted with its normals. Volumes are not occlusion-shaded either; they
 * composite over the shaded opaque scene like any other renderer would draw
 * them.
 *
 * @section ssao-before-edl SSAO delegates to EDL, never the reverse
 *
 * `vtkSSAOPass` fills the position and normal attachments of its framebuffer
 * through shader replacements it installs on the props of the delegate render.
 * `vtkEDLShading` renders the props into a framebuffer of its own and composites
 * through a fullscreen quad that writes colour only. An SSAO pass delegating to
 * EDL therefore never sees a prop: its position and normal attachments stay
 * undefined, the occlusion resolves to "unoccluded" everywhere, and the pass is
 * a no-op that still costs a multi-attachment framebuffer every frame. The same
 * applies to any caller-supplied base pass that composites through a fullscreen
 * quad, which is why the base goes below SSAO: SSAO still occludes every other
 * prop, and the base's pixels take one uniform veil rather than contact darkening.
 *
 * @section ssao-radius The SSAO radius is a world-space length, so derive it
 *
 * `Radius` and `Bias` are world units, so one hardcoded pair only suits one
 * dataset scale. Unless set explicitly they are re-derived from the renderer's
 * visible prop bounds as `RadiusScale` of the bounding-box diagonal, with the
 * bias a twentieth of the radius. A prop that opts out of bounds
 * (`vtkProp::SetUseBounds(false)`) is excluded.
 *
 * @sa pvPropKeyFilterPass pvSSAAVolumePass
 */

#ifndef pvRenderPassChain_h
#define pvRenderPassChain_h

#include "PyVistaRenderPassesModule.h"
#include "vtkImageProcessingPass.h"
#include "vtkObject.h"
#include "vtkRenderPass.h"
#include "vtkSmartPointer.h"

class pvSSAAVolumePass;
class vtkDepthOfFieldPass;
class vtkDualDepthPeelingPass;
class vtkEDLShading;
class vtkGaussianBlurPass;
class vtkRenderPassCollection;
class vtkRenderer;
class vtkSSAOPass;
class vtkSequencePass;
class vtkShadowMapPass;
class vtkWindow;

class PYVISTARENDERPASSES_EXPORT pvRenderPassChain : public vtkObject
{
public:
  static pvRenderPassChain* New();
  vtkTypeMacro(pvRenderPassChain, vtkObject);
  void PrintSelf(ostream& os, vtkIndent indent) override;

  ///@{
  /**
   * Order-independent transparency. Only the manual `vtkDualDepthPeelingPass`
   * composes with a custom chain, and it is built only when some other pass
   * makes a custom chain necessary. `vtkRenderer::SetUseDepthPeeling` is the
   * lighter path otherwise but does not compose with `vtkRenderer::SetPass`, so
   * it is the caller's to set; `GetUsesBuiltinDepthPeeling` reports when.
   */
  vtkSetMacro(DepthPeeling, bool);
  vtkGetMacro(DepthPeeling, bool);
  vtkBooleanMacro(DepthPeeling, bool);
  vtkSetClampMacro(DepthPeelingMaximumNumberOfPeels, int, 1, VTK_INT_MAX);
  vtkGetMacro(DepthPeelingMaximumNumberOfPeels, int);
  vtkSetClampMacro(DepthPeelingOcclusionRatio, double, 0.0, 1.0);
  vtkGetMacro(DepthPeelingOcclusionRatio, double);
  ///@}

  ///@{
  /**
   * Shadow mapping. The shadow-map baker and the shadow pass take the place of
   * the opaque stage in the scene base.
   */
  vtkSetMacro(Shadows, bool);
  vtkGetMacro(Shadows, bool);
  vtkBooleanMacro(Shadows, bool);
  ///@}

  ///@{
  /**
   * Eye dome lighting.
   */
  vtkSetMacro(EDL, bool);
  vtkGetMacro(EDL, bool);
  vtkBooleanMacro(EDL, bool);
  ///@}

  ///@{
  /**
   * Render props tagged on `pvPropKeyFilterPass::ChannelAnnotation` in their
   * own stage after EDL has composed, instead of through it. Only EDL gets the
   * split: every other pass is a colour operation that should cover the whole
   * frame. Has no effect while EDL is off.
   */
  vtkSetMacro(AnnotationBypass, bool);
  vtkGetMacro(AnnotationBypass, bool);
  vtkBooleanMacro(AnnotationBypass, bool);
  ///@}

  ///@{
  /**
   * Screen-space ambient occlusion. See the class documentation for why it sits
   * below EDL and why the radius is derived rather than fixed.
   */
  vtkSetMacro(SSAO, bool);
  vtkGetMacro(SSAO, bool);
  vtkBooleanMacro(SSAO, bool);
  vtkSetClampMacro(SsaoKernelSize, int, 1, 1000);
  vtkGetMacro(SsaoKernelSize, int);
  vtkSetMacro(SsaoBlur, bool);
  vtkGetMacro(SsaoBlur, bool);
  vtkBooleanMacro(SsaoBlur, bool);
  ///@}

  ///@{
  /**
   * SSAO sampling hemisphere radius, in world units. Setting it pins it;
   * `SetSsaoRadiusToDerived` hands the knob back to the bounds-derived policy.
   * A non-finite or non-positive value is rejected with a warning, because a
   * zero radius silently renders no occlusion at all.
   */
  void SetSsaoRadius(double radius);
  vtkGetMacro(SsaoRadius, double);
  void SetSsaoRadiusToDerived();
  vtkGetMacro(SsaoRadiusExplicit, bool);
  ///@}

  ///@{
  /**
   * SSAO angle bias that suppresses self-occlusion on flat surfaces, in world
   * units. Same explicit/derived contract as the radius; the derived value is
   * one twentieth of the radius in force.
   */
  void SetSsaoBias(double bias);
  vtkGetMacro(SsaoBias, double);
  void SetSsaoBiasToDerived();
  vtkGetMacro(SsaoBiasExplicit, bool);
  ///@}

  ///@{
  /**
   * Fraction of the visible bounding-box diagonal the derived radius takes.
   */
  vtkSetClampMacro(SsaoRadiusScale, double, 1e-6, 1.0);
  vtkGetMacro(SsaoRadiusScale, double);
  ///@}

  /**
   * Diagonal of the visible prop bounds the last successful `SyncDerivedSsao`
   * measured, or 0 when it has never measured one.
   */
  vtkGetMacro(SsaoBoundsDiagonal, double);

  ///@{
  /**
   * Internal format of the depth texture `vtkSSAOPass` allocates, as a
   * `vtkTextureObject` internal-format enum, or -1 (the default) to leave
   * VTK's own `Float32` in place.
   *
   * VTK's offscreen hardware GL context allocates a fixed-point window depth
   * attachment, so the blit feeding the AO pass its scene depth is a format
   * mismatch; a strict driver rejects it, the pass reads a zeroed depth
   * texture, and the whole frame crushes toward full occlusion. Matching the
   * window's format avoids the mismatched blit. Software GL tolerates the
   * mismatch, so the choice needs a live renderer probe and belongs to the caller.
   */
  vtkSetMacro(SsaoDepthFormat, int);
  vtkGetMacro(SsaoDepthFormat, int);
  ///@}

  ///@{
  /**
   * Depth of field. Incompatible with SSAO, which the caller validates; this
   * class builds whatever it is told.
   */
  vtkSetMacro(DepthOfField, bool);
  vtkGetMacro(DepthOfField, bool);
  vtkBooleanMacro(DepthOfField, bool);
  vtkSetMacro(DepthOfFieldAutomaticFocalDistance, bool);
  vtkGetMacro(DepthOfFieldAutomaticFocalDistance, bool);
  vtkBooleanMacro(DepthOfFieldAutomaticFocalDistance, bool);
  ///@}

  ///@{
  /**
   * Gaussian blur post-processing.
   */
  vtkSetMacro(Blur, bool);
  vtkGetMacro(Blur, bool);
  vtkBooleanMacro(Blur, bool);
  ///@}

  ///@{
  /**
   * Supersampled anti-aliasing through `pvSSAAVolumePass`, the outermost pass.
   */
  vtkSetMacro(AntiAliasing, bool);
  vtkGetMacro(AntiAliasing, bool);
  vtkBooleanMacro(AntiAliasing, bool);
  vtkSetClampMacro(SsaaFactor, double, 1.0, 4.0);
  vtkGetMacro(SsaaFactor, double);
  ///@}

  ///@{
  /**
   * A pass from another package for the translucent stage. It renders the
   * translucent props in place of `vtkTranslucentPass` or the dual depth
   * peeling pass, so it owns order-independent transparency; the depth-peeling
   * settings above are then its to read. Setting one makes the chain custom.
   */
  vtkSetSmartPointerMacro(TranslucentPass, vtkRenderPass);
  vtkGetSmartPointerMacro(TranslucentPass, vtkRenderPass);
  ///@}

  ///@{
  /**
   * A pass from another package over the shaded frame, above blur and below
   * SSAA. `Build` sets its delegate. Setting one makes the chain custom.
   */
  vtkSetSmartPointerMacro(PostPass, vtkImageProcessingPass);
  vtkGetSmartPointerMacro(PostPass, vtkImageProcessingPass);
  ///@}

  ///@{
  /**
   * A pass from another package over the SSAA-resolved frame, at window
   * resolution, below the overlay stage. `Build` sets its delegate. Setting one
   * makes the chain custom.
   */
  vtkSetSmartPointerMacro(OuterPass, vtkImageProcessingPass);
  vtkGetSmartPointerMacro(OuterPass, vtkImageProcessingPass);
  ///@}

  ///@{
  /**
   * Whether `Build` is going to receive a base pass. The decisions below
   * depend on it, and they are needed to build the scene base that pass wraps.
   */
  vtkSetMacro(BasePassProvided, bool);
  vtkGetMacro(BasePassProvided, bool);
  vtkBooleanMacro(BasePassProvided, bool);
  ///@}

  ///@{
  /**
   * Resolved build decisions, computed from the flags alone, so a caller can
   * ask before building whether it is going to own `vtkRenderer::SetPass`.
   */
  bool GetRequiresCustomPassChain() const;
  bool GetUsesManualDepthPeeling() const;
  bool GetUsesBuiltinDepthPeeling() const;
  ///@}

  /**
   * Re-derive the SSAO radius and bias from a renderer's visible prop bounds.
   * Returns true when a value moved. A no-op when both are explicit, when the
   * renderer is null, and when the scene is empty or degenerate, because a
   * radius of zero renders no occlusion at all and is worse than a stale one.
   * `Build` calls this itself.
   */
  bool SyncDerivedSsao(vtkRenderer* renderer);

  /**
   * Build the scene base: a camera pass over the lights, opaque (or shadow-map),
   * translucent (or dual-depth-peeling) and volumetric stages. The overlay
   * stage is not part of it; `Build` appends one after the outermost pass.
   * With SSAO on, neither are the translucent and volumetric stages: `Build`
   * runs them after the SSAO pass. Exposed so a caller can interpose its own
   * pass between the base and the screen-space chain; pass the result (or
   * whatever wraps it) back to `Build`.
   */
  vtkRenderPass* BuildSceneBase();

  ///@{
  /**
   * Assemble the chain and return its outermost pass, or nullptr when the
   * settings need no custom pass at all and the renderer should run VTK's
   * default pipeline. `base` is the pass the screen-space chain wraps; nullptr
   * means "call `BuildSceneBase` for me". Every pass built is retained by this
   * object and reachable through the `Get*Pass` accessors until the next build.
   *
   * The caller owns everything that is not a pass: `vtkRenderer::SetPass` with
   * the result, the built-in depth-peeling flags when
   * `GetUsesBuiltinDepthPeeling` is true, `SetAlphaBitPlanes` when either
   * depth-peeling path is, `vtkRenderWindow::SetMultiSamples`, and hidden-line
   * removal.
   *
   * @warning Issues no GL calls, but the passes it discards own framebuffers
   * and textures. Call `ReleaseGraphicsResources` with the context current
   * before rebuilding, or every rebuild leaks the previous chain's GPU memory.
   */
  vtkRenderPass* Build(vtkRenderer* renderer, vtkRenderPass* base);
  vtkRenderPass* Build(vtkRenderer* renderer) { return this->Build(renderer, nullptr); }
  ///@}

  ///@{
  /**
   * The passes of the chain built by the last `Build`, or nullptr when that
   * build did not include one. Borrowed pointers, valid until the next build.
   * `TopPass` is the sequence of the outermost scene pass and the overlay
   * stage; `ScenePass` is that outermost scene pass on its own.
   */
  vtkGetObjectMacro(TopPass, vtkRenderPass);
  vtkGetObjectMacro(ScenePass, vtkRenderPass);
  vtkGetObjectMacro(SceneBasePass, vtkRenderPass);
  vtkGetObjectMacro(DepthPeelingPass, vtkDualDepthPeelingPass);
  vtkGetObjectMacro(ShadowPass, vtkShadowMapPass);
  vtkGetObjectMacro(SsaoPass, vtkSSAOPass);
  vtkGetObjectMacro(EDLPass, vtkEDLShading);
  vtkGetObjectMacro(AnnotationSplitPass, vtkSequencePass);
  vtkGetObjectMacro(DepthOfFieldPass, vtkDepthOfFieldPass);
  vtkGetObjectMacro(BlurPass, vtkGaussianBlurPass);
  vtkGetObjectMacro(SsaaPass, pvSSAAVolumePass);
  ///@}

  /**
   * Release the GPU resources of every pass built by the last `Build`.
   *
   * @warning Issues OpenGL calls, so it must run with the context current. A
   * null `window` is refused with a warning: several of VTK's own passes assert
   * on it. Use `Forget` when there is no context to release against.
   */
  void ReleaseGraphicsResources(vtkWindow* window);

  /**
   * Drop every pass retained by the last `Build` without touching the GPU. For
   * a teardown that runs with no context current; whatever GPU memory those
   * passes still own is released by whoever destroys the context.
   */
  void Forget();

protected:
  pvRenderPassChain();
  ~pvRenderPassChain() override;

private:
  pvRenderPassChain(const pvRenderPassChain&) = delete;
  void operator=(const pvRenderPassChain&) = delete;

  void ForgetBasePasses();
  void ForgetChainPasses();
  void AddTranslucentAndVolumetricStages(vtkRenderPassCollection* collection);
  vtkSmartPointer<vtkRenderPass> BuildLateStage();

  bool DepthPeeling = false;
  int DepthPeelingMaximumNumberOfPeels = 8;
  double DepthPeelingOcclusionRatio = 0.0;
  bool Shadows = false;
  bool EDL = false;
  bool AnnotationBypass = true;
  bool SSAO = false;
  double SsaoRadius = 0.5;
  double SsaoBias = 0.025;
  bool SsaoRadiusExplicit = false;
  bool SsaoBiasExplicit = false;
  double SsaoRadiusScale = 0.03;
  double SsaoBoundsDiagonal = 0.0;
  int SsaoKernelSize = 256;
  bool SsaoBlur = true;
  int SsaoDepthFormat = -1;
  bool DepthOfField = false;
  bool DepthOfFieldAutomaticFocalDistance = true;
  bool Blur = false;
  bool AntiAliasing = false;
  double SsaaFactor = 2.2360679774997896; // sqrt(5), the pvSSAAVolumePass default
  vtkSmartPointer<vtkRenderPass> TranslucentPass;
  vtkSmartPointer<vtkImageProcessingPass> PostPass;
  vtkSmartPointer<vtkImageProcessingPass> OuterPass;
  bool BasePassProvided = false;

  // Owned concretely, because a caller reaching for a pass wants its own knobs
  // and vtkRenderPass has none of them.
  vtkSmartPointer<vtkRenderPass> TopPassOwned;
  vtkSmartPointer<vtkRenderPass> ScenePassOwned;
  vtkSmartPointer<vtkRenderPass> SceneBasePassOwned;
  vtkSmartPointer<vtkDualDepthPeelingPass> DepthPeelingPassOwned;
  vtkSmartPointer<vtkShadowMapPass> ShadowPassOwned;
  vtkSmartPointer<vtkSSAOPass> SsaoPassOwned;
  vtkSmartPointer<vtkRenderPass> LateStagePassOwned;
  vtkSmartPointer<vtkEDLShading> EDLPassOwned;
  vtkSmartPointer<vtkSequencePass> AnnotationSplitPassOwned;
  vtkSmartPointer<vtkDepthOfFieldPass> DepthOfFieldPassOwned;
  vtkSmartPointer<vtkGaussianBlurPass> BlurPassOwned;
  vtkSmartPointer<pvSSAAVolumePass> SsaaPassOwned;

  // Raw mirrors so vtkGetObjectMacro can hand out borrowed pointers.
  vtkRenderPass* TopPass = nullptr;
  vtkRenderPass* ScenePass = nullptr;
  vtkRenderPass* SceneBasePass = nullptr;
  vtkDualDepthPeelingPass* DepthPeelingPass = nullptr;
  vtkShadowMapPass* ShadowPass = nullptr;
  vtkSSAOPass* SsaoPass = nullptr;
  vtkEDLShading* EDLPass = nullptr;
  vtkSequencePass* AnnotationSplitPass = nullptr;
  vtkDepthOfFieldPass* DepthOfFieldPass = nullptr;
  vtkGaussianBlurPass* BlurPass = nullptr;
  pvSSAAVolumePass* SsaaPass = nullptr;
};

#endif
