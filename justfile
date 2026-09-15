# List available recipes
default:
    @just --list

# Fetch the VTK wheel SDK, build both variants, install with the dev extras
sync:
    uv venv --allow-existing
    uv run --no-project --no-sync python scripts/fetch_vtk_sdk.py
    uv sync --no-editable --extra dev --extra cvista --extra vtk --reinstall-package pyvista-render-passes

# Delete the virtual environment and every build tree, then sync
reset:
    rm -rf .venv build wheelhouse
    just sync

# Run the tests against one backend: `just test vtk` or `just test cvista`
test backend *args:
    PYVISTA_VTK_BACKEND={{ backend }} uv run --no-sync pytest {{ args }}

# Run type checking
typecheck:
    uv run --no-sync mypy

# Run all linters and formatters (pre-commit hooks)
lint:
    uvx pre-commit run --all-files

# Build one manylinux wheel through cibuildwheel (needs docker)
wheel:
    uvx cibuildwheel --only cp312-manylinux_x86_64
