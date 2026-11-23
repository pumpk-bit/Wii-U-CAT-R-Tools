"""Named backups of console folders, crash-log copies, and restores checked against SHA-1s.

A backup is a PC folder holding `manifest.json` and `files/<console path>`; every file is listed
with its size, SHA-1 and SHA-256. Reading only touches the console through FSA reads.
"""
import hashlib
import json
import posixpath
import time
from pathlib import Path

from . import config
from .safety import RECOVERY, SCT

RECOVERY_DIR = '/vol/storage_mlc01/sys/title/00050010/1f700700'
SCT_DIR = '/vol/storage_mlc01/sys/title/00050010/1f700500'
SETS = {
    'recovery-menu': {'roots': [RECOVERY_DIR + '/code', RECOVERY_DIR + '/meta'], 'title': RECOVERY},
    'sct': {'roots': [SCT_DIR + '/code', SCT_DIR + '/meta'], 'title': SCT},
    'sct-code': {'roots': [SCT_DIR + '/code'], 'title': SCT},
    'config': {'roots': ['/vol/system/config']},
}
BACKUP_ROOT = config.WORKSPACE / 'backups'
LOGS_ROOT = config.WORKSPACE / 'logs'
CRASH_LOGS = '/vol/system/logs'
MANIFEST = 'manifest.json'


def digests(data):
    return {'bytes': len(data), 'sha1': hashlib.sha1(data).hexdigest(), 'sha256': hashlib.sha256(data).hexdigest()}


def local_path(folder, console_path):
    """Where a console file lives inside a backup folder."""
    return Path(folder) / 'files' / console_path.lstrip('/')


def walk(session, root):
    """Console files under root (a file root is returned as itself), sorted."""
    if not session.stat(root)['directory']:
        return [root]
    found = []
    for entry in session.listdir(root):
        child = f"{root}/{entry['name']}"
        found.extend(walk(session, child) if entry['directory'] else [child])
    return sorted(found)


def copy_out(session, roots, folder, progress=None):
    """Read every file under roots into folder/files/..., check each local copy, return entries."""
    entries = []
    for root in roots:
        for path in walk(session, root):
            data = session.read_file(path, limit=256 * 1024 * 1024)
            target = local_path(folder, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if target.read_bytes() != data:
                raise OSError(f'local copy {target} did not read back')
            entries.append({'path': path, **digests(data)})
            if progress:
                progress(path, len(data))
    return entries


def write_manifest(folder, name, roots, files, **extra):
    manifest = {'format': 1, 'set': name, 'roots': list(roots), 'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
                **extra, 'files': files,
                'total_bytes': sum(f['bytes'] for f in files)}
    path = Path(folder) / MANIFEST
    path.write_text(json.dumps(manifest, indent=1) + '\n', encoding='utf-8')
    return manifest


def load(folder):
    path = Path(folder) / MANIFEST
    if not path.is_file():
        raise FileNotFoundError(f'{folder} has no {MANIFEST}; not a catr backup')
    manifest = json.loads(path.read_text('utf-8'))
    if manifest.get('format') != 1 or not isinstance(manifest.get('files'), list):
        raise ValueError(f'{path}: unknown manifest format')
    for item in manifest['files']:
        if not str(item.get('path', '')).startswith('/vol/') or '..' in item['path'].split('/'):
            raise ValueError(f'{path}: bad console path {item.get("path")!r}')
    return manifest


def verify_local(folder, manifest):
    """Problems with the PC copy (missing files, wrong size or SHA-1); empty when it is intact."""
    problems = []
    for item in manifest['files']:
        target = local_path(folder, item['path'])
        if not target.is_file():
            problems.append(f'missing {target}')
            continue
        data = target.read_bytes()
        if len(data) != item['bytes'] or hashlib.sha1(data).hexdigest() != item['sha1']:
            problems.append(f'{target} does not match its manifest SHA-1')
    return problems


def base_of(roots):
    """Folder the roots are restored relative to: the common parent of their parents."""
    return posixpath.commonpath([posixpath.dirname(r.rstrip('/')) for r in roots])


def targets(manifest, to=None):
    """{backed-up console path: console path to write}; `to` relocates the tree, keeping folder names."""
    if to is None:
        return {f['path']: f['path'] for f in manifest['files']}
    base = base_of(manifest['roots'])
    to = to.rstrip('/')
    return {f['path']: to + f['path'][len(base):] for f in manifest['files']}


def changes(old, new):
    """Compare two manifests' files by path: added, changed and removed paths."""
    before = {f['path']: f['sha1'] for f in old['files']} if old else {}
    after = {f['path']: f['sha1'] for f in new['files']}
    return {'added': sorted(p for p in after if p not in before),
            'changed': sorted(p for p in after if p in before and before[p] != after[p]),
            'removed': sorted(p for p in before if p not in after)}


def recovery_restore_path(path):
    """True for the only title files a restore may write in place: the Recovery Menu's code/ and meta/."""
    return any(path.startswith(f'{RECOVERY_DIR}/{part}/') and '/..' not in path for part in ('code', 'meta'))


def latest(prefix, root):
    """Newest earlier backup folder named <prefix>-*, or None."""
    found = sorted(p for p in Path(root).glob(prefix + '-*') if (p / MANIFEST).is_file())
    return found[-1] if found else None
