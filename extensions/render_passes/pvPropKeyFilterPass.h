/**
 * @class   pvPropKeyFilterPass
 * @brief   Render a delegate over only the props that carry (or do not carry) a tag.
 *
 * A screen-space pass (@c vtkEDLShading, @c vtkSSAOPass) shades whatever geometry
 * reached its buffers, annotation geometry included: axes and other line
 * geometry read as depth discontinuities under EDL and composite as dark
 * outlines.
 *
 * VTK's own prop filter (@c vtkRenderState::SetRequiredKeys) only expresses
 * "props that have ALL of these keys", so excluding a small tagged set would mean
 * tagging every other prop. This pass partitions the render state's prop array
 * instead and renders its delegate over one half. Every stock stage pass walks
 * that array, so the subset propagates through an arbitrary delegate chain.
 *
 * @c FILTER_KEY() holds a channel bitmask and each pass instance filters on one
 * channel, so independent partitions compose in one sequence:
 *
 * @code
 * sequence
 *   +- pvPropKeyFilterPass (RenderNonMatching)                  -> vtkEDLShading -> camera
 *   +- pvPropKeyFilterPass (RenderMatching, PreserveBuffers on) -> camera
 * @endcode
 *
 * The tagged props are drawn after the screen-space pass has composed, against
 * the depth it wrote, so they still occlude and are occluded correctly. The
 * delegate is always rendered, even on an empty subset, so the framebuffer and
 * camera setup it performs still happens.
 */

#ifndef pvPropKeyFilterPass_h
#define pvPropKeyFilterPass_h

#include "PyVistaRenderPassesModule.h"
#include "vtkRenderPass.h"

class vtkInformationIntegerKey;
class vtkProp;
class vtkRenderer;

class PYVISTARENDERPASSES_EXPORT pvPropKeyFilterPass : public vtkRenderPass
{
public:
  static pvPropKeyFilterPass* New();
  vtkTypeMacro(pvPropKeyFilterPass, vtkRenderPass);
  void PrintSelf(ostream& os, vtkIndent indent) override;

  enum Channels
  {
    ChannelAnnotation = 0,
    // Highest usable channel: the mask is a signed int and bit 31 is its sign bit.
    ChannelMaximum = 30
  };

  /**
   * Key tagging a prop for this pass, holding a channel bitmask. Prefer
   * @c SetPropMatching, which edits one channel's bit and leaves the others
   * alone. A prop with no key has no bits and is non-matching on every channel.
   */
  static vtkInformationIntegerKey* FILTER_KEY();

  /**
   * Bitmask of the channels a caller has explicitly decided about, matching or
   * not. A cleared FILTER_KEY() bit is indistinguishable from one nobody set,
   * and code that auto-tags props by type needs to tell "considered
   * and rejected" from "never considered". A prop carrying FILTER_KEY() but no
   * CONSIDERED_KEY() reads as decided on channel 0 only.
   */
  static vtkInformationIntegerKey* CONSIDERED_KEY();

  /**
   * `1 << channel`, or 0 for a channel outside [ChannelAnnotation, ChannelMaximum]
   * so an out-of-range query never aliases a real channel.
   */
  static int ChannelBit(int channel);

  /**
   * Whether @a prop is tagged as matching on @a channel. Null-safe.
   */
  static bool IsPropMatching(vtkProp* prop, int channel = ChannelAnnotation);

  /**
   * Whether @a channel of @a prop has been decided either way. Null-safe.
   */
  static bool IsPropConsidered(vtkProp* prop, int channel = ChannelAnnotation);

  /**
   * Record @a prop as matching (or deliberately not matching) on @a channel,
   * creating its property keys on demand. Only that channel's bit moves.
   * Out-of-range channels and a null @a prop are ignored.
   */
  static void SetPropMatching(vtkProp* prop, bool matching, int channel = ChannelAnnotation);

  /**
   * Undo @c SetPropMatching for @a channel, returning the prop to "never
   * considered" there. Other channels and other property keys are untouched.
   */
  static void ClearPropDecision(vtkProp* prop, int channel = ChannelAnnotation);

  enum Modes
  {
    RenderMatching = 0,
    RenderNonMatching = 1,
    RenderAll = 2
  };

  ///@{
  /**
   * Which half of the partition the delegate renders. @c RenderMatching (the
   * default) renders only props tagged on this pass's channel. @c RenderAll
   * does not partition: it exists for @c PreserveBuffers, a camera-pass stage
   * over every prop that draws over the frame instead of clearing it.
   */
  vtkSetClampMacro(Mode, int, RenderMatching, RenderAll);
  vtkGetMacro(Mode, int);
  void SetModeToRenderMatching() { this->SetMode(RenderMatching); }
  void SetModeToRenderNonMatching() { this->SetMode(RenderNonMatching); }
  void SetModeToRenderAll() { this->SetMode(RenderAll); }
  ///@}

  ///@{
  /**
   * Channel of the FILTER_KEY() bitmask this pass partitions on. Clamped to
   * [ChannelAnnotation, ChannelMaximum].
   */
  vtkSetClampMacro(Channel, int, ChannelAnnotation, ChannelMaximum);
  vtkGetMacro(Channel, int);
  ///@}

  ///@{
  /**
   * Composite onto the buffers a previous stage left instead of clearing them.
   *
   * @c vtkCameraPass calls @c vtkRenderer::Clear whenever the erase flags are
   * on, wiping both color and depth, so a second stage built the obvious way
   * erases the first. With this on the pass drops the renderer's erase flag
   * around the delegate render and restores standard alpha blending, which a
   * screen-space pass leaves disabled after its compose. Off by default.
   */
  vtkSetMacro(PreserveBuffers, bool);
  vtkGetMacro(PreserveBuffers, bool);
  vtkBooleanMacro(PreserveBuffers, bool);
  ///@}

  ///@{
  /**
   * Drop @c vtkVolume props from the array the delegate sees, on top of the
   * mode. @c vtkSSAOPass inspects every volume in its prop array, whether or
   * not a volumetric stage below it draws one, and logs an error for each
   * unshaded one. Off by default.
   */
  vtkSetMacro(ExcludeVolumes, bool);
  vtkGetMacro(ExcludeVolumes, bool);
  vtkBooleanMacro(ExcludeVolumes, bool);
  ///@}

  ///@{
  /**
   * Pass rendered over the filtered subset. Required.
   */
  void SetDelegatePass(vtkRenderPass* delegatePass);
  vtkRenderPass* GetDelegatePass();
  ///@}

  void Render(const vtkRenderState* s) override;
  void ReleaseGraphicsResources(vtkWindow* w) override;

protected:
  pvPropKeyFilterPass();
  ~pvPropKeyFilterPass() override;

  void RenderDelegate(const vtkRenderState* s, vtkRenderer* renderer);

  vtkRenderPass* DelegatePass = nullptr;
  int Mode = RenderMatching;
  int Channel = ChannelAnnotation;
  bool PreserveBuffers = false;
  bool ExcludeVolumes = false;

private:
  pvPropKeyFilterPass(const pvPropKeyFilterPass&) = delete;
  void operator=(const pvPropKeyFilterPass&) = delete;
};

#endif
