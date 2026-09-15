"""Import the installed package under every backend it was built for.

Each backend is checked in a subprocess with ``PYVISTA_VTK_BACKEND`` set, since
the choice is fixed at import. A backend whose runtime is not installed is
skipped; a built backend that fails to import fails the run.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

CHECK = (
    'import pyvista_render_passes as p, sys; '
    'assert p.BACKEND == sys.argv[1], (p.BACKEND, sys.argv[1]); '
    'p.pvRenderPassChain(); p.pvSSAAVolumePass(); p.pvPropKeyFilterPass(); '
    'print(p.BACKEND, p.__version__)'
)


def main() -> None:
    spec = importlib.util.find_spec('pyvista_render_passes')
    if spec is None or spec.origin is None:
        msg = 'pyvista_render_passes is not installed'
        raise SystemExit(msg)
    package = Path(spec.origin).parent
    built = [d.name[1:] for d in package.iterdir() if d.is_dir() and d.name in ('_vtk', '_cvista')]
    if not built:
        msg = f'no compiled backend under {package}'
        raise SystemExit(msg)

    checked = 0
    failed = []
    for backend in built:
        runtime = 'cvista' if backend == 'cvista' else 'vtkmodules'
        if importlib.util.find_spec(runtime) is None:
            print(f'{backend}: runtime {runtime!r} not installed, skipped')
            continue
        env = os.environ | {'PYVISTA_VTK_BACKEND': backend}
        run = subprocess.run([sys.executable, '-c', CHECK, backend], check=False, env=env)  # noqa: S603
        if run.returncode:
            failed.append(backend)
        checked += 1
    if not checked:
        msg = f'built for {built} but no runtime is installed'
        raise SystemExit(msg)
    if failed:
        msg = f'backends that failed to import: {failed}'
        raise SystemExit(msg)


if __name__ == '__main__':
    main()
