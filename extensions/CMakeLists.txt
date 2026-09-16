# One build of the passes against one VTK SDK. The root CMakeLists drives this
# once per variant through ExternalProject, and scripts/build_sdk.py packages
# the SDK half of that install tree. PVRP_STATIC builds C++-only archives
# (the Emscripten mode).
cmake_minimum_required(VERSION 3.15 FATAL_ERROR)
project(PyVistaRenderPasses CXX)

option(PVRP_STATIC "Static archives for C++ consumers, with no Python wrapping" OFF)
set(PVRP_BACKEND "" CACHE STRING "Backend this variant targets: cvista or vtk")
set(PVRP_VERSION "0.0.0" CACHE STRING "Version recorded in the CMake package")
if(PVRP_BACKEND STREQUAL "cvista")
  set(PVRP_RUNTIME_DIR cvista)
elseif(PVRP_BACKEND STREQUAL "vtk")
  set(PVRP_RUNTIME_DIR vtkmodules)
else()
  message(FATAL_ERROR "PVRP_BACKEND must be cvista or vtk")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(_vtk_components CommonCore RenderingCore RenderingOpenGL2)
if(NOT PVRP_STATIC)
  list(APPEND _vtk_components WrappingPythonCore)
endif()
find_package(VTK REQUIRED COMPONENTS ${_vtk_components} NO_MODULE)

include("${CMAKE_CURRENT_SOURCE_DIR}/PyVistaRenderPassesVTKIdentity.cmake")
_pyvista_render_passes_vtk_identity(_found_backend PVRP_GENERATION)
if(NOT _found_backend STREQUAL PVRP_BACKEND OR NOT PVRP_GENERATION)
  message(FATAL_ERROR
    "PVRP_BACKEND=${PVRP_BACKEND}, but ${VTK_DIR} is a ${_found_backend} SDK of generation '${PVRP_GENERATION}'")
endif()

# Kitware's manylinux2014 wheels are built with the pre-C++11 libstdc++ ABI;
# a std::string-returning method links only when this build matches it.
set(PVRP_CXX11_ABI "")
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  set(PVRP_CXX11_ABI 1)
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
    set(PVRP_CXX11_ABI 0)
  endif()
endif()

vtk_module_scan(
  MODULE_FILES "${CMAKE_CURRENT_SOURCE_DIR}/render_passes/vtk.module"
  PROVIDES_MODULES modules
  WANT_BY_DEFAULT ON
  HIDE_MODULES_FROM_CACHE ON
  ENABLE_TESTS OFF)

include(GNUInstallDirs)

# The SDK half of the install tree. The runtime wheel takes none of it.
set(_cmake_dest "lib/cmake/PyVistaRenderPasses")
set(_headers_dest "include/pyvista-render-passes")
set(_hierarchy_dest "lib/vtk/hierarchy/PyVistaRenderPasses")
set(_license_dest "share/licenses/PyVistaRenderPasses")

if(PVRP_STATIC)
  set(BUILD_SHARED_LIBS OFF)
  set(_lib_dest "lib")
  set(_bin_dest "bin")
else()
  set(BUILD_SHARED_LIBS ON)
  # Libraries land in pyvista_render_passes/_<backend>/; they resolve the
  # backend's libraries from the runtime wheel two levels up in site-packages.
  set(_lib_dest "pyvista_render_passes/_${PVRP_BACKEND}")
  set(_bin_dest "${_lib_dest}")
  set(CMAKE_INSTALL_LIBDIR "${_lib_dest}")
  set(CMAKE_INSTALL_BINDIR "${_bin_dest}")
  if(APPLE)
    # An @rpath install name lets a consumer's rpath pick the runtime wheel's copy.
    set(CMAKE_INSTALL_NAME_DIR "@rpath")
    set(CMAKE_INSTALL_RPATH "@loader_path;@loader_path/../../${PVRP_RUNTIME_DIR}")
  else()
    set(CMAKE_INSTALL_RPATH "$ORIGIN:$ORIGIN/../../${PVRP_RUNTIME_DIR}")
  endif()
  set(CMAKE_INSTALL_RPATH_USE_LINK_PATH OFF)
endif()

# INSTALL_EXPORT is what writes PyVistaRenderPasses-targets.cmake and the module
# properties; without it the install succeeds and find_package imports nothing.
vtk_module_build(
  MODULES ${modules}
  PACKAGE PyVistaRenderPasses
  INSTALL_EXPORT PyVistaRenderPasses
  TARGET_NAMESPACE PyVistaRenderPasses
  INSTALL_HEADERS ON
  HEADERS_DESTINATION "${_headers_dest}"
  CMAKE_DESTINATION "${_cmake_dest}"
  HIERARCHY_DESTINATION "${_hierarchy_dest}"
  LICENSE_DESTINATION "${_license_dest}"
  LIBRARY_DESTINATION "${_lib_dest}"
  ARCHIVE_DESTINATION "${_lib_dest}"
  RUNTIME_DESTINATION "${_bin_dest}")

if(PVRP_CXX11_ABI STREQUAL "0")
  # A consumer compiling against our headers must use the same ABI.
  vtk_module_definitions(PyVistaRenderPasses::RenderPasses PUBLIC _GLIBCXX_USE_CXX11_ABI=0)
endif()

if(NOT PVRP_STATIC)
  vtk_module_wrap_python(
    MODULES ${modules}
    PYTHON_PACKAGE "pyvista_render_passes._${PVRP_BACKEND}"
    MODULE_DESTINATION "."
    CMAKE_DESTINATION "${_cmake_dest}"
    HEADERS_DESTINATION "${_headers_dest}"
    BUILD_STATIC OFF
    INSTALL_HEADERS ON)

  configure_file(variant_init.py.in "${CMAKE_CURRENT_BINARY_DIR}/__init__.py" @ONLY)
  install(FILES "${CMAKE_CURRENT_BINARY_DIR}/__init__.py" DESTINATION "${_lib_dest}")
endif()

include(CMakePackageConfigHelpers)
configure_file(PyVistaRenderPassesConfig.cmake.in
  "${CMAKE_CURRENT_BINARY_DIR}/PyVistaRenderPassesConfig.cmake" @ONLY)
string(REGEX MATCH "^[0-9]+(\\.[0-9]+)*" _pvrp_numeric_version "${PVRP_VERSION}")
write_basic_package_version_file(
  "${CMAKE_CURRENT_BINARY_DIR}/PyVistaRenderPassesConfigVersion.cmake"
  VERSION "${_pvrp_numeric_version}"
  COMPATIBILITY SameMinorVersion)
install(FILES
  "${CMAKE_CURRENT_BINARY_DIR}/PyVistaRenderPassesConfig.cmake"
  "${CMAKE_CURRENT_BINARY_DIR}/PyVistaRenderPassesConfigVersion.cmake"
  PyVistaRenderPassesVTKIdentity.cmake
  DESTINATION "${_cmake_dest}")
