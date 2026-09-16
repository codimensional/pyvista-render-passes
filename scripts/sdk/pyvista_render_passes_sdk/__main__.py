"""Print the ``PyVistaRenderPasses_DIR`` of one variant, for a cmake command line."""

from __future__ import annotations

import argparse

from . import cmake_dir


def main() -> int:
    """Print the requested directory.

    Returns
    -------
    int
        Process exit status.

    """
    parser = argparse.ArgumentParser(prog='python -m pyvista_render_passes_sdk')
    parser.add_argument('--cmake-dir', action='store_true', required=True)
    parser.add_argument('--backend', help='cvista or vtk; required when the SDK carries both')
    args = parser.parse_args()
    try:
        print(cmake_dir(args.backend))
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
