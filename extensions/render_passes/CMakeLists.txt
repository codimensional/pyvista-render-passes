vtk_module_add_module(PyVistaRenderPasses
  SOURCES pvPropKeyFilterPass.cxx
          pvRenderPassChain.cxx
          pvSSAAVolumePass.cxx
  HEADERS pvPropKeyFilterPass.h
          pvRenderPassChain.h
          pvSSAAVolumePass.h
)
