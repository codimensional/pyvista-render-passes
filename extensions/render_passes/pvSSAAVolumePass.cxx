// SPDX-FileCopyrightText: Copyright (c) 2026 CoDimensional PBC
// SPDX-FileCopyrightText: Copyright (c) Ken Martin, Will Schroeder, Bill Lorensen
// SPDX-License-Identifier: MIT AND BSD-3-Clause
//
// Derived from VTK's vtkSSAAPass.cxx, vtkSSAAPassFS.glsl and
// vtkTextureObjectVS.glsl (BSD-3-Clause). See LICENSE for the VTK notice.

#include "pvSSAAVolumePass.h"

#include "vtkActor.h"
#include "vtkActorCollection.h"
#include "vtkObjectFactory.h"
#include "vtkOpenGLError.h"
#include "vtkOpenGLFramebufferObject.h"
#include "vtkOpenGLHelper.h"
#include "vtkOpenGLRenderWindow.h"
#include "vtkOpenGLShaderCache.h"
#include "vtkOpenGLState.h"
#include "vtkOpenGLVertexArrayObject.h"
#include "vtkProperty.h"
#include "vtkRenderPass.h"
#include "vtkRenderState.h"
#include "vtkRenderer.h"
#include "vtkShaderProgram.h"
#include "vtkTextureObject.h"
#include "vtk_glad.h"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <unordered_set>
#include <vector>

namespace
{
// Pass-through vertex shader matching stock vtkTextureObjectVS: RenderQuad and
// CopyToFrameBuffer bind the quad as "vertexMC"/"tcoordMC".
const char* SSAAVolumePassVS = R"GLSL(//VTK::System::Dec
in vec4 vertexMC;
in vec2 tcoordMC;
out vec2 tcoordVC;
void main()
{
  tcoordVC = tcoordMC;
  gl_Position = vertexMC;
}
)GLSL";

// Separable downsample, one axis per invocation; the same Lanczos kernel as
// stock vtkSSAAPassFS, which credits Brad Larson's sample code. Four taps span
// +/-1.5 destination pixels, hence the 3/8 texel offset the caller supplies
// per axis.
const char* SSAAVolumePassFS = R"GLSL(//VTK::System::Dec
uniform sampler2D source;
//VTK::Output::Dec
uniform float texelWidthOffset;
uniform float texelHeightOffset;
in vec2 tcoordVC;
void main()
{
  vec2 firstOffset = vec2(texelWidthOffset, texelHeightOffset);
  vec4 fragmentColor = texture2D(source, tcoordVC) * 0.38026;
  fragmentColor += texture2D(source, tcoordVC - firstOffset) * 0.27667;
  fragmentColor += texture2D(source, tcoordVC + firstOffset) * 0.27667;
  fragmentColor += texture2D(source, tcoordVC - 2.0*firstOffset) * 0.08074;
  fragmentColor += texture2D(source, tcoordVC + 2.0*firstOffset) * 0.08074;
  fragmentColor += texture2D(source, tcoordVC - 3.0*firstOffset) * -0.02612;
  fragmentColor += texture2D(source, tcoordVC + 3.0*firstOffset) * -0.02612;
  fragmentColor += texture2D(source, tcoordVC - 4.0*firstOffset) * -0.02143;
  fragmentColor += texture2D(source, tcoordVC + 4.0*firstOffset) * -0.02143;
  gl_FragData[0] = fragmentColor;
}
)GLSL";

// Depth must not be averaged: a blended depth across an occlusion edge is a
// surface that does not exist. Reduce the footprint with min() instead.
const char* SSAAVolumeDepthResolveFS = R"GLSL(//VTK::System::Dec
uniform sampler2D depthSource;
uniform vec2 sourceStep;  // 1.0 / supersampled size, in texture coords
uniform ivec2 footprint;  // supersamples per window pixel, per axis (>= 1)
//VTK::Output::Dec
in vec2 tcoordVC;
void main()
{
  vec2 base = tcoordVC - 0.5 * vec2(footprint) * sourceStep + 0.5 * sourceStep;
  float d = 1.0;
  for (int j = 0; j < footprint.y; ++j)
  {
    for (int i = 0; i < footprint.x; ++i)
    {
      vec2 uv = base + vec2(float(i), float(j)) * sourceStep;
      d = min(d, texture2D(depthSource, uv).r);
    }
  }
  gl_FragDepth = d;
}
)GLSL";

struct PrimitiveState
{
  vtkProperty* Property = nullptr;
  double PointSize = 1.0;
  double LineWidth = 1.0;
};

class ScopedPrimitiveScale
{
public:
  ScopedPrimitiveScale(vtkRenderer* renderer, double scale)
  {
    if (renderer == nullptr || scale == 1.0)
    {
      return;
    }

    std::unordered_set<vtkProperty*> seen;
    vtkActorCollection* actors = renderer->GetActors();
    actors->InitTraversal();
    vtkActor* actor = actors->GetNextActor();
    while (actor != nullptr)
    {
      vtkProperty* prop = actor->GetProperty();
      if (prop != nullptr && seen.insert(prop).second)
      {
        const double pointSize = prop->GetPointSize();
        const double lineWidth = prop->GetLineWidth();
        this->States.push_back({ prop, pointSize, lineWidth });
        prop->SetPointSize(std::max(1.0, pointSize * scale));
        prop->SetLineWidth(std::max(1.0, lineWidth * scale));
      }
      actor = actors->GetNextActor();
    }
  }

  ~ScopedPrimitiveScale()
  {
    for (const PrimitiveState& state : this->States)
    {
      state.Property->SetPointSize(state.PointSize);
      state.Property->SetLineWidth(state.LineWidth);
    }
  }

  ScopedPrimitiveScale(const ScopedPrimitiveScale&) = delete;
  void operator=(const ScopedPrimitiveScale&) = delete;

private:
  std::vector<PrimitiveState> States;
};
}

vtkStandardNewMacro(pvSSAAVolumePass);

pvSSAAVolumePass::pvSSAAVolumePass()
{
  this->DelegatePass = nullptr;
  this->FrameBufferObject = nullptr;
  this->Pass1 = nullptr;
  this->Pass2 = nullptr;
  this->DepthTex = nullptr;
  this->SSAAProgram = nullptr;
  this->DepthResolveProgram = nullptr;
  this->SupersampleFactor = std::sqrt(5.0);
  this->PrimitiveScaleFactor = std::sqrt(5.0);
  this->MaxSizeWarningIssued = false;
  this->DepthTexFormat = -1;
  this->ResolveDepth = true;
}

pvSSAAVolumePass::~pvSSAAVolumePass()
{
  if (this->DelegatePass != nullptr)
  {
    this->DelegatePass->Delete();
    this->DelegatePass = nullptr;
  }
  if (this->FrameBufferObject != nullptr)
  {
    this->FrameBufferObject->Delete();
    this->FrameBufferObject = nullptr;
  }
  if (this->Pass1 != nullptr)
  {
    this->Pass1->Delete();
    this->Pass1 = nullptr;
  }
  if (this->Pass2 != nullptr)
  {
    this->Pass2->Delete();
    this->Pass2 = nullptr;
  }
  if (this->DepthTex != nullptr)
  {
    this->DepthTex->Delete();
    this->DepthTex = nullptr;
  }
  delete this->SSAAProgram;
  this->SSAAProgram = nullptr;
  delete this->DepthResolveProgram;
  this->DepthResolveProgram = nullptr;
}

void pvSSAAVolumePass::SetSupersampleFactor(double factor)
{
  if (!std::isfinite(factor))
  {
    vtkErrorMacro("SupersampleFactor must be finite.");
    return;
  }

  const double clamped = std::min(4.0, std::max(1.0, factor));
  if (this->SupersampleFactor != clamped)
  {
    this->SupersampleFactor = clamped;
    this->Modified();
  }
}

void pvSSAAVolumePass::SetPrimitiveScaleFactor(double scale)
{
  if (!std::isfinite(scale))
  {
    vtkErrorMacro("PrimitiveScaleFactor must be finite.");
    return;
  }

  const double clamped = std::max(1.0, scale);
  if (this->PrimitiveScaleFactor != clamped)
  {
    this->PrimitiveScaleFactor = clamped;
    this->Modified();
  }
}

void pvSSAAVolumePass::SetDelegatePass(vtkRenderPass* delegatePass)
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

vtkRenderPass* pvSSAAVolumePass::GetDelegatePass()
{
  return this->DelegatePass;
}

double pvSSAAVolumePass::EffectiveFactor(int width, int height)
{
  const int maxAxis = std::max(width, height);
  if (maxAxis <= 0)
  {
    return this->SupersampleFactor;
  }

  GLint maxTexSize = 0;
  glGetIntegerv(GL_MAX_TEXTURE_SIZE, &maxTexSize);
  if (maxTexSize <= 0)
  {
    return this->SupersampleFactor;
  }

  const double maxAllowed = static_cast<double>(maxTexSize) / static_cast<double>(maxAxis);
  if (this->SupersampleFactor > maxAllowed)
  {
    if (!this->MaxSizeWarningIssued)
    {
      vtkWarningMacro("SupersampleFactor " << this->SupersampleFactor << " exceeds the "
                                           << maxTexSize << "px GL texture limit for a " << width
                                           << "x" << height << " window; clamping to " << maxAllowed
                                           << ". This warning is issued once.");
      this->MaxSizeWarningIssued = true;
    }
    return std::max(1.0, maxAllowed);
  }
  return this->SupersampleFactor;
}

void pvSSAAVolumePass::Render(const vtkRenderState* s)
{
  assert("pre: s_exists" && s != nullptr);

  vtkOpenGLClearErrorMacro();
  this->NumberOfRenderedProps = 0;

  if (this->DelegatePass == nullptr)
  {
    vtkWarningMacro("No delegate in pvSSAAVolumePass.");
    return;
  }

  vtkRenderer* r = s->GetRenderer();

  // PreRender/PostRender bracket the whole render so the RenderPasses() key the
  // GPU volume mapper reads is set on both the passthrough and supersample paths.
  this->PreRender(s);
  ScopedPrimitiveScale primitiveScale(r, this->PrimitiveScaleFactor);

  int size[2];
  s->GetWindowSize(size);
  const int width = size[0];
  const int height = size[1];

  const double factor = this->EffectiveFactor(width, height);

  // Into the renderer's own tile of the window: a subplot's viewport does not
  // start at the window origin.
  int originX = 0;
  int originY = 0;
  if (s->GetFrameBuffer() == nullptr)
  {
    int tileWidth = 0;
    int tileHeight = 0;
    r->GetTiledSizeAndOrigin(&tileWidth, &tileHeight, &originX, &originY);
  }

  // Always through the framebuffer, even at 1x: a screen-space delegate (EDL,
  // blur) at the window clears and composites the whole window, wiping the
  // other subplots, and writes no window depth for the overlay stage to read.
  vtkOpenGLRenderWindow* renWin = static_cast<vtkOpenGLRenderWindow*>(r->GetRenderWindow());
  vtkOpenGLState* ostate = renWin->GetState();

  // vtkEDLShading and the image-processing passes leave blending off, and
  // the overlay stage that follows needs it.
  vtkOpenGLState::ScopedglEnableDisable dsaver(ostate, GL_DEPTH_TEST);
  vtkOpenGLState::ScopedglEnableDisable bsaver(ostate, GL_BLEND);

  GLint maxTexSize = 0;
  glGetIntegerv(GL_MAX_TEXTURE_SIZE, &maxTexSize);
  int w = static_cast<int>(std::ceil(width * factor));
  int h = static_cast<int>(std::ceil(height * factor));
  if (maxTexSize > 0)
  {
    w = std::min(w, maxTexSize);
    h = std::min(h, maxTexSize);
  }

  if (this->Pass1 == nullptr)
  {
    this->Pass1 = vtkTextureObject::New();
    this->Pass1->SetContext(renWin);
  }
  if (this->FrameBufferObject == nullptr)
  {
    this->FrameBufferObject = vtkOpenGLFramebufferObject::New();
    this->FrameBufferObject->SetContext(renWin);
  }

  if (this->Pass1->GetWidth() != static_cast<unsigned int>(w) ||
    this->Pass1->GetHeight() != static_cast<unsigned int>(h))
  {
    this->Pass1->Create2D(
      static_cast<unsigned int>(w), static_cast<unsigned int>(h), 4, VTK_UNSIGNED_CHAR, false);
  }

  // Queried before PushFramebufferBindings, while GL_DRAW_FRAMEBUFFER is still
  // the window framebuffer the depth resolve writes back into.
  GLint windowDepthType = 0;
  glGetFramebufferAttachmentParameteriv(GL_DRAW_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
    GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE, &windowDepthType);
  const bool windowDepthIsFloat = (windowDepthType == GL_FLOAT);

  ostate->PushFramebufferBindings();
  vtkRenderState s2(r);
  s2.SetPropArrayAndCount(s->GetPropArray(), s->GetPropArrayCount());
  s2.SetFrameBuffer(this->FrameBufferObject);
  this->FrameBufferObject->Bind();
  this->FrameBufferObject->AddColorAttachment(0, this->Pass1);
  this->FrameBufferObject->ActivateDrawBuffer(0);

  // A texture rather than a renderbuffer so the depth resolve can sample it.
  // Fixed depth is 32-bit: the GPU volume mapper blits the scene depth into a
  // DEPTH_COMPONENT32 texture and Mesa rejects a blit between depth formats
  // that differ, leaving the volume unrendered on llvmpipe and GLX.
  if (this->DepthTex == nullptr)
  {
    this->DepthTex = vtkTextureObject::New();
    this->DepthTex->SetContext(renWin);
  }
  const int depthFormat =
    windowDepthIsFloat ? vtkTextureObject::Float32 : vtkTextureObject::Fixed32;
  if (this->DepthTex->GetWidth() != static_cast<unsigned int>(w) ||
    this->DepthTex->GetHeight() != static_cast<unsigned int>(h) ||
    this->DepthTexFormat != depthFormat)
  {
    this->DepthTex->AllocateDepth(
      static_cast<unsigned int>(w), static_cast<unsigned int>(h), depthFormat);
    this->DepthTex->SetMinificationFilter(vtkTextureObject::Nearest);
    this->DepthTex->SetMagnificationFilter(vtkTextureObject::Nearest);
    this->DepthTex->SetWrapS(vtkTextureObject::ClampToEdge);
    this->DepthTex->SetWrapT(vtkTextureObject::ClampToEdge);
    this->DepthTexFormat = depthFormat;
  }
  this->FrameBufferObject->AddDepthAttachment(this->DepthTex);
  this->FrameBufferObject->StartNonOrtho(w, h);
  ostate->vtkglViewport(0, 0, w, h);
  ostate->vtkglScissor(0, 0, w, h);

  // A screen-space delegate renders the scene into a framebuffer of its own
  // and writes colour only here, so the depth this resolves would be stale.
  {
    vtkOpenGLState::ScopedglDepthMask dmsaver(ostate);
    ostate->vtkglDepthMask(GL_TRUE);
    ostate->vtkglClearDepth(1.0);
    ostate->vtkglClear(GL_DEPTH_BUFFER_BIT);
  }

  ostate->vtkglEnable(GL_DEPTH_TEST);
  this->DelegatePass->Render(&s2);
  this->NumberOfRenderedProps += this->DelegatePass->GetNumberOfRenderedProps();

  // The w x h depth texture would size-mismatch the smaller colour attachments
  // the downsample attaches next and fail the FBO completeness check. It stays
  // alive for the depth resolve below.
  this->FrameBufferObject->RemoveDepthAttachment();

  if (w == width && h == height)
  {
    ostate->PopFramebufferBindings();
    // The scissor still covers the framebuffer from the origin; a tile away
    // from the origin would be clipped out of the copy back.
    ostate->vtkglScissor(originX, originY, width, height);
    ostate->vtkglDisable(GL_BLEND);
    ostate->vtkglDisable(GL_DEPTH_TEST);
    this->Pass1->Activate();
    this->Pass1->CopyToFrameBuffer(0, 0, width - 1, height - 1, originX, originY,
      originX + width - 1, originY + height - 1, width, height, nullptr, nullptr);
    this->Pass1->Deactivate();
    ostate->vtkglViewport(originX, originY, width, height);
  }
  else
  {
    if (this->Pass2 == nullptr)
    {
      this->Pass2 = vtkTextureObject::New();
      this->Pass2->SetContext(this->FrameBufferObject->GetContext());
    }
    if (this->Pass2->GetWidth() != static_cast<unsigned int>(width) ||
      this->Pass2->GetHeight() != static_cast<unsigned int>(h))
    {
      this->Pass2->Create2D(static_cast<unsigned int>(width), static_cast<unsigned int>(h), 4,
        VTK_UNSIGNED_CHAR, false);
    }

    this->FrameBufferObject->AddColorAttachment(0, this->Pass2);
    this->FrameBufferObject->Start(width, h);

    if (this->SSAAProgram == nullptr)
    {
      this->SSAAProgram = new vtkOpenGLHelper;
      vtkShaderProgram* newShader =
        renWin->GetShaderCache()->ReadyShaderProgram(SSAAVolumePassVS, SSAAVolumePassFS, "");
      if (newShader != this->SSAAProgram->Program)
      {
        this->SSAAProgram->Program = newShader;
        this->SSAAProgram->VAO->ShaderProgramChanged();
      }
      this->SSAAProgram->ShaderSourceTime.Modified();
    }
    else
    {
      renWin->GetShaderCache()->ReadyShaderProgram(this->SSAAProgram->Program);
    }

    if (this->SSAAProgram->Program == nullptr)
    {
      vtkErrorMacro("Couldn't build the SSAA downsample shader program.");
      ostate->PopFramebufferBindings();
      this->PostRender(s);
      return;
    }

    this->Pass1->Activate();
    int sourceId = this->Pass1->GetTextureUnit();
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    this->SSAAProgram->Program->SetUniformi("source", sourceId);
    this->SSAAProgram->Program->SetUniformf("texelWidthOffset", 0.375f / width);
    this->SSAAProgram->Program->SetUniformf("texelHeightOffset", 0.0f);

    ostate->vtkglDisable(GL_BLEND);
    ostate->vtkglDisable(GL_DEPTH_TEST);

    this->FrameBufferObject->RenderQuad(
      0, width - 1, 0, h - 1, this->SSAAProgram->Program, this->SSAAProgram->VAO);
    this->Pass1->Deactivate();

    ostate->PopFramebufferBindings();
    ostate->vtkglScissor(originX, originY, width, height);

    this->Pass2->Activate();
    sourceId = this->Pass2->GetTextureUnit();
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    this->SSAAProgram->Program->SetUniformi("source", sourceId);
    this->SSAAProgram->Program->SetUniformf("texelWidthOffset", 0.0f);
    this->SSAAProgram->Program->SetUniformf("texelHeightOffset", 0.375f / height);

    this->Pass2->CopyToFrameBuffer(0, 0, width - 1, h - 1, originX, originY, originX + width - 1,
      originY + height - 1, width, height, this->SSAAProgram->Program, this->SSAAProgram->VAO);
    this->Pass2->Deactivate();
    ostate->vtkglViewport(originX, originY, width, height);
  }

  // The window framebuffer is bound for draw again. No in-pipeline pass reads
  // the resolved window depth (every other pass is an inner delegate that ran on
  // the supersampled depth), so the min reduction's one-pixel silhouette halo is
  // only ever seen by an external depth read, where over-occluding is the safe
  // direction.
  if (this->ResolveDepth)
  {
    this->RenderDepthResolve(s);
  }

  this->PostRender(s);
  vtkOpenGLCheckErrorMacro("failed after Render");
}

void pvSSAAVolumePass::RenderDepthResolve(const vtkRenderState* s)
{
  if (s == nullptr || this->DepthTex == nullptr || this->DepthTex->GetWidth() == 0)
  {
    return;
  }
  vtkRenderer* r = s->GetRenderer();
  vtkOpenGLRenderWindow* renWin = static_cast<vtkOpenGLRenderWindow*>(r->GetRenderWindow());
  vtkOpenGLState* ostate = renWin->GetState();

  int size[2];
  s->GetWindowSize(size);
  const int width = size[0];
  const int height = size[1];
  int originX = 0;
  int originY = 0;
  if (s->GetFrameBuffer() == nullptr)
  {
    int tileWidth = 0;
    int tileHeight = 0;
    r->GetTiledSizeAndOrigin(&tileWidth, &tileHeight, &originX, &originY);
  }
  const int w = static_cast<int>(this->DepthTex->GetWidth());
  const int h = static_cast<int>(this->DepthTex->GetHeight());

  vtkOpenGLState::ScopedglViewport vpsaver(ostate);
  vtkOpenGLState::ScopedglScissor scsaver(ostate);
  vtkOpenGLState::ScopedglEnableDisable dtsaver(ostate, GL_DEPTH_TEST);
  vtkOpenGLState::ScopedglEnableDisable blsaver(ostate, GL_BLEND);
  ostate->vtkglViewport(originX, originY, width, height);
  ostate->vtkglScissor(originX, originY, width, height);
  {
    if (this->DepthResolveProgram == nullptr)
    {
      this->DepthResolveProgram = new vtkOpenGLHelper;
      vtkShaderProgram* depthShader = renWin->GetShaderCache()->ReadyShaderProgram(
        SSAAVolumePassVS, SSAAVolumeDepthResolveFS, "");
      if (depthShader != this->DepthResolveProgram->Program)
      {
        this->DepthResolveProgram->Program = depthShader;
        this->DepthResolveProgram->VAO->ShaderProgramChanged();
      }
      this->DepthResolveProgram->ShaderSourceTime.Modified();
    }
    else
    {
      renWin->GetShaderCache()->ReadyShaderProgram(this->DepthResolveProgram->Program);
    }

    if (this->DepthResolveProgram->Program != nullptr)
    {
      this->DepthTex->Activate();
      const int depthUnit = this->DepthTex->GetTextureUnit();
      this->DepthTex->SetMinificationFilter(vtkTextureObject::Nearest);
      this->DepthTex->SetMagnificationFilter(vtkTextureObject::Nearest);
      this->DepthResolveProgram->Program->SetUniformi("depthSource", depthUnit);
      const float sourceStep[2] = { 1.0f / static_cast<float>(w), 1.0f / static_cast<float>(h) };
      this->DepthResolveProgram->Program->SetUniform2f("sourceStep", sourceStep);
      const int footprint[2] = { std::max(
                                   1, static_cast<int>(std::ceil(static_cast<double>(w) / width))),
        std::max(1, static_cast<int>(std::ceil(static_cast<double>(h) / height))) };
      this->DepthResolveProgram->Program->SetUniform2i("footprint", footprint);

      // Depth-only pass: every window pixel is overwritten with the resolved
      // value. Scoped savers restore whatever func/mask state the caller had.
      vtkOpenGLState::ScopedglDepthFunc dfsaver(ostate);
      vtkOpenGLState::ScopedglDepthMask dmsaver(ostate);
      vtkOpenGLState::ScopedglColorMask cmsaver(ostate);

      ostate->vtkglDisable(GL_BLEND);
      ostate->vtkglEnable(GL_DEPTH_TEST);
      ostate->vtkglDepthFunc(GL_ALWAYS);
      ostate->vtkglDepthMask(GL_TRUE);
      ostate->vtkglColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE);

      this->DepthTex->CopyToFrameBuffer(
        this->DepthResolveProgram->Program, this->DepthResolveProgram->VAO);

      this->DepthTex->Deactivate();
    }
    else
    {
      vtkErrorMacro("Couldn't build the SSAA depth-resolve shader program.");
    }
  }
}

void pvSSAAVolumePass::ReleaseGraphicsResources(vtkWindow* w)
{
  assert("pre: w_exists" && w != nullptr);
  this->Superclass::ReleaseGraphicsResources(w);

  // vtkOpenGLHelper::ReleaseGraphicsResources nulls its Program but leaves the
  // helper allocated, so the next Render() would re-ready a null program and
  // bail every frame. Destroy the helpers so the next Render() rebuilds them.
  if (this->SSAAProgram != nullptr)
  {
    this->SSAAProgram->ReleaseGraphicsResources(w);
    delete this->SSAAProgram;
    this->SSAAProgram = nullptr;
  }
  if (this->DepthResolveProgram != nullptr)
  {
    this->DepthResolveProgram->ReleaseGraphicsResources(w);
    delete this->DepthResolveProgram;
    this->DepthResolveProgram = nullptr;
  }
  if (this->FrameBufferObject != nullptr)
  {
    this->FrameBufferObject->ReleaseGraphicsResources(w);
  }
  if (this->Pass1 != nullptr)
  {
    this->Pass1->ReleaseGraphicsResources(w);
  }
  if (this->Pass2 != nullptr)
  {
    this->Pass2->ReleaseGraphicsResources(w);
  }
  if (this->DepthTex != nullptr)
  {
    this->DepthTex->ReleaseGraphicsResources(w);
  }
  if (this->DelegatePass != nullptr)
  {
    this->DelegatePass->ReleaseGraphicsResources(w);
  }
}

void pvSSAAVolumePass::PrintSelf(ostream& os, vtkIndent indent)
{
  this->Superclass::PrintSelf(os, indent);
  os << indent << "DelegatePass: ";
  if (this->DelegatePass != nullptr)
  {
    this->DelegatePass->PrintSelf(os, indent.GetNextIndent());
  }
  else
  {
    os << "(none)\n";
  }
  os << indent << "SupersampleFactor: " << this->SupersampleFactor << "\n";
  os << indent << "PrimitiveScaleFactor: " << this->PrimitiveScaleFactor << "\n";
  os << indent << "ResolveDepth: " << (this->ResolveDepth ? "On" : "Off") << "\n";
}
