"""Package the C++ SDK from the install trees a runtime wheel build already made.

Each variant build installs into ``<build tree>/staging-<backend>``: the runtime
files under ``pyvista_render_passes/`` and, beside them, the headers, CMake
package and hierarchy file the runtime wheel leaves out. This compiles nothing:
it copies each staging tree, minus the wrapped Python module, into
``prefix/<backend>/`` of the ``pyvista-render-passes-sdk`` wheel.

cibuildwheel's repair commands pass ``--built {wheel}`` (whose tag names the
build tree) and ``--repaired {dest_dir}`` (whose wheel lends the platform tag).
With neither, the tree ``just sync`` left for this interpreter is used.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from importlib.metadata import PackageNotFoundError, version as dist_version
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / 'scripts' / 'sdk' / 'pyvista_render_passes_sdk'
CMAKE_DIR = Path('lib/cmake/PyVistaRenderPasses')
REQUIRED = (
    'lib/cmake/PyVistaRenderPasses/PyVistaRenderPassesConfig.cmake',
    'lib/cmake/PyVistaRenderPasses/PyVistaRenderPassesConfigVersion.cmake',
    'lib/cmake/PyVistaRenderPasses/PyVistaRenderPassesVTKIdentity.cmake',
    # vtk_module_build writes these two only when INSTALL_EXPORT is set.
    'lib/cmake/PyVistaRenderPasses/PyVistaRenderPasses-targets.cmake',
    'lib/cmake/PyVistaRenderPasses/PyVistaRenderPasses-vtk-module-properties.cmake',
    'lib/cmake/PyVistaRenderPasses/pyvista_render_passes._{backend}-vtk-python-module-properties.cmake',
    'lib/vtk/hierarchy/PyVistaRenderPasses/PyVistaRenderPasses-hierarchy.txt',
    'include/pyvista-render-passes/PyVistaRenderPassesModule.h',
    'include/pyvista-render-passes/pvPropKeyFilterPass.h',
    'include/pyvista-render-passes/pvRenderPassChain.h',
    'include/pyvista-render-passes/pvSSAAVolumePass.h',
)


def is_python_module(path: Path) -> bool:
    """Return whether ``path`` is the wrapped extension module only the runtime wheel ships.

    Parameters
    ----------
    path : pathlib.Path
        A file in a staging tree.

    Returns
    -------
    bool
        ``True`` for ``PyVistaRenderPasses*.so`` / ``.pyd``, not ``libPyVistaRenderPasses``.

    """
    return path.name.startswith('PyVistaRenderPasses.') and path.suffix in {'.so', '.pyd'}


def build_tree(built: Path | None = None) -> Path:
    """Return the scikit-build-core build tree holding the ``staging-<backend>`` trees.

    Parameters
    ----------
    built : pathlib.Path, optional
        The wheel that tree produced; defaults to the one tree for this interpreter.

    Returns
    -------
    pathlib.Path
        ``build/<wheel tag>``.

    """
    if built is not None:
        tree = ROOT / 'build' / '-'.join(built.name.removesuffix('.whl').split('-')[-3:])
    else:
        py = f'cp{sys.version_info.major}{sys.version_info.minor}'
        trees = [t for t in sorted((ROOT / 'build').glob(f'{py}-*')) if any(t.glob('staging-*'))]
        if len(trees) != 1:
            msg = f'expected one build tree for {py} under build/, found {trees}; run `just sync`'
            raise SystemExit(msg)
        (tree,) = trees
    if not any(tree.glob('staging-*')):
        msg = f'{tree} holds no staging-<backend> tree'
        raise SystemExit(msg)
    return tree


def read_config(prefix: Path) -> dict[str, str]:
    """Return the ``PyVistaRenderPasses_*`` values a prefix's package config records.

    Parameters
    ----------
    prefix : pathlib.Path
        An SDK install prefix.

    Returns
    -------
    dict[str, str]
        ``BACKEND``, ``GENERATION``, ``GLIBCXX_USE_CXX11_ABI`` and ``STATIC``.

    """
    text = (prefix / CMAKE_DIR / 'PyVistaRenderPassesConfig.cmake').read_text(encoding='utf-8')
    return dict(re.findall(r'set\(PyVistaRenderPasses_(\w+) "([^"]*)"\)', text))


def _check_prefix(prefix: Path, backend: str, build_host_paths: tuple[str, ...]) -> None:
    problems = [
        f'missing {rel}'
        for rel in (r.format(backend=backend) for r in REQUIRED)
        if not (prefix / rel).is_file()
    ]
    problems += [
        f'ships {p.relative_to(prefix)}' for p in prefix.rglob('*') if is_python_module(p)
    ]
    for path in (prefix / CMAKE_DIR).glob('*.cmake'):
        text = path.read_text(encoding='utf-8', errors='replace')
        problems += [f'{path.name} names {n}' for n in build_host_paths if n in text]
    if problems:
        msg = f'incomplete SDK prefix {prefix}: ' + '; '.join(problems)
        raise SystemExit(msg)


def _ignore(directory: str, names: list[str]) -> set[str]:
    skip = {'__init__.py', '__pycache__'}
    return {n for n in names if n in skip or is_python_module(Path(directory, n))}


def assemble(tree: Path, out: Path) -> dict[str, Path]:
    """Copy each staging tree of ``tree``, minus the Python module, to ``out/<backend>``.

    Parameters
    ----------
    tree : pathlib.Path
        A build tree from :func:`build_tree`.
    out : pathlib.Path
        Directory to hold one prefix per backend.

    Returns
    -------
    dict[str, pathlib.Path]
        Backend to its checked prefix.

    """
    prefixes = {}
    for staging in sorted(tree.glob('staging-*')):
        backend = staging.name.removeprefix('staging-')
        prefix = out / backend
        shutil.rmtree(prefix, ignore_errors=True)
        shutil.copytree(staging, prefix, ignore=_ignore)
        _check_prefix(prefix, backend, (str(staging), str(ROOT)))
        prefixes[backend] = prefix
    return prefixes


def platform_tag(tree: Path, repaired: Path | None) -> str:
    """Return the platform tag of the repaired runtime wheel, else of the build tree.

    Parameters
    ----------
    tree : pathlib.Path
        A build tree from :func:`build_tree`.
    repaired : pathlib.Path, optional
        Directory holding the one repaired runtime wheel.

    Returns
    -------
    str
        For example ``manylinux_2_27_x86_64.manylinux_2_28_x86_64``.

    """
    if repaired is None:
        return tree.name.split('-', 2)[2]
    wheels = [
        w for w in repaired.glob('*.whl') if not w.name.startswith('pyvista_render_passes_sdk')
    ]
    if len(wheels) != 1:
        msg = f'expected one repaired runtime wheel in {repaired}, found {wheels}'
        raise SystemExit(msg)
    return wheels[0].name.removesuffix('.whl').split('-')[-1]


def _digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()


def write_wheel(prefixes: dict[str, Path], version: str, plat: str, out: Path) -> Path:
    """Write the ``pyvista-render-passes-sdk`` wheel carrying ``prefixes``.

    Parameters
    ----------
    prefixes : dict[str, pathlib.Path]
        Backend to prefix, from :func:`assemble`.
    version : str
        The runtime wheel's version.
    plat : str
        Platform tag, from :func:`platform_tag`.
    out : pathlib.Path
        Output directory.

    Returns
    -------
    pathlib.Path
        The wheel written.

    """
    files: dict[str, bytes] = {
        f'pyvista_render_passes_sdk/{src.name}': src.read_bytes()
        for src in sorted(TEMPLATE.glob('*.py'))
    }
    info = f'"""Written by scripts/build_sdk.py."""\n\nVERSION = {version!r}\n'
    info += f'BACKENDS = {tuple(sorted(prefixes))!r}\n'
    files['pyvista_render_passes_sdk/_build_info.py'] = info.encode()
    for backend, prefix in sorted(prefixes.items()):
        for path in sorted(p for p in prefix.rglob('*') if p.is_file()):
            name = (
                f'pyvista_render_passes_sdk/prefix/{backend}/{path.relative_to(prefix).as_posix()}'
            )
            files[name] = path.read_bytes()
    requires = []
    if (cvista := prefixes.get('cvista')) is not None:
        requires.append(f'Requires-Dist: cvista-sdk=={read_config(cvista)["GENERATION"]}.*')
    dist_info = f'pyvista_render_passes_sdk-{version}.dist-info'
    files[f'{dist_info}/METADATA'] = '\n'.join(
        [
            'Metadata-Version: 2.1',
            'Name: pyvista-render-passes-sdk',
            f'Version: {version}',
            'Summary: C++ SDK for pyvista-render-passes: headers, CMake package, hierarchy files',
            'License: MIT AND BSD-3-Clause',
            'Requires-Python: >=3.12',
            *requires,
            '',
        ]
    ).encode()
    files[f'{dist_info}/WHEEL'] = '\n'.join(
        [
            'Wheel-Version: 1.0',
            'Generator: scripts/build_sdk.py',
            'Root-Is-Purelib: false',
            *(f'Tag: py3-none-{p}' for p in plat.split('.')),
            '',
        ]
    ).encode()
    record = [f'{name},sha256={_digest(data)},{len(data)}' for name, data in files.items()]
    files[f'{dist_info}/RECORD'] = '\n'.join([*record, f'{dist_info}/RECORD,,', '']).encode()

    out.mkdir(parents=True, exist_ok=True)
    wheel = out / f'pyvista_render_passes_sdk-{version}-py3-none-{plat}.whl'
    with zipfile.ZipFile(wheel, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            mode = 0o755 if re.search(r'\.(so|dylib|dll)(\.|$)', name) else 0o644
            entry.external_attr = mode << 16
            archive.writestr(entry, data, zipfile.ZIP_DEFLATED)
    return wheel


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--built', type=Path, help='wheel scikit-build-core built')
    parser.add_argument('--repaired', type=Path, help='dir holding the repaired runtime wheel')
    parser.add_argument('--out', type=Path, default=ROOT / 'build' / 'sdk-dist')
    args = parser.parse_args()
    tree = build_tree(args.built)
    out = args.out.resolve()
    prefixes = assemble(tree, tree / 'sdk-prefix')
    if args.built is not None:
        version = args.built.name.split('-')[1]
    else:
        try:
            version = dist_version('pyvista-render-passes')
        except PackageNotFoundError:
            version = '0.0.0'
    print(write_wheel(prefixes, version, platform_tag(tree, args.repaired), out))


if __name__ == '__main__':
    main()
