"""Download and unpack Kitware's VTK wheel SDK for this interpreter and platform.

The SDK lands in ``build/vtk-sdk/``, where the CMake build looks for it. The
VTK version defaults to the installed ``vtk`` wheel's, else to the version the
package targets. On a platform Kitware ships no SDK for, nothing is fetched
and the build carries the cvista variant only.
"""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import sys
import tarfile
import time
import urllib.request

BASE_URL = 'https://vtk.org/files/wheel-sdks/'
SDK_DIR = Path(__file__).resolve().parent.parent / 'build' / 'vtk-sdk'
DEFAULT_VTK_VERSION = '9.7.0'


def _platform_tag() -> str | None:
    machine = platform.machine().lower()
    if sys.platform.startswith('linux'):
        if machine in ('x86_64', 'amd64'):
            return 'manylinux2014_x86_64.manylinux_2_17_x86_64'
        if machine in ('aarch64', 'arm64'):
            return 'manylinux_2_28_aarch64'
    elif sys.platform == 'darwin':
        if machine == 'x86_64':
            return 'macosx_10_10_x86_64'
    elif sys.platform == 'win32' and machine in ('amd64', 'x86_64'):
        return 'win_amd64'
    return None


def _vtk_version() -> str:
    try:
        return version('vtk')
    except PackageNotFoundError:
        return DEFAULT_VTK_VERSION


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vtk_version', nargs='?', default=None)
    args = parser.parse_args()
    vtk_version = args.vtk_version or _vtk_version()

    plat = _platform_tag()
    if plat is None:
        print(
            f'no VTK wheel SDK for {sys.platform}/{platform.machine()}; skipped', file=sys.stderr
        )
        return
    py = f'cp{sys.version_info.major}{sys.version_info.minor}'
    name = f'vtk-wheel-sdk-{vtk_version}-{py}-{py}-{plat}'
    target = SDK_DIR / name
    if target.is_dir():
        print(target)
        return

    SDK_DIR.mkdir(parents=True, exist_ok=True)
    archive = SDK_DIR / f'{name}.tar.xz'
    url = f'{BASE_URL}{name}.tar.xz'
    print(f'fetching {url}', file=sys.stderr)
    for attempts_left in (2, 1, 0):
        try:
            urllib.request.urlretrieve(url, archive)  # noqa: S310  fixed https host
            break
        except OSError as exc:
            if not attempts_left:
                raise
            print(f'retrying after {exc}', file=sys.stderr)
            time.sleep(10)
    with tarfile.open(archive) as tar:
        tar.extractall(SDK_DIR, filter='data')
    archive.unlink()
    if not target.is_dir():
        msg = f'{archive.name} did not unpack to {target}'
        raise SystemExit(msg)
    print(target)


if __name__ == '__main__':
    main()
