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

# scripts/build_variants.py decides what to build (every SDK found, or the one
# PVRP_BACKEND + PVRP_VTK_DIR select) and writes the wheel's runtime pins from
# the same SDKs, keyed on the interpreter scikit-build-core hands this build.
execute_process(
  COMMAND "${Python_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/build_variants.py"
  OUTPUT_VARIABLE _variant_lines
  ERROR_VARIABLE _variant_error
  RESULT_VARIABLE _variant_result
  OUTPUT_STRIP_TRAILING_WHITESPACE)
if(_variant_result)
  message(FATAL_ERROR "scripts/build_variants.py failed: ${_variant_error}")
endif()
string(REGEX REPLACE "\r?\n" ";" _variant_lines "${_variant_lines}")

set(_version "${SKBUILD_PROJECT_VERSION}")
if(NOT _version)
  set(_version "0.0.0")
endif()

foreach(_line IN LISTS _variant_lines)
  string(REPLACE "|" ";" _fields "${_line}")
  list(GET _fields 0 _variant)
  list(GET _fields 1 _vtk_dir)
  message(STATUS "Building variant ${_variant} against ${_vtk_dir}")
  set(_staging "${CMAKE_CURRENT_BINARY_DIR}/staging-${_variant}")
  ExternalProject_Add(variant_${_variant}
    SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/extensions"
    BINARY_DIR "${CMAKE_CURRENT_BINARY_DIR}/variant-${_variant}"
    INSTALL_DIR "${_staging}"
    BUILD_ALWAYS ON
    CMAKE_ARGS
      "-DVTK_DIR=${_vtk_dir}"
      "-DPVRP_BACKEND=${_variant}"
      "-DPVRP_VERSION=${_version}"
      "-DPython3_EXECUTABLE=${Python_EXECUTABLE}"
      "-DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}"
      "-DCMAKE_INSTALL_PREFIX=${_staging}"
      "-DCMAKE_OSX_DEPLOYMENT_TARGET=${CMAKE_OSX_DEPLOYMENT_TARGET}"
      "-DCMAKE_OSX_ARCHITECTURES=${CMAKE_OSX_ARCHITECTURES}")
  # The runtime wheel takes the package directory only.
  install(DIRECTORY "${_staging}/pyvista_render_passes/" DESTINATION "pyvista_render_passes"
    USE_SOURCE_PERMISSIONS)
endforeach()
