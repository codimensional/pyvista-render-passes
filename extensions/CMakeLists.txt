# One build of the passes against one VTK distribution. The root CMakeLists
# drives this once per backend through ExternalProject.
cmake_minimum_required(VERSION 3.15 FATAL_ERROR)
project(PyVistaRenderPassesVariant CXX)

set(PVRP_BACKEND "" CACHE STRING "Backend this variant targets: cvista or vtk")
set(PVRP_RUNTIME_DIR "" CACHE STRING "site-packages dir holding the backend's shared libraries")
if(NOT PVRP_BACKEND OR NOT PVRP_RUNTIME_DIR)
  message(FATAL_ERROR "PVRP_BACKEND and PVRP_RUNTIME_DIR are required")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(VTK REQUIRED
  COMPONENTS CommonCore RenderingCore RenderingOpenGL2 WrappingPythonCore
  NO_MODULE)

# Kitware's manylinux2014 wheels are built with the pre-C++11 libstdc++ ABI;
# a std::string-returning method links only when this build matches it.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  set(_abi_probe "${CMAKE_CURRENT_BINARY_DIR}/abi_probe.cxx")
  file(WRITE "${_abi_probe}"
    "#include <vtkObject.h>\n#include <string>\n"
    "int main() { vtkObject* o = vtkObject::New(); std::string s = o->vtkObject::GetObjectDescription(); o->Delete(); return int(s.size()); }\n")
  try_compile(_links_new_abi "${CMAKE_CURRENT_BINARY_DIR}/abi_probe"
    SOURCES "${_abi_probe}"
    LINK_LIBRARIES VTK::CommonCore
    CXX_STANDARD 17)
  if(NOT _links_new_abi)
    try_compile(_links_old_abi "${CMAKE_CURRENT_BINARY_DIR}/abi_probe_old"
      SOURCES "${_abi_probe}"
      COMPILE_DEFINITIONS -D_GLIBCXX_USE_CXX11_ABI=0
      LINK_LIBRARIES VTK::CommonCore
      CXX_STANDARD 17)
    if(NOT _links_old_abi)
      message(FATAL_ERROR "Cannot link against ${VTK_DIR} with either libstdc++ ABI")
    endif()
    message(STATUS "Using the pre-C++11 libstdc++ ABI to match ${VTK_DIR}")
    add_compile_definitions(_GLIBCXX_USE_CXX11_ABI=0)
  endif()
endif()

vtk_module_scan(
  MODULE_FILES "${CMAKE_CURRENT_SOURCE_DIR}/render_passes/vtk.module"
  PROVIDES_MODULES modules
  WANT_BY_DEFAULT ON
  HIDE_MODULES_FROM_CACHE ON
  ENABLE_TESTS OFF)

include(GNUInstallDirs)
set(BUILD_SHARED_LIBS ON)

# Everything lands in pyvista_render_passes/_<backend>/; the kit resolves the
# backend's libraries from the runtime wheel two levels up in site-packages.
set(_pkg_dir "pyvista_render_passes/_${PVRP_BACKEND}")
set(CMAKE_INSTALL_LIBDIR "${_pkg_dir}")
set(CMAKE_INSTALL_BINDIR "${_pkg_dir}")
if(APPLE)
  set(CMAKE_INSTALL_RPATH "@loader_path;@loader_path/../../${PVRP_RUNTIME_DIR}")
else()
  set(CMAKE_INSTALL_RPATH "$ORIGIN:$ORIGIN/../../${PVRP_RUNTIME_DIR}")
endif()
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH OFF)

vtk_module_build(MODULES ${modules} INSTALL_HEADERS OFF)

vtk_module_wrap_python(
  MODULES ${modules}
  PYTHON_PACKAGE "pyvista_render_passes._${PVRP_BACKEND}"
  MODULE_DESTINATION "."
  BUILD_STATIC OFF
  INSTALL_HEADERS OFF)

configure_file(variant_init.py.in "${CMAKE_CURRENT_BINARY_DIR}/__init__.py" @ONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/__init__.py" DESTINATION "${_pkg_dir}")
