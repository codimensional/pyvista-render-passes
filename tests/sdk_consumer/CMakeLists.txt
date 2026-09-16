# A third-party VTK module built against the installed PyVistaRenderPasses SDK
# and installed into a site-packages directory (CMAKE_INSTALL_PREFIX) beside the
# runtime wheels, which it finds through pyvista_render_passes_runtime_rpath.
cmake_minimum_required(VERSION 3.15 FATAL_ERROR)
project(SdkConsumer CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
option(CONSUMER_RUNTIME_RPATH "Load the runtime wheels through the SDK's rpath helper" ON)

find_package(VTK REQUIRED COMPONENTS CommonCore WrappingPythonCore)
find_package(PyVistaRenderPasses REQUIRED)

vtk_module_scan(
  MODULE_FILES "${CMAKE_CURRENT_SOURCE_DIR}/module/vtk.module"
  PROVIDES_MODULES modules
  WANT_BY_DEFAULT ON
  HIDE_MODULES_FROM_CACHE ON
  ENABLE_TESTS OFF)

include(GNUInstallDirs)
set(BUILD_SHARED_LIBS ON)
set(CMAKE_INSTALL_LIBDIR "sdk_consumer")
set(CMAKE_INSTALL_BINDIR "sdk_consumer")
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH OFF)
if(APPLE)
  set(CMAKE_INSTALL_RPATH "@loader_path")
else()
  set(CMAKE_INSTALL_RPATH "$ORIGIN")
endif()
if(CONSUMER_RUNTIME_RPATH)
  pyvista_render_passes_runtime_rpath(_runtime_rpath DESTINATION sdk_consumer)
  list(APPEND CMAKE_INSTALL_RPATH ${_runtime_rpath})
endif()

vtk_module_build(MODULES ${modules} INSTALL_HEADERS OFF)
vtk_module_wrap_python(
  MODULES ${modules}
  PYTHON_PACKAGE "sdk_consumer"
  MODULE_DESTINATION "."
  BUILD_STATIC OFF
  INSTALL_HEADERS OFF)

file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/__init__.py" "")
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/__init__.py" DESTINATION "sdk_consumer")
