"""VTK names the tests use, resolved off whichever backend the package loaded."""

from __future__ import annotations

from pyvista_render_passes._backend import backend_module

_core = backend_module('vtkCommonCore')
_rendering = backend_module('vtkRenderingCore')
_opengl = backend_module('vtkRenderingOpenGL2')

numpy_support = backend_module('util.numpy_support')

vtkCommand = _core.vtkCommand
vtkFloatArray = _core.vtkFloatArray
vtkInformation = _core.vtkInformation
vtkInformationIntegerKey = _core.vtkInformationIntegerKey
vtkProp = _rendering.vtkProp
vtkRenderPass = _rendering.vtkRenderPass
vtkCameraPass = _opengl.vtkCameraPass
vtkDualDepthPeelingPass = _opengl.vtkDualDepthPeelingPass
vtkEDLShading = _opengl.vtkEDLShading
vtkGaussianBlurPass = _opengl.vtkGaussianBlurPass
vtkOpenGLRenderPass = _opengl.vtkOpenGLRenderPass
vtkRenderPassCollection = _opengl.vtkRenderPassCollection
vtkRenderStepsPass = _opengl.vtkRenderStepsPass
vtkSequencePass = _opengl.vtkSequencePass
vtkSSAOPass = _opengl.vtkSSAOPass
vtkTextureObject = _opengl.vtkTextureObject
vtkVolumetricPass = _opengl.vtkVolumetricPass
