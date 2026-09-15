cmake_minimum_required(VERSION 3.15 FATAL_ERROR)
project(pyvista_render_passes NONE)

include(ExternalProject)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
if(APPLE AND NOT CMAKE_OSX_DEPLOYMENT_TARGET)
  set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "")
endif()

find_package(Python REQUIRED COMPONENTS Interpreter)
set(_py_tag "cp${Python_VERSION_MAJOR}${Python_VERSION_MINOR}")

# cvista: the `cvista-sdk` wheel in [build-system] requires ships the CMake config.
set(PVRP_CVISTA_SDK "" CACHE PATH "Directory holding cvista-sdk's vtk-config.cmake")
if(NOT PVRP_CVISTA_SDK)
  execute_process(
    COMMAND "${Python_EXECUTABLE}" -c
      "import cvista_sdk, pathlib; print(pathlib.Path(cvista_sdk.__file__).parent / 'cmake')"
    OUTPUT_VARIABLE PVRP_CVISTA_SDK
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET)
endif()

# Stock VTK: Kitware's wheel SDK, unpacked by scripts/fetch_vtk_sdk.py.
set(PVRP_VTK_SDK "" CACHE PATH "Root of an unpacked vtk-wheel-sdk-* tarball")
if(NOT PVRP_VTK_SDK)
  file(GLOB _sdk_roots "${CMAKE_CURRENT_SOURCE_DIR}/build/vtk-sdk/vtk-wheel-sdk-*-${_py_tag}-*")
  if(_sdk_roots)
    list(GET _sdk_roots 0 PVRP_VTK_SDK)
  endif()
endif()
set(_vtk_config "")
if(PVRP_VTK_SDK)
  file(GLOB _vtk_config "${PVRP_VTK_SDK}/vtk-*.data/headers/cmake")
endif()

set(_variants "")
if(PVRP_CVISTA_SDK AND EXISTS "${PVRP_CVISTA_SDK}/vtk-config.cmake")
  list(APPEND _variants cvista)
  set(_vtk_dir_cvista "${PVRP_CVISTA_SDK}")
  set(_runtime_cvista cvista)
endif()
if(_vtk_config AND EXISTS "${_vtk_config}/vtk-config.cmake")
  list(APPEND _variants vtk)
  set(_vtk_dir_vtk "${_vtk_config}")
  set(_runtime_vtk vtkmodules)
endif()
if(NOT _variants)
  message(FATAL_ERROR "No VTK to build against: neither cvista-sdk nor a vtk wheel SDK was found")
endif()
message(STATUS "Building variants: ${_variants}")

foreach(_variant IN LISTS _variants)
  set(_staging "${CMAKE_CURRENT_BINARY_DIR}/staging-${_variant}")
  ExternalProject_Add(variant_${_variant}
    SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/extensions"
    BINARY_DIR "${CMAKE_CURRENT_BINARY_DIR}/variant-${_variant}"
    INSTALL_DIR "${_staging}"
    BUILD_ALWAYS ON
    CMAKE_ARGS
      "-DVTK_DIR=${_vtk_dir_${_variant}}"
      "-DPVRP_BACKEND=${_variant}"
      "-DPVRP_RUNTIME_DIR=${_runtime_${_variant}}"
      "-DPython3_EXECUTABLE=${Python_EXECUTABLE}"
      "-DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}"
      "-DCMAKE_INSTALL_PREFIX=${_staging}"
      "-DCMAKE_OSX_DEPLOYMENT_TARGET=${CMAKE_OSX_DEPLOYMENT_TARGET}"
      "-DCMAKE_OSX_ARCHITECTURES=${CMAKE_OSX_ARCHITECTURES}")
  install(DIRECTORY "${_staging}/" DESTINATION "." USE_SOURCE_PERMISSIONS)
endforeach()
