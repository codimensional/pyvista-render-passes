# Which VTK the calling scope found: its backend (cvista or vtk) and the
# generation a binary built against it stays compatible with. The build records
# it; PyVistaRenderPassesConfig.cmake refuses a VTK whose identity differs.
function(_pyvista_render_passes_vtk_identity backend_var generation_var)
  # cvista-sdk keeps <pkg>/_version.py beside <pkg>/cmake. Its fourth release
  # segment is the ABI generation, which VTK_VERSION does not carry.
  set(_version_file "${VTK_DIR}/../_version.py")
  set(_generation "")
  if(EXISTS "${_version_file}")
    set(_backend cvista)
    file(STRINGS "${_version_file}" _lines REGEX "version")
    string(REGEX MATCH "[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+" _generation "${_lines}")
  else()
    # Stock VTK keeps its ABI within a minor release, as the runtime pin assumes.
    set(_backend vtk)
    string(REGEX MATCH "^[0-9]+\\.[0-9]+" _generation "${VTK_VERSION}")
  endif()
  set("${backend_var}" "${_backend}" PARENT_SCOPE)
  set("${generation_var}" "${_generation}" PARENT_SCOPE)
endfunction()
