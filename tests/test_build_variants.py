"""The runtime pin written into the wheel follows the SDK the build used."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib

import pytest

from tests.conftest import REPO, load_script

build_variants = load_script('build_variants')


def _cvista_sdk(root: Path, version: str) -> Path:
    cmake = root / 'cvista_sdk' / 'cmake'
    cmake.mkdir(parents=True)
    (cmake / 'vtk-config.cmake').write_text('')
    (root / 'cvista_sdk' / '_version.py').write_text(f"__version__ = version = '{version}'\n")
    return cmake


def _vtk_sdk(root: Path, version: str) -> Path:
    root.mkdir(parents=True)
    (root / 'vtk-config.cmake').write_text('')
    (root / 'vtk-config-version.cmake').write_text(f'set(PACKAGE_VERSION "{version}")\n')
    return root


@pytest.mark.parametrize(
    ('version', 'requirement'),
    [
        ('1.2.3.4.post56789+local.abcdef', 'cvista[rendering]==1.2.3.4.*'),
        ('9.7.0.5', 'cvista[rendering]==9.7.0.5.*'),
    ],
)
def test_cvista_pin_is_the_sdk_generation(tmp_path, version, requirement):
    env = {'PVRP_BACKEND': 'cvista', 'PVRP_VTK_DIR': str(_cvista_sdk(tmp_path, version))}
    (variant,) = build_variants.discover(env)
    assert variant.requirement == requirement


def test_vtk_pin_is_the_sdk_minor(tmp_path):
    env = {'PVRP_BACKEND': 'vtk', 'PVRP_VTK_DIR': str(_vtk_sdk(tmp_path / 'sdk', '9.6.1'))}
    (variant,) = build_variants.discover(env)
    assert variant.requirement == 'vtk>=9.6,<9.7'


def test_cvista_sdk_without_a_generation_is_refused(tmp_path):
    env = {'PVRP_BACKEND': 'cvista', 'PVRP_VTK_DIR': str(_cvista_sdk(tmp_path, '9.6.2'))}
    (variant,) = build_variants.discover(env)
    with pytest.raises(SystemExit, match='no ABI generation'):
        _ = variant.requirement


@pytest.mark.parametrize('env', [{'PVRP_BACKEND': 'cvista'}, {'PVRP_VTK_DIR': '/nowhere'}])
def test_half_a_selection_is_refused(env):
    with pytest.raises(SystemExit, match='set together'):
        build_variants.discover(env)


def _stock_sdk(root: Path, version: str) -> None:
    py = f'cp{sys.version_info.major}{sys.version_info.minor}'
    sdk = root / f'vtk-wheel-sdk-{version}-{py}-{py}-plat' / f'vtk-{version}.data' / 'headers'
    _vtk_sdk(sdk / 'cmake', version)


def test_one_stock_sdk_is_found(tmp_path):
    _stock_sdk(tmp_path, '9.7.0')
    variant = build_variants.find_stock_sdk(tmp_path)
    assert variant is not None
    assert variant.requirement == 'vtk>=9.7,<9.8'


def test_two_stock_sdks_for_one_interpreter_are_refused(tmp_path):
    _stock_sdk(tmp_path, '9.7.0')
    _stock_sdk(tmp_path, '9.7.1')
    with pytest.raises(SystemExit, match='more than one'):
        build_variants.find_stock_sdk(tmp_path)


def test_sdist_marks_extras_dynamic_and_looks_for_no_sdk(tmp_path):
    uv = shutil.which('uv')
    assert uv is not None
    # A selection that fails discovery: the sdist succeeds only if it never looks.
    env = {k: v for k, v in os.environ.items() if k != 'VIRTUAL_ENV'}
    env |= {'PVRP_BACKEND': 'cvista', 'PVRP_VTK_DIR': str(tmp_path / 'nowhere')}
    result = subprocess.run(  # noqa: S603
        [uv, 'build', '--sdist', '--out-dir', str(tmp_path), str(REPO)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    (sdist,) = tmp_path.glob('*.tar.gz')
    with tarfile.open(sdist) as tar:
        (member,) = [m for m in tar.getmembers() if re.fullmatch(r'[^/]+/PKG-INFO', m.name)]
        stream = tar.extractfile(member)
        assert stream is not None
        pkg_info = stream.read().decode()
    assert 'Dynamic: Provides-Extra' in pkg_info
    assert re.findall(r'^Provides-Extra: (.+)$', pkg_info, re.MULTILINE) == []


def test_sdk_dependency_group_pins_the_build_requirement():
    data = tomllib.loads((REPO / 'pyproject.toml').read_text(encoding='utf-8'))
    build = [r for r in data['build-system']['requires'] if r.startswith('cvista-sdk')]
    assert data['dependency-groups']['sdk'] == build
