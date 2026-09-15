// SPDX-FileCopyrightText: Copyright (c) 2026 CoDimensional PBC
// SPDX-FileCopyrightText: Copyright (c) Ken Martin, Will Schroeder, Bill Lorensen
// SPDX-License-Identifier: MIT AND BSD-3-Clause

/**
 * @class   pvSSAAVolumePass
 * @brief   Variable-factor SSAA render pass that composes with GPU volume mappers.
 *
 * Supersamples the scene into an enlarged framebuffer and downsamples it back to
 * the logical window with the same separable Lanczos-class filter as stock
 * @c vtkSSAAPass. Two differences from the stock pass:
 *
 * 1. It derives from @c vtkOpenGLRenderPass, so @c Render registers the
 *    @c vtkOpenGLRenderPass::RenderPasses() information key. The GPU volume
 *    ray-cast mapper keys its @c PreserveViewport flag off that key; without it
 *    the mapper reads the logical window size instead of the live @c GL_VIEWPORT
 *    while the pass renders into a larger framebuffer, and the size mismatch
 *    corrupts its scene-depth blit and @c gl_FragCoord uniforms, so volumes
 *    render garbled, half-lit, or blank under stock SSAA.
 *
 * 2. The supersample factor is a runtime property rather than a hardcoded
 *    @c sqrt(5). It can be changed between frames, from 1.0 to 4.0, without a
 *    chain rebuild. At 1.0 the scene still renders into a tile-sized
 *    framebuffer and is copied back unfiltered: that confines a screen-space
 *    delegate to its own subplot and keeps the depth resolve below.
 *
 * The depth attachment is a texture, float when the window depth is float and
 * 32-bit fixed otherwise (the GPU volume mapper blits scene depth into a
 * @c GL_DEPTH_COMPONENT32 texture, and Mesa refuses a blit between differing
 * depth formats). With @c ResolveDepth on, the supersampled depth is resolved into
 * the window depth attachment after the scene render by a full-screen quad that
 * writes the minimum (closest) supersample covering each window pixel to
 * @c gl_FragDepth, so a single-sampled @c glReadPixels(GL_DEPTH_COMPONENT) of
 * the window returns valid, conservative scene depth. A scaled depth blit is not
 * portable and would nearest-pick one sample; the min reduction is both. On by
 * default: without it the window depth is never written under this pass, and
 * anything that reads it (vtkSelectVisiblePoints behind point labels, depth
 * images, picking) sees stale or empty depth. Off, the path is byte-identical to
 * stock SSAA colour.
 */
#ifndef pvSSAAVolumePass_h
#define pvSSAAVolumePass_h

#include "PyVistaRenderPassesModule.h"
#include "vtkOpenGLRenderPass.h"
#include "vtkSetGet.h"

class vtkOpenGLFramebufferObject;
class vtkOpenGLHelper;
class vtkRenderPass;
class vtkTextureObject;

class PYVISTARENDERPASSES_EXPORT pvSSAAVolumePass : public vtkOpenGLRenderPass
{
public:
  static pvSSAAVolumePass* New();
  vtkTypeMacro(pvSSAAVolumePass, vtkOpenGLRenderPass);
  void PrintSelf(ostream& os, vtkIndent indent) override;

  void Render(const vtkRenderState* s) override;
  void ReleaseGraphicsResources(vtkWindow* w) override;

  ///@{
  /**
   * Delegate rendering the scene to be supersampled.
   */
  void SetDelegatePass(vtkRenderPass* delegatePass);
  vtkRenderPass* GetDelegatePass();
  ///@}

  ///@{
  /**
   * Linear supersample factor per axis, clamped to [1.0, 4.0]; non-finite
   * values are rejected. Defaults to sqrt(5). The framebuffer is reallocated on
   * the next frame when it changes. Does not auto-adjust PrimitiveScaleFactor.
   */
  virtual void SetSupersampleFactor(double factor);
  vtkGetMacro(SupersampleFactor, double);
  ///@}

  ///@{
  /**
   * Scale applied to point and line widths while the supersampled framebuffer
   * is active. GL specifies those widths in render-target pixels, so without
   * it they thin by the supersample factor. Set it equal to the factor to keep
   * widths visually constant; 1.0 disables compensation.
   */
  virtual void SetPrimitiveScaleFactor(double scale);
  vtkGetMacro(PrimitiveScaleFactor, double);
  ///@}

  ///@{
  /**
   * Resolve the supersampled depth into the window framebuffer's depth
   * attachment after the scene render (see the class documentation). On by
   * default; off leaves the window depth unwritten under this pass.
   */
  vtkSetMacro(ResolveDepth, bool);
  vtkGetMacro(ResolveDepth, bool);
  vtkBooleanMacro(ResolveDepth, bool);
  ///@}

protected:
  pvSSAAVolumePass();
  ~pvSSAAVolumePass() override;

  /**
   * Factor for this frame, clamped so factor * axis stays within the GL
   * maximum texture size. Warns once if clamped.
   */
  double EffectiveFactor(int width, int height);

  vtkRenderPass* DelegatePass;
  vtkOpenGLFramebufferObject* FrameBufferObject;
  vtkTextureObject* Pass1;
  vtkTextureObject* Pass2;
  vtkTextureObject* DepthTex;
  vtkOpenGLHelper* SSAAProgram;
  vtkOpenGLHelper* DepthResolveProgram;

  double SupersampleFactor;
  double PrimitiveScaleFactor;
  bool MaxSizeWarningIssued;
  int DepthTexFormat;
  bool ResolveDepth;

private:
  pvSSAAVolumePass(const pvSSAAVolumePass&) = delete;
  void operator=(const pvSSAAVolumePass&) = delete;
};

#endif
