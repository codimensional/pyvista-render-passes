# Only find_package(PyVistaRenderPasses): the refusal tests configure this with
# a VTK_DIR the package was not built against.
cmake_minimum_required(VERSION 3.15 FATAL_ERROR)
project(FindOnly CXX)
find_package(PyVistaRenderPasses REQUIRED)
