"""A third-party VTK module builds, links, wraps and loads against the C++ SDK.

The SDK prefixes are assembled from the staging trees ``just sync`` built, the
way ``scripts/build_sdk.py`` packages the SDK wheel. The consumer in
``tests/sdk_consumer`` has methods that take and return ``pvRenderPassChain*``
and one that calls a ``std::string``-returning VTK method. Each negative test
withholds one thing the SDK exports and shows the consumer break without it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import zipfile

import pytest

import pyvista_render_passes
from pyvista_render_passes._backend import ROOT as RUNTIME_ROOT, VARIANT
from tests.conftest import load_script

CONSUMER = Path(__file__).resolve().parent / 'sdk_consumer'
CMAKE = Path('lib/cmake/PyVistaRenderPasses')
HIERARCHY = Path('lib/vtk/hierarchy/PyVistaRenderPasses/PyVistaRenderPasses-hierarchy.txt')


build_sdk = load_script('build_sdk')
build_variants = load_script('build_variants')

CHECK = """
import json, sys
sys.path.insert(0, sys.argv[1])
try:
    # Before anything else loads VTK: the consumer's rpath alone must resolve it.
    from sdk_consumer.SdkConsumer import pvChainHolder
except ImportError as exc:
    print(json.dumps({'error': str(exc)}))
    raise SystemExit(0)
import pyvista_render_passes as p
holder = pvChainHolder()
result = {'methods': sorted(n for n in dir(holder) if n.endswith('Chain'))}
if 'SetChain' in result['methods']:
    chain = p.pvRenderPassChain()
    holder.SetChain(chain)
    result['roundtrip'] = holder.GetChain() is chain
    result['description'] = holder.DescribeChain()
print(json.dumps(result))
"""


def _run(args, *, check=True, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(  # noqa: S603
        [str(a) for a in args], capture_output=True, text=True, check=False, **kwargs
    )
    if check:
        assert result.returncode == 0, f'{args}\n{result.stdout[-6000:]}\n{result.stderr[-6000:]}'
    return result


@pytest.fixture(scope='module')
def prefixes(tmp_path_factory) -> dict[str, Path]:
    return build_sdk.assemble(build_sdk.build_tree(), tmp_path_factory.mktemp('sdk'))


@pytest.fixture(scope='module')
def vtk_dirs() -> dict[str, Path]:
    return {v.backend: v.vtk_dir for v in build_variants.discover({})}


def _configure(source, build, prefix, vtk_dir, *extra, check=True):
    generator = ['-G', 'Ninja'] if shutil.which('ninja') else []
    return _run(
        [
            'cmake',
            '-S',
            source,
            '-B',
            build,
            *generator,
            f'-DVTK_DIR={vtk_dir}',
            f'-DPyVistaRenderPasses_DIR={prefix / CMAKE}',
            f'-DPython3_EXECUTABLE={sys.executable}',
            '-DCMAKE_BUILD_TYPE=Release',
            *extra,
        ],
        check=check,
    )


def _consumer(prefix: Path, vtk_dir: Path, work: Path, *, runtime_rpath: bool = True) -> dict:
    site = work / 'site'
    rpath = 'ON' if runtime_rpath else 'OFF'
    _configure(
        CONSUMER, work / 'build', prefix, vtk_dir, f'-DCMAKE_INSTALL_PREFIX={site}',
        f'-DCONSUMER_RUNTIME_RPATH={rpath}',
    )  # fmt: skip
    built = _run(['cmake', '--build', work / 'build', '--parallel'], check=False)
    if built.returncode:
        return {'error': built.stdout + built.stderr}
    _run(['cmake', '--install', work / 'build'])
    # The runtime wheels sit beside the consumer, where its relative rpath looks.
    installed = Path(pyvista_render_passes.__file__).resolve().parent.parent
    for entry in installed.iterdir():
        if entry.name == 'pyvista_render_passes' or entry.name.split('.')[0] == RUNTIME_ROOT:
            (site / entry.name).symlink_to(entry)
    loader_paths = {'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH'}
    env = {k: v for k, v in os.environ.items() if k not in loader_paths}
    env['PYVISTA_VTK_BACKEND'] = VARIANT
    out = _run([sys.executable, '-c', CHECK, site], env=env).stdout
    return json.loads(out.strip().splitlines()[-1])


def _copy(prefix: Path, tmp_path: Path) -> Path:
    copy = tmp_path / 'prefix'
    shutil.copytree(prefix, copy)
    return copy


def _strip(path: Path, pattern: str) -> None:
    text, removed = re.subn(pattern, '', path.read_text(encoding='utf-8'))
    assert removed == 1, f'{pattern!r} is not in {path.name} exactly once'
    path.write_text(text, encoding='utf-8')


def test_consumer_loads_first_and_wraps_methods_taking_the_chain(prefixes, vtk_dirs, tmp_path):
    result = _consumer(prefixes[VARIANT], vtk_dirs[VARIANT], tmp_path)
    assert result.get('methods') == ['DescribeChain', 'GetChain', 'SetChain'], result
    assert result['roundtrip'] is True
    assert result['description'].startswith('pvRenderPassChain')


def test_consumer_without_the_runtime_rpath_cannot_load_first(prefixes, vtk_dirs, tmp_path):
    result = _consumer(prefixes[VARIANT], vtk_dirs[VARIANT], tmp_path, runtime_rpath=False)
    error = result.get('error', '')
    # glibc's wording, then dyld's.
    missing = ('cannot open shared object file', 'Library not loaded', 'image not found')
    assert any(phrase in error for phrase in missing), result


def test_consumer_drops_those_methods_without_the_hierarchy_file(prefixes, vtk_dirs, tmp_path):
    prefix = _copy(prefixes[VARIANT], tmp_path)
    # An SDK without the hierarchy ships neither the file nor the property naming it.
    (prefix / HIERARCHY).unlink()
    _strip(
        prefix / CMAKE / 'PyVistaRenderPasses-vtk-module-properties.cmake',
        r'set_property\(TARGET[^)]*INTERFACE_vtk_module_hierarchy[^)]*\)\n',
    )
    result = _consumer(prefix, vtk_dirs[VARIANT], tmp_path / 'consumer')
    assert result == {'methods': ['DescribeChain']}


def test_consumer_needs_the_exported_libstdcxx_abi(prefixes, vtk_dirs, tmp_path):
    prefix = _copy(prefixes[VARIANT], tmp_path)
    targets = prefix / CMAKE / 'PyVistaRenderPasses-targets.cmake'
    if '_GLIBCXX_USE_CXX11_ABI=0' not in targets.read_text(encoding='utf-8'):
        pytest.skip(f'the {VARIANT} SDK uses the default libstdc++ ABI, so none is exported')
    _strip(targets, r';?_GLIBCXX_USE_CXX11_ABI=0')
    result = _consumer(prefix, vtk_dirs[VARIANT], tmp_path / 'consumer')
    assert 'GetObjectDescription' in result.get('error', ''), result


def _require(mapping: dict[str, Path], backend: str) -> Path:
    if backend not in mapping:
        pytest.skip(f'this platform builds no {backend} variant')
    return mapping[backend]


def _refusal(prefix: Path, vtk_dir: Path, work: Path) -> str:
    result = _configure(CONSUMER / 'find_only', work, prefix, vtk_dir, check=False)
    assert result.returncode != 0, 'find_package accepted a VTK it was not built against'
    return ' '.join((result.stdout + result.stderr).split())


def test_cvista_package_refuses_a_stock_sdk_of_the_same_version(prefixes, vtk_dirs, tmp_path):
    prefix, vtk_dir = _require(prefixes, 'cvista'), _require(vtk_dirs, 'vtk')
    assert 'is a vtk SDK' in _refusal(prefix, vtk_dir, tmp_path)


def test_stock_package_refuses_a_cvista_sdk_of_the_same_version(prefixes, vtk_dirs, tmp_path):
    prefix, vtk_dir = _require(prefixes, 'vtk'), _require(vtk_dirs, 'cvista')
    assert 'is a cvista SDK' in _refusal(prefix, vtk_dir, tmp_path)


def test_cvista_package_refuses_another_generation(prefixes, tmp_path):
    release = build_sdk.read_config(_require(prefixes, 'cvista'))['GENERATION'].split('.')
    other = '.'.join([*release[:3], str(int(release[3]) + 1)])
    sdk = tmp_path / 'other' / 'cvista_sdk'
    (sdk / 'cmake').mkdir(parents=True)
    # Just enough of an SDK for find_package(VTK) to succeed, one generation on.
    (sdk / 'cmake' / 'vtk-config.cmake').write_text('set(VTK_VERSION "9.7.0")\nset(VTK_FOUND 1)\n')
    (sdk / '_version.py').write_text(f"__version__ = version = '{other}'\n")
    output = _refusal(prefixes['cvista'], sdk / 'cmake', tmp_path / 'build')
    assert f"is generation '{other}'" in output


def test_package_refuses_a_vtk_with_the_other_libstdcxx_abi(prefixes, vtk_dirs, tmp_path):
    prefix = _copy(_require(prefixes, 'vtk'), tmp_path)
    cvista_dir = _require(vtk_dirs, 'cvista')
    stock = build_sdk.read_config(prefix)
    if stock['GLIBCXX_USE_CXX11_ABI'] != '0':
        pytest.skip('this stock SDK uses the default libstdc++ ABI, so the probe cannot refuse')
    # Record cvista's own backend and generation, so only the ABI probe can refuse.
    config = prefix / CMAKE / 'PyVistaRenderPassesConfig.cmake'
    generation = build_sdk.read_config(_require(prefixes, 'cvista'))['GENERATION']
    text = config.read_text(encoding='utf-8')
    text = text.replace('_BACKEND "vtk"', '_BACKEND "cvista"')
    text = text.replace(f'_GENERATION "{stock["GENERATION"]}"', f'_GENERATION "{generation}"')
    config.write_text(text, encoding='utf-8')
    assert 'libstdc++ ABI' in _refusal(prefix, cvista_dir, tmp_path / 'build')


def test_sdk_wheel_installs_and_carries_every_prefix(prefixes, tmp_path):
    plat = sysconfig.get_platform().replace('-', '_').replace('.', '_')
    wheel = build_sdk.write_wheel(prefixes, '1.2.3', plat, tmp_path)
    assert wheel.name == f'pyvista_render_passes_sdk-1.2.3-py3-none-{plat}.whl'
    archive = zipfile.ZipFile(wheel)
    names = set(archive.namelist())
    for backend in prefixes:
        root = f'pyvista_render_passes_sdk/prefix/{backend}'
        missing = [
            r for r in build_sdk.REQUIRED if f'{root}/{r.format(backend=backend)}' not in names
        ]
        assert not missing, backend
        assert any(n.startswith(f'{root}/pyvista_render_passes/_{backend}/lib') for n in names)
    assert not [n for n in names if build_sdk.is_python_module(Path(n))]
    metadata = archive.read('pyvista_render_passes_sdk-1.2.3.dist-info/METADATA').decode()
    if 'cvista' in prefixes:
        generation = build_sdk.read_config(prefixes['cvista'])['GENERATION']
        assert f'Requires-Dist: cvista-sdk=={generation}.*' in metadata
    # Installing reads RECORD, WHEEL and METADATA, which unzipping does not.
    uv = shutil.which('uv')
    assert uv is not None
    target = tmp_path / 'installed'
    _run([uv, 'pip', 'install', '--no-deps', '--target', target, wheel])
    env = os.environ | {'PYTHONPATH': str(target)}
    args = ['-m', 'pyvista_render_passes_sdk', '--cmake-dir', '--backend', VARIANT]
    cmake_dir = Path(_run([sys.executable, *args], env=env).stdout.strip())
    assert cmake_dir.is_relative_to(target)
    assert (cmake_dir / 'PyVistaRenderPassesConfig.cmake').is_file()
