"""Find the VTK SDKs a build compiles against and derive each one's runtime pin.

One implementation serves two readers: the root ``CMakeLists.txt`` runs this
script for the variants to build, and scikit-build-core loads it as the
``[[tool.dynamic-metadata]]`` provider of the wheel's ``optional-dependencies``.

By default every SDK that is present is built: ``cvista-sdk`` from the build
environment and Kitware's wheel SDK under ``build/vtk-sdk/``. Setting
``PVRP_BACKEND`` (``cvista`` or ``vtk``) together with ``PVRP_VTK_DIR`` (the
directory holding that SDK's ``vtk-config.cmake``) builds that one variant
against that SDK instead, and the runtime pin follows the SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BACKENDS = ('cvista', 'vtk')
# cvista's ABI generation is the fourth release segment of its version.
CVISTA_GENERATION_SEGMENTS = 4
_BUILD = {'state': 'wheel'}


@dataclass(frozen=True)
class Variant:
    """One build of the passes against one VTK SDK."""

    backend: str
    vtk_dir: Path
    sdk_version: str

    @property
    def generation(self) -> str:
        """Leading release segments that decide binary compatibility."""
        release = re.findall(r'\d+', self.sdk_version.split('+')[0].split('.post')[0])
        if self.backend == 'vtk':
            return '.'.join(release[:2])
        if len(release) < CVISTA_GENERATION_SEGMENTS:
            msg = (
                f'cvista SDK version {self.sdk_version!r} at {self.vtk_dir} has no ABI '
                'generation segment; cannot pin the runtime'
            )
            raise SystemExit(msg)
        return '.'.join(release[:CVISTA_GENERATION_SEGMENTS])

    @property
    def requirement(self) -> str:
        """Requirement on the runtime distribution this build links against."""
        if self.backend == 'vtk':
            major, minor = (int(s) for s in self.generation.split('.'))
            return f'vtk>={major}.{minor},<{major}.{minor + 1}'
        # `cvista` carries the import package in every generation; the `rendering`
        # extra pulls the split-out rendering distribution where one exists.
        return f'cvista[rendering]=={self.generation}.*'


def _read(pattern: str, path: Path) -> str:
    match = re.search(pattern, path.read_text(encoding='utf-8')) if path.is_file() else None
    if match is None:
        msg = f'cannot read an SDK version from {path}'
        raise SystemExit(msg)
    return match.group(1)


def _variant(backend: str, vtk_dir: Path) -> Variant:
    if not (vtk_dir / 'vtk-config.cmake').is_file():
        msg = f'{vtk_dir} holds no vtk-config.cmake'
        raise SystemExit(msg)
    if backend == 'cvista':
        # Same source extensions/PyVistaRenderPassesVTKIdentity.cmake reads.
        version = _read(r"version\s*=\s*'([^']+)'", vtk_dir.parent / '_version.py')
    else:
        version = _read(r'set\(PACKAGE_VERSION "([^"]+)"\)', vtk_dir / 'vtk-config-version.cmake')
    return Variant(backend, vtk_dir.resolve(), version)


def find_stock_sdk(sdk_root: Path) -> Variant | None:
    """Return the Kitware wheel SDK fetched for this interpreter, if exactly one is.

    Parameters
    ----------
    sdk_root : pathlib.Path
        Directory ``scripts/fetch_vtk_sdk.py`` unpacks into.

    Returns
    -------
    Variant or None
        The stock variant, or ``None`` when no SDK was fetched.

    """
    py = f'cp{sys.version_info.major}{sys.version_info.minor}'
    configs = list(
        sdk_root.glob(f'vtk-wheel-sdk-*-{py}-*/vtk-*.data/headers/cmake/vtk-config.cmake')
    )
    if len(configs) > 1:
        msg = f'more than one VTK wheel SDK for {py} under {sdk_root}: {sorted(configs)}'
        raise SystemExit(msg)
    return _variant('vtk', configs[0].parent) if configs else None


def discover(env: Mapping[str, str] | None = None) -> list[Variant]:
    """Return the variants this build compiles.

    Parameters
    ----------
    env : Mapping[str, str], optional
        Environment to read ``PVRP_BACKEND`` and ``PVRP_VTK_DIR`` from. Defaults
        to ``os.environ``.

    Returns
    -------
    list[Variant]
        The caller-selected variant, or every SDK found.

    """
    env = os.environ if env is None else env
    backend, vtk_dir = env.get('PVRP_BACKEND', ''), env.get('PVRP_VTK_DIR', '')
    if backend or vtk_dir:
        if not (backend and vtk_dir):
            msg = 'PVRP_BACKEND and PVRP_VTK_DIR select one variant and must be set together'
            raise SystemExit(msg)
        if backend not in BACKENDS:
            msg = f'PVRP_BACKEND must be one of {BACKENDS}, not {backend!r}'
            raise SystemExit(msg)
        return [_variant(backend, Path(vtk_dir))]

    found = []
    spec = importlib.util.find_spec('cvista_sdk')
    if spec is not None and spec.origin is not None:
        cmake_dir = Path(spec.origin).parent / 'cmake'
        if (cmake_dir / 'vtk-config.cmake').is_file():
            found.append(_variant('cvista', cmake_dir))
    if (stock := find_stock_sdk(ROOT / 'build' / 'vtk-sdk')) is not None:
        found.append(stock)
    if not found:
        msg = 'No VTK to build against: neither cvista-sdk nor a vtk wheel SDK was found'
        raise SystemExit(msg)
    return found


def build_state(state: str) -> None:
    """Record which build scikit-build-core is computing metadata for.

    Parameters
    ----------
    state : str
        ``'sdist'``, ``'wheel'``, ``'editable'`` or a ``metadata_*`` phase.

    """
    _BUILD['state'] = state


def dynamic_wheel(settings: Mapping[str, Any]) -> dict[str, bool]:
    """Report that the extras of a wheel may differ from its sdist's.

    Parameters
    ----------
    settings : Mapping[str, Any]
        Provider settings from ``pyproject.toml``, unused.

    Returns
    -------
    dict[str, bool]
        ``optional-dependencies`` marked dynamic in the sdist metadata.

    """
    del settings
    return {'optional-dependencies': True}


def dynamic_metadata(
    settings: Mapping[str, Any], project: Mapping[str, Any]
) -> dict[str, dict[str, list[str]]]:
    """Provide ``optional-dependencies``: one extra per built variant.

    An sdist compiles nothing, so it carries no extra and never looks for an
    SDK; the wheel built from it computes its own.

    Parameters
    ----------
    settings : Mapping[str, Any]
        Provider settings from ``pyproject.toml``; none are accepted.
    project : Mapping[str, Any]
        The static ``[project]`` table, unused.

    Returns
    -------
    dict[str, dict[str, list[str]]]
        ``{'optional-dependencies': {extra: [requirement]}}``.

    """
    del project
    if settings:
        msg = f'build_variants takes no settings, got {dict(settings)}'
        raise RuntimeError(msg)
    variants = [] if _BUILD['state'] == 'sdist' else discover()
    return {'optional-dependencies': {v.backend: [v.requirement] for v in variants}}


if __name__ == '__main__':
    for v in discover():
        print(f'{v.backend}|{v.vtk_dir.as_posix()}')
