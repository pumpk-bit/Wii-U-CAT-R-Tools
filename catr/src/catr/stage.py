"""Stage an unpacked title (code/ content/ meta/) into the PCFS host root.

The layout is the one the Cafe SDK's PCFS uses (data/mlc/usr/title/...). Only the host root is written; the build folder is never changed and nothing is sent to the console.
"""
import re
import shutil
import subprocess
from pathlib import Path
from . import config
from .safety import title_id

OS_VERSION = '000500101000400A'     # the console's OS; a debug-OS build makes it try an OS relaunch (hangs)
DEBUG_EXTENSIONS = ('.elf', '.map', '.dla', '.dnm')    # debugger-only; ~590 MB for a Unity build


def xml_hex(text, tag):
    match = re.search(rf'<{tag}\b[^>]*>\s*([0-9A-Fa-f]{{16}})\s*</{tag}>', text)
    return match[1].upper() if match else None


def set_xml_hex(text, tag, value):
    return re.sub(rf'(<{tag}\b[^>]*>)\s*[0-9A-Fa-f]{{16}}\s*(</{tag}>)', rf'\g<1>{value}\g<2>', text)


def read_keep_bom(path):
    data = Path(path).read_bytes()
    bom = data.startswith(b'\xef\xbb\xbf')
    return data[3 if bom else 0:].decode('utf-8'), bom


def write_keep_bom(path, text, bom):
    Path(path).write_bytes((b'\xef\xbb\xbf' if bom else b'') + text.encode('utf-8'))


def staged_id(original, explicit=None, keep=False):
    """PCFS-only ID by default: last byte -> F0 (F1 if it already was F0), so an installed copy
    of the same title can't be launched instead."""
    if explicit:
        return title_id(explicit).upper()
    if keep:
        return original
    candidate = original[:14] + 'F0'
    return candidate if candidate != original else original[:14] + 'F1'


def stage(build, root, explicit=None, keep=False, os_version=OS_VERSION, cafe_root=None,
          include_debug=False):
    build, root = Path(build).resolve(), Path(root).resolve()
    for part in ('code', 'content', 'meta'):
        if not (build / part).is_dir():
            raise FileNotFoundError(f"Build folder has no '{part}' folder: {build}")
    for name in ('code/app.xml', 'meta/meta.xml', 'code/cos.xml'):
        if not (build / name).is_file():
            raise FileNotFoundError(f'Missing {build / name}')
    original = xml_hex(read_keep_bom(build / 'code/app.xml')[0], 'title_id')
    if not original:
        raise ValueError(f"No title_id in {build / 'code/app.xml'}")
    tid = staged_id(original, explicit, keep)
    target = root / 'data/mlc/usr/title' / tid[:8].lower() / tid[8:].lower()
    if build == target or build in target.parents or target in build.parents:
        raise ValueError(f'Build folder and staging target overlap: {build} / {target}')

    sys_data = root / 'data/mlc/sys/title/0005001b'
    if not sys_data.exists():          # ErrEula panics on start-up without the SDK's system data
        sdk = (Path(cafe_root) / 'data/mlc/sys/title/0005001b' if cafe_root
               else config.cafe_path('data/mlc/sys/title/0005001b')[1])
        if not sdk.exists():
            raise FileNotFoundError(f'Host root lacks sys/title/0005001b and the SDK copy is missing: {sdk}')
        shutil.copytree(sdk, sys_data)

    for part in ('code', 'content', 'meta'):
        args = ['robocopy', str(build / part), str(target / part), '/MIR', '/NFL', '/NDL', '/NJH', '/NJS', '/NP',
                '/R:1', '/W:1']
        if part == 'code' and not include_debug:
            args += ['/XF', *(f'*{ext}' for ext in DEBUG_EXTENSIONS)]
        code = subprocess.run(args, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode
        if code >= 8:                  # robocopy: 0-7 success, 8+ failure
            raise OSError(f'robocopy failed for {part} (exit {code})')
    if not include_debug:              # /XF also shields excluded files already in the target
        for path in (target / 'code').rglob('*'):
            if path.is_file() and path.suffix.lower() in DEBUG_EXTENSIONS:
                path.unlink()

    for name in ('code/app.xml', 'meta/meta.xml'):
        text, bom = read_keep_bom(target / name)
        text = set_xml_hex(set_xml_hex(text, 'title_id', tid), 'os_version', os_version)
        write_keep_bom(target / name, text, bom)
        if xml_hex(text, 'title_id') != tid:
            raise ValueError(f'Could not set title_id in {target / name}')

    cos = read_keep_bom(target / 'code/cos.xml')[0]
    boot = re.search(r'<argstr\b[^>]*>\s*([^<\s]+)', cos)
    files = [p for p in target.rglob('*') if p.is_file()]
    return {'title': tid.lower(), 'original': original.lower(), 'boot_file': boot[1] if boot else None,
            'path': str(target), 'files': len(files), 'bytes': sum(p.stat().st_size for p in files),
            'os_version': os_version}


def writable_save(root, tid):
    """Clear read-only flags in the title's PCFS save folder (data/mlc/usr/save/<hi>/<lo>).

    ACP copies the title's meta.xml into save/.../meta/ at every launch. When that copy was
    read-only on the PC, the open for writing failed (-196640 over PCFS), and the title's first
    save access ended in a fatal error (ACP_STATUS_INVALID_PARAM, 160-0101) on 2026-11-15.
    Returns the files changed (paths relative to the save folder).
    """
    import os
    import stat
    save = Path(root) / 'data/mlc/usr/save' / tid[:8].lower() / tid[8:].lower()
    changed = []
    if not save.is_dir():
        return changed
    for path in save.rglob('*'):
        if path.is_file() and not os.access(path, os.W_OK):
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
            changed.append(path.relative_to(save).as_posix())
    return changed
