#include "pvRenderPassChain.h"

#include "pvPropKeyFilterPass.h"
#include "pvSSAAVolumePass.h"

#include "vtkCameraPass.h"
#include "vtkDepthOfFieldPass.h"
#include "vtkDualDepthPeelingPass.h"
#include "vtkEDLShading.h"
#include "vtkGaussianBlurPass.h"
#include "vtkImageProcessingPass.h"
#include "vtkLightsPass.h"
#include "vtkNew.h"
#include "vtkObjectFactory.h"
#include "vtkOpaquePass.h"
#include "vtkOverlayPass.h"
#include "vtkRenderPassCollection.h"
#include "vtkRenderer.h"
#include "vtkSSAOPass.h"
#include "vtkSequencePass.h"
// vtkShadowMapPass.h only forward-declares the baker, which is added to the
// sequence alongside the shadow pass.
#include "vtkShadowMapBakerPass.h"
#include "vtkShadowMapPass.h"
#include "vtkTranslucentPass.h"
#include "vtkVolumetricPass.h"

#include <cmath>

vtkStandardNewMacro(pvRenderPassChain);

namespace
{

// The geometry stages of vtkRenderStepsPass without its camera pass (which
// clears) and without the overlay stage (which Build appends at the top).
vtkSmartPointer<vtkSequencePass> MakeGeometrySteps()
{
  vtkNew<vtkRenderPassCollection> collection;
  vtkNew<vtkLightsPass> lights;
  vtkNew<vtkOpaquePass> opaque;
  vtkNew<vtkTranslucentPass> translucent;
  vtkNew<vtkVolumetricPass> volumetric;
  collection->AddItem(lights);
  collection->AddItem(opaque);
  collection->AddItem(translucent);
  collection->AddItem(volumetric);
  vtkSmartPointer<vtkSequencePass> sequence = vtkSmartPointer<vtkSequencePass>::New();
  sequence->SetPasses(collection);
  return sequence;
}

// Scene stage renders every untagged prop through scenePass, then the
// annotation stage renders the tagged ones with a plain camera pass against the
// depth the first stage left. PreserveBuffers on the second stage is required:
// vtkCameraPass clears while the erase flags are on.
vtkSmartPointer<vtkSequencePass> MakeAnnotationSplit(vtkRenderPass* scenePass)
{
  vtkNew<pvPropKeyFilterPass> sceneStage;
  sceneStage->SetChannel(pvPropKeyFilterPass::ChannelAnnotation);
  sceneStage->SetModeToRenderNonMatching();
  sceneStage->SetDelegatePass(scenePass);

  vtkNew<vtkCameraPass> annotationCamera;
  annotationCamera->SetDelegatePass(MakeGeometrySteps());

  vtkNew<pvPropKeyFilterPass> annotationStage;
  annotationStage->SetChannel(pvPropKeyFilterPass::ChannelAnnotation);
  annotationStage->SetModeToRenderMatching();
  annotationStage->PreserveBuffersOn();
  annotationStage->SetDelegatePass(annotationCamera);

  vtkNew<vtkRenderPassCollection> stages;
  stages->AddItem(sceneStage);
  stages->AddItem(annotationStage);

  vtkSmartPointer<vtkSequencePass> sequence = vtkSmartPointer<vtkSequencePass>::New();
  sequence->SetPasses(stages);
  return sequence;
}

} // namespace

//------------------------------------------------------------------------------
pvRenderPassChain::pvRenderPassChain() = default;

//------------------------------------------------------------------------------
pvRenderPassChain::~pvRenderPassChain() = default;

//------------------------------------------------------------------------------
void pvRenderPassChain::SetSsaoRadius(double radius)
{
  if (!std::isfinite(radius) || radius <= 0.0)
  {
    vtkWarningMacro(<< "SsaoRadius must be finite and positive; ignoring " << radius << ".");
    return;
  }
  this->SsaoRadiusExplicit = true;
  if (this->SsaoRadius != radius)
  {
    this->SsaoRadius = radius;
    this->Modified();
  }
}

//------------------------------------------------------------------------------
void pvRenderPassChain::SetSsaoRadiusToDerived()
{
  if (this->SsaoRadiusExplicit)
  {
    this->SsaoRadiusExplicit = false;
    this->Modified();
  }
}

//------------------------------------------------------------------------------
void pvRenderPassChain::SetSsaoBias(double bias)
{
  if (!std::isfinite(bias) || bias < 0.0)
  {
    vtkWarningMacro(<< "SsaoBias must be finite and non-negative; ignoring " << bias << ".");
    return;
  }
  this->SsaoBiasExplicit = true;
  if (this->SsaoBias != bias)
  {
    this->SsaoBias = bias;
    this->Modified();
  }
}

//------------------------------------------------------------------------------
void pvRenderPassChain::SetSsaoBiasToDerived()
{
  if (this->SsaoBiasExplicit)
  {
    this->SsaoBiasExplicit = false;
    this->Modified();
  }
}

//------------------------------------------------------------------------------
bool pvRenderPassChain::SyncDerivedSsao(vtkRenderer* renderer)
{
  if ((this->SsaoRadiusExplicit && this->SsaoBiasExplicit) || renderer == nullptr)
  {
    return false;
  }

  double bounds[6];
  renderer->ComputeVisiblePropBounds(bounds);
  if (bounds[0] > bounds[1])
  {
    return false;
  }
  const double dx = bounds[1] - bounds[0];
  const double dy = bounds[3] - bounds[2];
  const double dz = bounds[5] - bounds[4];
  const double diagonal = std::sqrt(dx * dx + dy * dy + dz * dz);
  if (!(diagonal > 0.0) || !std::isfinite(diagonal))
  {
    return false;
  }

  this->SsaoBoundsDiagonal = diagonal;
  bool changed = false;
  if (!this->SsaoRadiusExplicit)
  {
    const double radius = this->SsaoRadiusScale * diagonal;
    changed = changed || radius != this->SsaoRadius;
    this->SsaoRadius = radius;
  }
  if (!this->SsaoBiasExplicit)
  {
    const double bias = this->SsaoRadius / 20.0;
    changed = changed || bias != this->SsaoBias;
    this->SsaoBias = bias;
  }
  if (changed)
  {
    this->Modified();
  }
  return changed;
}

//------------------------------------------------------------------------------
bool pvRenderPassChain::GetRequiresCustomPassChain() const
{
  return this->Shadows || this->EDL || this->DepthOfField || this->Blur || this->SSAO ||
    this->AntiAliasing || this->BasePassProvided || this->TranslucentPass != nullptr ||
    this->PostPass != nullptr;
}

//------------------------------------------------------------------------------
bool pvRenderPassChain::GetUsesManualDepthPeeling() const
{
  return this->DepthPeeling && this->TranslucentPass == nullptr &&
    this->GetRequiresCustomPassChain();
}

//------------------------------------------------------------------------------
bool pvRenderPassChain::GetUsesBuiltinDepthPeeling() const
{
  return this->DepthPeeling && !this->GetRequiresCustomPassChain();
}

//------------------------------------------------------------------------------
// Forgotten in two groups because they are built in two groups: a caller that
// interposes its own pass calls BuildSceneBase itself, and a single Forget at
// the top of Build would discard the passes that call just recorded.
void pvRenderPassChain::ForgetBasePasses()
{
  this->SceneBasePassOwned = nullptr;
  this->DepthPeelingPassOwned = nullptr;
  this->ShadowPassOwned = nullptr;

  this->SceneBasePass = nullptr;
  this->DepthPeelingPass = nullptr;
  this->ShadowPass = nullptr;
}

//------------------------------------------------------------------------------
void pvRenderPassChain::ForgetChainPasses()
{
  this->TopPassOwned = nullptr;
  this->ScenePassOwned = nullptr;
  this->SsaoPassOwned = nullptr;
  this->LateStagePassOwned = nullptr;
  this->EDLPassOwned = nullptr;
  this->AnnotationSplitPassOwned = nullptr;
  this->DepthOfFieldPassOwned = nullptr;
  this->BlurPassOwned = nullptr;
  this->SsaaPassOwned = nullptr;

  this->TopPass = nullptr;
  this->ScenePass = nullptr;
  this->SsaoPass = nullptr;
  this->EDLPass = nullptr;
  this->AnnotationSplitPass = nullptr;
  this->DepthOfFieldPass = nullptr;
  this->BlurPass = nullptr;
  this->SsaaPass = nullptr;
}

//------------------------------------------------------------------------------
vtkRenderPass* pvRenderPassChain::BuildSceneBase()
{
  this->ForgetBasePasses();

  // The stages of vtkRenderStepsPass laid out by hand: that pass wraps them in
  // a camera pass of its own, which clears the buffers, so nothing rendered
  // ahead of it in a sequence (the shadow pass) would survive.
  vtkNew<vtkRenderPassCollection> collection;
  vtkNew<vtkLightsPass> lights;
  collection->AddItem(lights);

  if (this->Shadows)
  {
    vtkNew<vtkShadowMapPass> shadows;
    collection->AddItem(shadows->GetShadowMapBakerPass());
    collection->AddItem(shadows);
    this->ShadowPassOwned = shadows;
    this->ShadowPass = shadows;
  }
  else
  {
    vtkNew<vtkOpaquePass> opaque;
    collection->AddItem(opaque);
  }

  if (!this->SSAO)
  {
    this->AddTranslucentAndVolumetricStages(collection);
  }

  vtkNew<vtkSequencePass> sequence;
  sequence->SetPasses(collection);

  vtkSmartPointer<vtkCameraPass> camera = vtkSmartPointer<vtkCameraPass>::New();
  camera->SetDelegatePass(sequence);

  this->SceneBasePassOwned = camera;
  this->SceneBasePass = camera;
  return camera;
}

//------------------------------------------------------------------------------
void pvRenderPassChain::AddTranslucentAndVolumetricStages(vtkRenderPassCollection* collection)
{
  if (this->TranslucentPass != nullptr)
  {
    collection->AddItem(this->TranslucentPass);
  }
  else if (this->GetUsesManualDepthPeeling())
  {
    vtkNew<vtkTranslucentPass> translucent;
    vtkNew<vtkDualDepthPeelingPass> peeling;
    peeling->SetTranslucentPass(translucent);
    peeling->SetMaximumNumberOfPeels(this->DepthPeelingMaximumNumberOfPeels);
    peeling->SetOcclusionRatio(this->DepthPeelingOcclusionRatio);
    collection->AddItem(peeling);
    this->DepthPeelingPassOwned = peeling;
    this->DepthPeelingPass = peeling;
  }
  else
  {
    vtkNew<vtkTranslucentPass> translucent;
    collection->AddItem(translucent);
  }

  vtkNew<vtkVolumetricPass> volumetric;
  collection->AddItem(volumetric);
}

//------------------------------------------------------------------------------
// The translucent and volumetric stages after SSAO, drawn over its output
// through a camera pass that does not clear: see the class documentation.
vtkSmartPointer<vtkRenderPass> pvRenderPassChain::BuildLateStage()
{
  vtkNew<vtkRenderPassCollection> collection;
  this->AddTranslucentAndVolumetricStages(collection);
  vtkNew<vtkSequencePass> steps;
  steps->SetPasses(collection);
  vtkNew<vtkCameraPass> camera;
  camera->SetDelegatePass(steps);

  vtkSmartPointer<pvPropKeyFilterPass> stage = vtkSmartPointer<pvPropKeyFilterPass>::New();
  stage->SetModeToRenderAll();
  stage->PreserveBuffersOn();
  stage->SetDelegatePass(camera);
  return stage;
}

//------------------------------------------------------------------------------
vtkRenderPass* pvRenderPassChain::Build(vtkRenderer* renderer, vtkRenderPass* base)
{
  this->SyncDerivedSsao(renderer);

  const bool custom = this->GetRequiresCustomPassChain();
  vtkSmartPointer<vtkRenderPass> retainedBase = base;
  this->ForgetChainPasses();

  if (!custom)
  {
    this->ForgetBasePasses();
    return nullptr;
  }

  vtkSmartPointer<vtkRenderPass> current = retainedBase;
  if (current == nullptr)
  {
    current = this->BuildSceneBase();
  }
  else
  {
    this->SceneBasePassOwned = current;
    this->SceneBasePass = current;
  }

  if (this->SSAO)
  {
    vtkNew<vtkSSAOPass> ssao;
    ssao->SetRadius(this->SsaoRadius);
    ssao->SetBias(this->SsaoBias);
    ssao->SetKernelSize(this->SsaoKernelSize);
    ssao->SetBlur(this->SsaoBlur);
    if (this->SsaoDepthFormat >= 0)
    {
      ssao->SetDepthFormat(this->SsaoDepthFormat);
    }
    ssao->SetDelegatePass(current);
    this->SsaoPassOwned = ssao;
    this->SsaoPass = ssao;

    // Volumes render in the late stage; kept out of the SSAO pass's prop array
    // so it neither logs "Shading must be enabled" nor touches their keys.
    vtkNew<pvPropKeyFilterPass> opaqueOnly;
    opaqueOnly->SetModeToRenderAll();
    opaqueOnly->ExcludeVolumesOn();
    opaqueOnly->SetDelegatePass(ssao);

    vtkSmartPointer<vtkRenderPass> late = this->BuildLateStage();
    vtkNew<vtkRenderPassCollection> stages;
    stages->AddItem(opaqueOnly);
    stages->AddItem(late);
    vtkNew<vtkSequencePass> sequence;
    sequence->SetPasses(stages);
    current = sequence;
    this->LateStagePassOwned = late;
  }

  if (this->EDL)
  {
    vtkNew<vtkEDLShading> edl;
    edl->SetDelegatePass(current);
    current = edl;
    this->EDLPassOwned = edl;
    this->EDLPass = edl;

    if (this->AnnotationBypass)
    {
      vtkSmartPointer<vtkSequencePass> split = MakeAnnotationSplit(current);
      current = split;
      this->AnnotationSplitPassOwned = split;
      this->AnnotationSplitPass = split;
    }
  }

  if (this->DepthOfField)
  {
    vtkNew<vtkDepthOfFieldPass> dof;
    dof->SetAutomaticFocalDistance(this->DepthOfFieldAutomaticFocalDistance);
    dof->SetDelegatePass(current);
    current = dof;
    this->DepthOfFieldPassOwned = dof;
    this->DepthOfFieldPass = dof;
  }

  if (this->Blur)
  {
    vtkNew<vtkGaussianBlurPass> blur;
    blur->SetDelegatePass(current);
    current = blur;
    this->BlurPassOwned = blur;
    this->BlurPass = blur;
  }

  if (this->PostPass != nullptr)
  {
    this->PostPass->SetDelegatePass(current);
    current = this->PostPass;
  }

  // The screen-space passes composite over the whole window, so on a subplot
  // they wipe the other tiles; the SSAA pass at 1x confines them to their own.
  const bool screenSpace = this->EDL || this->DepthOfField || this->Blur;
  if (this->AntiAliasing || screenSpace)
  {
    vtkNew<pvSSAAVolumePass> ssaa;
    const double factor = this->AntiAliasing ? this->SsaaFactor : 1.0;
    ssaa->SetSupersampleFactor(factor);
    ssaa->SetPrimitiveScaleFactor(factor);
    ssaa->SetDelegatePass(current);
    current = ssaa;
    this->SsaaPassOwned = ssaa;
    this->SsaaPass = ssaa;
  }

  this->ScenePassOwned = current;
  this->ScenePass = current;

  // The overlay stage last, at the window: see the class documentation.
  vtkNew<vtkOverlayPass> overlay;
  vtkNew<vtkRenderPassCollection> top;
  top->AddItem(current);
  top->AddItem(overlay);
  vtkNew<vtkSequencePass> sequence;
  sequence->SetPasses(top);
  this->TopPassOwned = sequence;
  this->TopPass = sequence;
  return sequence;
}

//------------------------------------------------------------------------------
void pvRenderPassChain::ReleaseGraphicsResources(vtkWindow* window)
{
  if (window == nullptr)
  {
    vtkWarningMacro(<< "ReleaseGraphicsResources needs the window whose context owns the "
                       "passes; ignoring a null one. Use Forget() to drop the chain without "
                       "a context.");
    return;
  }

  // Walk the members rather than the delegate graph: a caller-supplied base is
  // released by whoever built it, and the structural passes own no GL resources.
  vtkRenderPass* const passes[] = { this->SsaaPassOwned, this->BlurPassOwned,
    this->DepthOfFieldPassOwned, this->EDLPassOwned, this->SsaoPassOwned, this->ShadowPassOwned,
    this->DepthPeelingPassOwned };
  for (vtkRenderPass* pass : passes)
  {
    if (pass != nullptr)
    {
      pass->ReleaseGraphicsResources(window);
    }
  }
}

//------------------------------------------------------------------------------
void pvRenderPassChain::Forget()
{
  this->ForgetChainPasses();
  this->ForgetBasePasses();
}

//------------------------------------------------------------------------------
void pvRenderPassChain::PrintSelf(ostream& os, vtkIndent indent)
{
  this->Superclass::PrintSelf(os, indent);
  os << indent << "DepthPeeling: " << this->DepthPeeling << "\n";
  os << indent << "DepthPeelingMaximumNumberOfPeels: " << this->DepthPeelingMaximumNumberOfPeels
     << "\n";
  os << indent << "DepthPeelingOcclusionRatio: " << this->DepthPeelingOcclusionRatio << "\n";
  os << indent << "Shadows: " << this->Shadows << "\n";
  os << indent << "EDL: " << this->EDL << "\n";
  os << indent << "AnnotationBypass: " << this->AnnotationBypass << "\n";
  os << indent << "SSAO: " << this->SSAO << "\n";
  os << indent << "SsaoRadius: " << this->SsaoRadius
     << (this->SsaoRadiusExplicit ? " (explicit)" : " (derived)") << "\n";
  os << indent << "SsaoBias: " << this->SsaoBias
     << (this->SsaoBiasExplicit ? " (explicit)" : " (derived)") << "\n";
  os << indent << "SsaoRadiusScale: " << this->SsaoRadiusScale << "\n";
  os << indent << "SsaoBoundsDiagonal: " << this->SsaoBoundsDiagonal << "\n";
  os << indent << "SsaoKernelSize: " << this->SsaoKernelSize << "\n";
  os << indent << "SsaoBlur: " << this->SsaoBlur << "\n";
  os << indent << "SsaoDepthFormat: " << this->SsaoDepthFormat << "\n";
  os << indent << "DepthOfField: " << this->DepthOfField << "\n";
  os << indent << "DepthOfFieldAutomaticFocalDistance: " << this->DepthOfFieldAutomaticFocalDistance
     << "\n";
  os << indent << "Blur: " << this->Blur << "\n";
  os << indent << "AntiAliasing: " << this->AntiAliasing << "\n";
  os << indent << "SsaaFactor: " << this->SsaaFactor << "\n";
  os << indent << "TranslucentPass: " << this->TranslucentPass.GetPointer() << "\n";
  os << indent << "PostPass: " << this->PostPass.GetPointer() << "\n";
  os << indent << "BasePassProvided: " << this->BasePassProvided << "\n";
}
