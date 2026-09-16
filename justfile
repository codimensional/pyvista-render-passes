# Extras for the variants this platform builds: Windows has no cvista SDK,
# macOS arm64 no Kitware SDK (docs/design.md "Two builds in one wheel")
extras := if os() == "windows" { "--extra vtk" } else if os() == "macos" { if arch() == "aarch64" { "--extra cvista" } else { "--extra cvista --extra vtk" } } else { "--extra cvista --extra vtk" }

# List available recipes
default:
    @just --list

# Fetch the VTK wheel SDK, build every variant, install with the dev and SDK groups
sync:
    uv venv --allow-existing
    uv run --no-sync python scripts/fetch_vtk_sdk.py
    uv sync --no-editable {{ extras }} --group dev --group sdk --reinstall-package pyvista-render-passes

# Delete the virtual environment and every build tree, then sync
reset:
    rm -rf .venv build wheelhouse
    just sync

# Package the C++ SDK wheel from the `just sync` build tree into build/sdk-dist/
sdk *args:
    uv run --no-sync python scripts/build_sdk.py {{ args }}

# Run the tests against one backend: `just test vtk` or `just test cvista`
test backend *args:
    PYVISTA_VTK_BACKEND={{ backend }} uv run --no-sync pytest {{ args }}

# Run type checking
typecheck:
    uv run --no-sync mypy

# Run all linters and formatters (pre-commit hooks)
lint:
    uvx pre-commit run --all-files

# Build one manylinux runtime wheel and its SDK wheel through cibuildwheel (needs docker)
wheel:
    uvx cibuildwheel --only cp312-manylinux_x86_64
