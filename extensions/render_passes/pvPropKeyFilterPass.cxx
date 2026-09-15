#include "pvPropKeyFilterPass.h"

#include "vtkInformation.h"
#include "vtkInformationIntegerKey.h"
#include "vtkNew.h"
#include "vtkObjectFactory.h"
#include "vtkOpenGLRenderWindow.h"
#include "vtkOpenGLState.h"
#include "vtkProp.h"
#include "vtkRenderState.h"
#include "vtkRenderer.h"
#include "vtkVolume.h"

#include "vtk_glad.h"

#include <cassert>
#include <vector>

vtkStandardNewMacro(pvPropKeyFilterPass);
vtkInformationKeyMacro(pvPropKeyFilterPass, FILTER_KEY, Integer);
vtkInformationKeyMacro(pvPropKeyFilterPass, CONSIDERED_KEY, Integer);

namespace
{

int FilterMask(vtkProp* prop)
{
  vtkInformation* keys = prop != nullptr ? prop->GetPropertyKeys() : nullptr;
  if (keys == nullptr || keys->Has(pvPropKeyFilterPass::FILTER_KEY()) == 0)
  {
    return 0;
  }
  return keys->Get(pvPropKeyFilterPass::FILTER_KEY());
}

int ConsideredMask(vtkProp* prop)
{
  vtkInformation* keys = prop != nullptr ? prop->GetPropertyKeys() : nullptr;
  if (keys == nullptr)
  {
    return 0;
  }
  if (keys->Has(pvPropKeyFilterPass::CONSIDERED_KEY()) != 0)
  {
    return keys->Get(pvPropKeyFilterPass::CONSIDERED_KEY());
  }
  // A bare FILTER_KEY() means a channel-0 decision, including the value-0
  // "deliberately not matching" case, so presence rather than value answers.
  return keys->Has(pvPropKeyFilterPass::FILTER_KEY()) != 0
    ? pvPropKeyFilterPass::ChannelBit(pvPropKeyFilterPass::ChannelAnnotation)
    : 0;
}

} // anonymous namespace

//------------------------------------------------------------------------------
pvPropKeyFilterPass::pvPropKeyFilterPass() = default;

//------------------------------------------------------------------------------
pvPropKeyFilterPass::~pvPropKeyFilterPass()
{
  if (this->DelegatePass != nullptr)
  {
    this->DelegatePass->Delete();
  }
}

//------------------------------------------------------------------------------
int pvPropKeyFilterPass::ChannelBit(int channel)
{
  if (channel < ChannelAnnotation || channel > ChannelMaximum)
  {
    vtkGenericWarningMacro(<< "Channel " << channel << " is outside [" << ChannelAnnotation << ", "
                           << ChannelMaximum << "]; no prop can be tagged on it.");
    return 0;
  }
  return 1 << channel;
}

//------------------------------------------------------------------------------
bool pvPropKeyFilterPass::IsPropMatching(vtkProp* prop, int channel)
{
  const int bit = pvPropKeyFilterPass::ChannelBit(channel);
  return bit != 0 && (FilterMask(prop) & bit) != 0;
}

//------------------------------------------------------------------------------
bool pvPropKeyFilterPass::IsPropConsidered(vtkProp* prop, int channel)
{
  const int bit = pvPropKeyFilterPass::ChannelBit(channel);
  return bit != 0 && (ConsideredMask(prop) & bit) != 0;
}

//------------------------------------------------------------------------------
void pvPropKeyFilterPass::SetPropMatching(vtkProp* prop, bool matching, int channel)
{
  const int bit = pvPropKeyFilterPass::ChannelBit(channel);
  if (prop == nullptr || bit == 0)
  {
    return;
  }

  // Read both masks before writing either: ConsideredMask() falls back to
  // FILTER_KEY()'s presence.
  const int filter = FilterMask(prop);
  const int considered = ConsideredMask(prop);

  vtkInformation* keys = prop->GetPropertyKeys();
  if (keys == nullptr)
  {
    vtkNew<vtkInformation> created;
    prop->SetPropertyKeys(created);
    keys = created;
  }
  keys->Set(pvPropKeyFilterPass::FILTER_KEY(), matching ? (filter | bit) : (filter & ~bit));
  keys->Set(pvPropKeyFilterPass::CONSIDERED_KEY(), considered | bit);
}

//------------------------------------------------------------------------------
void pvPropKeyFilterPass::ClearPropDecision(vtkProp* prop, int channel)
{
  const int bit = pvPropKeyFilterPass::ChannelBit(channel);
  if (prop == nullptr || bit == 0)
  {
    return;
  }
  vtkInformation* keys = prop->GetPropertyKeys();
  if (keys == nullptr ||
    (keys->Has(pvPropKeyFilterPass::FILTER_KEY()) == 0 &&
      keys->Has(pvPropKeyFilterPass::CONSIDERED_KEY()) == 0))
  {
    return;
  }

  const int filter = FilterMask(prop);
  const int considered = ConsideredMask(prop);
  keys->Set(pvPropKeyFilterPass::FILTER_KEY(), filter & ~bit);
  keys->Set(pvPropKeyFilterPass::CONSIDERED_KEY(), considered & ~bit);
}

//------------------------------------------------------------------------------
void pvPropKeyFilterPass::SetDelegatePass(vtkRenderPass* delegatePass)
{
  if (this->DelegatePass == delegatePass)
  {
    return;
  }
  if (this->DelegatePass != nullptr)
  {
    this->DelegatePass->Delete();
  }
  this->DelegatePass = delegatePass;
  if (this->DelegatePass != nullptr)
  {
    this->DelegatePass->Register(this);
  }
  this->Modified();
}

//------------------------------------------------------------------------------
vtkRenderPass* pvPropKeyFilterPass::GetDelegatePass()
{
  return this->DelegatePass;
}

//------------------------------------------------------------------------------
void pvPropKeyFilterPass::Render(const vtkRenderState* s)
{
  assert("pre: s_exists" && s != nullptr);

  this->NumberOfRenderedProps = 0;

  if (this->DelegatePass == nullptr)
  {
    vtkErrorMacro("No delegate pass; nothing rendered.");
    return;
  }

  const int count = s->GetPropArrayCount();
  vtkProp** props = s->GetPropArray();
  const bool keepMatching = this->Mode == RenderMatching;
  const bool keepAll = this->Mode == RenderAll;

  std::vector<vtkProp*> subset;
  subset.reserve(static_cast<size_t>(count < 0 ? 0 : count));
  for (int i = 0; i < count; ++i)
  {
    if (this->ExcludeVolumes && vtkVolume::SafeDownCast(props[i]) != nullptr)
    {
      continue;
    }
    if (keepAll || pvPropKeyFilterPass::IsPropMatching(props[i], this->Channel) == keepMatching)
    {
      subset.push_back(props[i]);
    }
  }

  vtkRenderState s2(s->GetRenderer());
  s2.SetPropArrayAndCount(
    subset.empty() ? nullptr : subset.data(), static_cast<int>(subset.size()));
  s2.SetFrameBuffer(s->GetFrameBuffer());
  s2.SetRequiredKeys(s->GetRequiredKeys());

  vtkRenderer* renderer = s->GetRenderer();
  const vtkTypeBool erase = renderer != nullptr ? renderer->GetErase() : 0;
  const bool suppressErase = this->PreserveBuffers && renderer != nullptr && erase != 0;
  if (suppressErase)
  {
    renderer->SetErase(0);
  }

  this->RenderDelegate(&s2, renderer);

  if (suppressErase)
  {
    renderer->SetErase(erase);
  }

  this->NumberOfRenderedProps += this->DelegatePass->GetNumberOfRenderedProps();
}

//------------------------------------------------------------------------------
void pvPropKeyFilterPass::RenderDelegate(const vtkRenderState* s, vtkRenderer* renderer)
{
  vtkOpenGLRenderWindow* window = renderer != nullptr
    ? vtkOpenGLRenderWindow::SafeDownCast(renderer->GetRenderWindow())
    : nullptr;
  vtkOpenGLState* ostate = window != nullptr ? window->GetState() : nullptr;

  if (!this->PreserveBuffers || ostate == nullptr)
  {
    this->DelegatePass->Render(s);
    return;
  }

  // A screen-space pass composes with GL_BLEND off, so a stage after it would
  // draw an alpha-emitting actor as opaque colour. Restore the blending VTK
  // renders with outside a pass chain.
  vtkOpenGLState::ScopedglEnableDisable blendSaver(ostate, GL_BLEND);
  vtkOpenGLState::ScopedglBlendFuncSeparate blendFuncSaver(ostate);
  ostate->vtkglEnable(GL_BLEND);
  ostate->vtkglBlendFuncSeparate(
    GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA);

  this->DelegatePass->Render(s);
}

//------------------------------------------------------------------------------
void pvPropKeyFilterPass::ReleaseGraphicsResources(vtkWindow* w)
{
  assert("pre: w_exists" && w != nullptr);

  this->Superclass::ReleaseGraphicsResources(w);

  if (this->DelegatePass != nullptr)
  {
    this->DelegatePass->ReleaseGraphicsResources(w);
  }
}

//------------------------------------------------------------------------------
void pvPropKeyFilterPass::PrintSelf(ostream& os, vtkIndent indent)
{
  this->Superclass::PrintSelf(os, indent);

  const char* mode = this->Mode == RenderMatching ? "RenderMatching"
    : this->Mode == RenderNonMatching             ? "RenderNonMatching"
                                                  : "RenderAll";
  os << indent << "Mode: " << mode << "\n";
  os << indent << "Channel: " << this->Channel << "\n";
  os << indent << "PreserveBuffers: " << (this->PreserveBuffers ? "on" : "off") << "\n";
  os << indent << "ExcludeVolumes: " << (this->ExcludeVolumes ? "on" : "off") << "\n";
  os << indent << "DelegatePass: ";
  if (this->DelegatePass != nullptr)
  {
    os << "\n";
    this->DelegatePass->PrintSelf(os, indent.GetNextIndent());
  }
  else
  {
    os << "(none)\n";
  }
}
