"""Builds catr-bundle.zip: a private Python runtime, catr and pcfsserver.exe for CatR-Tools.exe.

    py -3 -I make_bundle.py --catr <catr folder> [--workspace <workspace>] --pcfs-server <pcfsserver.exe> --out <zip>

Only the standard library is needed (catr uses nothing else). The runtime is copied from the
Python running this script: python.exe, its DLLs and extension modules, and the standard library
compiled into python3XX.zip, with a ._pth file so nothing outside the bundle is searched.
Left out: tkinter/Tcl/Tk, sqlite, tests, IDLE, ensurepip/venv and site-packages.
"""
import argparse
import hashlib
import json
import py_compile
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

SKIP_LIB = {'test', 'tests', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'venv', 'site-packages',
            'lib2to3', 'pydoc_data', 'sqlite3', '__pycache__', '__phello__'}
SKIP_LIB_FILES = {'turtle.py', 'antigravity.py', 'this.py'}
SKIP_DLLS = ('tcl', 'tk', '_tkinter', 'sqlite3', '_sqlite3', '_test', 'xxlimited', 'python.cat')


def stdlib_zip(lib, target):
    """The standard library's modules and packages, compiled. Each .pyc names its source as Lib/...,
    so the build PC's folders aren't in it (zipfile.PyZipFile would put them there)."""
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive, \
            tempfile.TemporaryDirectory() as temp:
        compiled = Path(temp) / 'module.pyc'
        for source in sorted(lib.rglob('*.py')):
            relative = source.relative_to(lib)
            if set(relative.parts) & SKIP_LIB or source.name in SKIP_LIB_FILES:
                continue
            packages = list(relative.parents)[:-1]
            if not all((lib / package / '__init__.py').is_file() for package in packages):
                continue
            py_compile.compile(str(source), cfile=str(compiled), dfile='Lib/' + relative.as_posix(),
                               doraise=True, optimize=0)
            archive.write(compiled, relative.with_suffix('.pyc').as_posix())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--catr', required=True, type=Path)
    parser.add_argument('--workspace', type=Path,
                        help="catr's workspace (backups, captures); without it the exe chooses one when it runs")
    parser.add_argument('--pcfs-server', required=True, type=Path)
    parser.add_argument('--licenses', type=Path, help='folder of license files to include')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    home = Path(sys.base_prefix)
    tag = f'python{sys.version_info.major}{sys.version_info.minor}'
    if not (args.catr / 'catr.py').is_file():
        raise SystemExit(f'no catr.py in {args.catr}')
    if not args.pcfs_server.is_file():
        raise SystemExit(f'missing {args.pcfs_server}')
    work = args.out.with_suffix('.stdlib.zip')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stdlib_zip(home / 'Lib', work)

    manifest = {'python': sys.version.split()[0], 'built': datetime.now().isoformat(timespec='seconds')}
    if args.workspace:
        manifest['workspace'] = str(args.workspace.resolve())
    with zipfile.ZipFile(args.out, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for item in home.iterdir():
            if item.is_file() and item.suffix.lower() in ('.exe', '.dll') or item.name == 'LICENSE.txt':
                bundle.write(item, f'python/{item.name}')
        for item in (home / 'DLLs').iterdir():
            if item.suffix.lower() in ('.pyd', '.dll') and not item.name.lower().startswith(SKIP_DLLS):
                bundle.write(item, f'python/{item.name}')
        bundle.write(work, f'python/{tag}.zip')
        bundle.writestr(f'python/{tag}._pth', f'{tag}.zip\n.\n')
        bundle.write(args.catr / 'catr.py', 'catr/catr.py')
        for item in sorted((args.catr / 'src').rglob('*')):
            if item.is_file() and '__pycache__' not in item.parts and item.suffix != '.pyc':
                bundle.write(item, 'catr/' + item.relative_to(args.catr).as_posix())
        bundle.write(args.pcfs_server, 'pcfs/pcfsserver.exe')
        for item in sorted(args.licenses.iterdir()) if args.licenses else []:
            bundle.write(item, f'licenses/{item.name}')
        bundle.writestr('bundle.json', json.dumps(manifest, indent=2) + '\n')
    work.unlink()
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()[:12]
    print(f'{args.out} ({args.out.stat().st_size / 1048576:.1f} MB, {digest}); Python {manifest["python"]}')


if __name__ == '__main__':
    main()
