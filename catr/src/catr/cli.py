import argparse
import concurrent.futures
import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from . import __version__, config, pcfs, stage
from .actions import Watch, choose_method
from .safety import RECOVERY, title_id, user_command
from .config import audit
from .transport import serial
from .transport.wup import Session, probe


def output(value):
    print(json.dumps(value, indent=2))


def normalize(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


def local_networks():
    """The /24 networks of this PC's own private IPv4 addresses, where `find` looks without --ip."""
    addresses = {ipaddress.ip_address(info[4][0])
                 for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    return sorted({ipaddress.ip_network(f'{address}/24', strict=False) for address in addresses
                   if address.is_private and not address.is_loopback and not address.is_link_local})


def find(args):
    settings = config.load()
    if args.ip:
        candidates = [args.ip]
    else:
        networks = [ipaddress.ip_network(args.network, strict=False)] if args.network else local_networks()
        if not networks:
            raise ValueError('This PC has no private IPv4 address to search from; use find --ip ADDRESS')
        for network in networks:
            if network.version != 4 or network.prefixlen < 24 or not network.is_private:
                raise ValueError('Discovery requires a private IPv4 /24 or smaller network')
        def opened(ip):
            try:
                with socket.create_connection((str(ip), 1337), timeout=.25):
                    return str(ip)
            except OSError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
            candidates = [ip for ip in pool.map(opened, (ip for network in networks for ip in network.hosts())) if ip]
    # Probe sequentially: each server may support only one active client.
    results = [probe(ip) for ip in candidates]
    matches = [item for item in results if item['status'] == 'wupserver']
    report = {'results': results, 'saved': len(matches) == 1}
    if len(matches) == 1:
        settings.update(ip=matches[0]['ip'], baud=57600)
        try:            # without --port, a port saved earlier is kept
            settings['serial_port'] = serial.pick_port(args.port)
            report['serial_port'] = settings['serial_port']
        except ValueError as exc:       # the network commands work without one
            report['serial_port_not_saved'] = str(exc)
        config.save(settings)
    output(report)
    return 0 if len(matches) == 1 else 1


# IOSU build identity. fw.img holds the IOSU modules; reading it takes ~74 s over
# wupserver (2026-11-14), so `info` shows the recorded baseline and `info --iosu` re-hashes.
# The SD route to the deployed plugins hung the console on 2026-11-14 and isn't used.
IOSU_DIR = '/vol/system/title/00050010/1000400a/code/'
IOSU_FILES = ('fw.img', 'kernel.img')
IOSU_BASELINE = config.HOME / 'iosu.json'


def iosu_report(session, args, warnings):
    try:
        baseline = json.loads(IOSU_BASELINE.read_text('utf-8'))
    except (OSError, ValueError):
        baseline = None
    paths = [IOSU_DIR + name for name in IOSU_FILES] + list(args.iosu_file)
    if not args.iosu:
        sizes = {}
        for path in paths:
            try:
                sizes[path] = session.stat(path)['size']
            except (OSError, ValueError):
                sizes[path] = None
        if not baseline:
            warnings.append('IOSU build not hashed yet; run: catr info --iosu (about 80 s)')
            return {'sizes': sizes}
        changed = [p for p in paths if p in baseline['files'] and baseline['files'][p]['bytes'] != sizes.get(p)]
        if changed:
            warnings.append(f'IOSU file size differs from the baseline: {", ".join(changed)}; run catr info --iosu')
        return {'baseline': baseline, 'sizes_match': not changed}
    files = {}
    for path in paths:
        data = session.read_file(path, limit=64 * 1024 * 1024)
        files[path] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    result = {'files': files, 'hashed': time.strftime('%Y-%m-%dT%H:%M:%S')}
    if not baseline:
        IOSU_BASELINE.parent.mkdir(parents=True, exist_ok=True)
        IOSU_BASELINE.write_text(json.dumps(result, indent=2), encoding='utf-8')
        result['baseline'] = f'recorded in {IOSU_BASELINE}'
    else:
        diff = [p for p, f in files.items() if p in baseline['files'] and baseline['files'][p]['sha256'] != f['sha256']]
        result['matches_baseline'] = not diff
        if diff:
            warnings.append(f'IOSU differs from the baseline of {baseline["hashed"]}: {", ".join(diff)}; '
                            f'address-based work must be rechecked (delete {IOSU_BASELINE} to accept)')
    return result


def info(args):
    settings = config.load()
    ip = args.ip or settings.get('ip')
    report = {'toolkit': __version__, 'ip': ip, 'warnings': []}
    report['artifacts'] = []
    plugins = config.ROOT.parent / 'plugins'     # not in CatR-Tools.exe's built-in copy
    artifacts = [pcfs.SERVER, *sorted(plugins.glob('*/*.ipx'))]
    for path in artifacts:
        report['artifacts'].append({'source': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None})
    if not ip:
        report['warnings'].append('No console selected; run find --ip ADDRESS')
    else:
        try:
            with Session(ip) as session:
                report['probe'] = session.probe()
                report['default_title'] = session.default_title()
                if report['default_title'] != RECOVERY:
                    report['warnings'].append('Default title differs from Recovery Menu')
                maintenance = config.HOME / 'maintenance.json'
                if maintenance.exists():
                    report['maintenance'] = json.loads(maintenance.read_text('utf-8'))
                report['metadata'] = []
                for label, path in [('SCT', '/vol/storage_mlc01/sys/title/00050010/1f700500/code/app.xml'),
                                    ('OS', '/vol/system_slc/title/00050010/1000400a/code/app.xml')]:
                    item = {'name': label, 'source': path}
                    try:
                        root = ET.fromstring(session.read_file(path))
                        item['fields'] = {child.tag: child.text for child in root if child.tag in ('title_id', 'title_version', 'os_version', 'sdk_version')}
                    except (OSError, ValueError, ET.ParseError) as exc:
                        item['unavailable'] = str(exc)
                    report['metadata'].append(item)
                report['iosu'] = iosu_report(session, args, report['warnings'])
                report['free_space'] = []
                for volume in ('/vol/storage_mlc01', '/vol/system'):
                    try:
                        report['free_space'].append(session.space(volume))
                    except (OSError, ValueError) as exc:
                        report['free_space'].append({'volume': volume, 'unavailable': str(exc)})
        except (OSError, ValueError, ET.ParseError) as exc:
            report['console_error'] = str(exc)
    if not (config.HOME / 'serial.json').exists():
        report['serial_error'] = 'serial service not running; start it with: catr serial --background'
        output(report)
        return 1 if not ip or 'console_error' in report else 0
    try:
        report['serial'] = serial.rpc({'op': 'status'})
        report['sdkversion'] = serial.rpc({'op': 'cos', 'command': 'sdkversion'})
        report['sdkversion']['meaning'] = 'running title SDK; not installed OS version'
    except (OSError, RuntimeError, ValueError) as exc:
        report['serial_error'] = str(exc)
    output(report)
    return 1 if not ip or 'console_error' in report else 0


def console_ip(args):
    ip = getattr(args, 'ip', None) or config.load().get('ip')
    if not ip:
        raise ValueError('No console selected; run find --ip ADDRESS')
    return ip


def remote_path(path):
    if not path.startswith('/vol/'):
        raise ValueError(f'Console paths start with /vol/: {path}')
    return path.rstrip('/') or '/'


def ls(args):
    path = remote_path(args.path)
    with Session(console_ip(args), timeout=10) as session:
        entries = session.listdir(path)
    if args.json:
        output(entries)
    else:
        for entry in entries:
            print(f"{'<dir>' if entry['directory'] else entry['size']:>12}  {entry['name']}{'/' if entry['directory'] else ''}")
    return 0


def stat(args):
    with Session(console_ip(args), timeout=10) as session:
        output(session.stat(remote_path(args.path)))
    return 0


def get(args):
    """Copy a console file (or, with -r, a directory) to the PC. Reads only."""
    source = remote_path(args.path)
    target = Path(args.dest or Path(source).name)
    copied = []
    with Session(console_ip(args), timeout=10) as session:
        def fetch(src, dst):
            if dst.exists() and not args.force:
                raise FileExistsError(f'{dst} exists; use --force to overwrite')
            data = session.read_file(src, limit=args.limit)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            copied.append({'source': src, 'dest': str(dst), 'bytes': len(data),
                           'sha256': hashlib.sha256(data).hexdigest()})

        def walk(src, dst):
            for entry in session.listdir(src):
                child = f"{src}/{entry['name']}"
                if entry['directory']:
                    walk(child, dst / entry['name'])
                else:
                    fetch(child, dst / entry['name'])

        if session.stat(source)['directory']:
            if not args.recursive:
                raise IsADirectoryError(f'{source} is a directory; use -r')
            walk(source, target)
        else:
            fetch(source, target)
    output({'copied': copied, 'files': len(copied), 'bytes': sum(item['bytes'] for item in copied)})
    return 0


# Read back even with install --size-only: the TMD names the title, version and contents.
ALWAYS_READ_BACK = {'title.tmd'}


def put(args):
    """Copy a PC file (or, with -r, a folder) to the console; every file is read back and compared.

    Only `install --size-only` turns the read-back off (args.readback False); then each file's size
    is checked and title.tmd is still read back. MCP does NOT check content hashes while installing:
    on 2026-11-15 an image with one flipped byte in a content file's data installed without an
    error and the installed file had the damage.
    """
    from .safety import STAGING, write_check
    readback = getattr(args, 'readback', True)
    source = Path(args.source)
    target = write_check(remote_path(args.dest), args.unsafe)
    files = sorted(p for p in source.rglob('*') if p.is_file()) if source.is_dir() else [source]
    if source.is_dir() and not args.recursive:
        raise IsADirectoryError(f'{source} is a folder; use -r')
    if not files or not all(p.is_file() for p in files):
        raise FileNotFoundError(f'nothing to copy from {source}')
    total = sum(p.stat().st_size for p in files)
    plan = [(p, target + ('/' + p.relative_to(source).as_posix() if source.is_dir() else '')) for p in files]
    copied = []
    with Session(console_ip(args), timeout=30) as session:
        # One stat for a new target folder instead of one per file (~0.25 s each).
        existing = ([dst for _, dst in plan if session.exists(dst)]
                    if not source.is_dir() or session.exists(target) else [])
        if existing and not args.force:
            raise FileExistsError(f'{existing[0]} exists on the console ({len(existing)} files); use --force to overwrite')
        if existing or not target.startswith(STAGING):
            confirm(f'write {len(plan)} files ({total:,} bytes) to {target}'
                    + (f', overwriting {len(existing)}' if existing else ''), args.yes)
        audit('put', target, files=len(plan), bytes=total, overwrite=len(existing))
        made = set()
        for local, dst in plan:
            parent = dst.rsplit('/', 1)[0]
            parts = parent.split('/')
            for i in range(4, len(parts) + 1):        # /vol/<volume>/... : create missing folders
                folder = '/'.join(parts[:i])
                if folder not in made:
                    session.makedir(folder)
                    made.add(folder)
            data = local.read_bytes()
            started = time.monotonic()
            session.write_file(dst, data)
            full = readback or local.name in ALWAYS_READ_BACK
            if full:
                if session.read_file(dst, limit=len(data) + 1) != data:
                    raise OSError(f'read-back of {dst} differs from {local}; stopped (earlier files are in place)')
            else:
                size = session.stat(dst)['size']
                if size != len(data):
                    raise OSError(f'{dst} is {size:,} bytes on the console, {local} is {len(data):,}; stopped')
            copied.append({'source': str(local), 'dest': dst, 'bytes': len(data),
                           'sha256': hashlib.sha256(data).hexdigest(),
                           'check': 'read-back' if full else 'size',
                           'seconds': round(time.monotonic() - started, 1)})
            print(f'  {dst}  {len(data):,} bytes, ' + ('read back OK' if full else 'size OK'),
                  file=sys.stderr, flush=True)
    output({'copied': copied, 'files': len(copied), 'bytes': sum(item['bytes'] for item in copied)})
    return 0


def rm(args):
    """Remove a console file, or with -r a folder tree. Never under system or title folders."""
    from .safety import write_check
    target = write_check(remote_path(args.path), args.unsafe)
    with Session(console_ip(args), timeout=30) as session:
        info = session.exists(target)
        if not info:
            raise FileNotFoundError(f'{target} does not exist on the console')
        doomed = []

        def walk(path):
            for entry in session.listdir(path):
                child = f"{path}/{entry['name']}"
                if entry['directory']:
                    walk(child)
                doomed.append(child)
        if info['directory']:
            if not args.recursive:
                raise IsADirectoryError(f'{target} is a folder; use -r')
            walk(target)
        doomed.append(target)
        confirm(f'permanently delete {target} ({len(doomed)} entries) on the console', args.yes)
        audit('rm', target, entries=len(doomed))
        for path in doomed:
            session.remove(path)
    output({'removed': target, 'entries': len(doomed)})
    return 0


def mkdir(args):
    from .safety import write_check
    target = write_check(remote_path(args.path), args.unsafe)
    with Session(console_ip(args), timeout=10) as session:
        created = session.makedir(target)
    if created:
        audit('mkdir', target)
    output({'path': target, 'created': created})
    return 0


def is_pcfs(record):
    """A PCFS copy (storage_hfiomlc01 / hfio device): its files live on the PC."""
    return 'hfio' in record.get('device', '') or '/storage_hfio' in record.get('path', '')


def by_title(records):
    """One MCP record per title ID, preferring the console's own copy over a PCFS one.

    While PCFS is mounted MCP also lists the PC's copies (2026-11-14: the Recovery Menu, SCT
    updates and HOME Menu variants appeared twice, once on mlc and once on hfiomlc). The PCFS
    record is kept only when it is the only one.
    """
    chosen = {}
    for record in records:
        old = chosen.get(record['title'])
        if old is None or (is_pcfs(old) and not is_pcfs(record)):
            chosen[record['title']] = record
    return chosen


def tmd_identity(data):
    """(title id, title version, os version) from a signed TMD (RSA-2048 signature type 0x10004)."""
    import struct
    if len(data) < 0x1E0 or struct.unpack('>I', data[:4])[0] != 0x10004:
        raise ValueError('not an RSA-2048 signed title.tmd')
    return data[0x18C:0x194].hex(), struct.unpack('>H', data[0x1DC:0x1DE])[0], data[0x184:0x18C].hex()


def makecfmaster():
    """(SDK root, makecfmaster.exe)."""
    return config.cafe_path('system/bin/tool/mastering/makecfmaster.exe')


def wumad_image(wumad):
    """Convert a .wumad into an install image with `makecfmaster installimage` (as the build
    scripts do). Returns (image folder, temporary folder to delete afterwards).

    The result is the same, file for file, as the Install folder the title build scripts make.
    """
    import shutil
    import tempfile
    root, tool = makecfmaster()
    temp = Path(tempfile.mkdtemp(prefix='catr-wumad-'))
    out, work = temp / 'out', temp / 'work'
    out.mkdir()
    work.mkdir()
    env = dict(os.environ, CAFE_ROOT=str(root),
               PATH=f"{tool.parent}{os.pathsep}{root / 'system/bin/tool'}{os.pathsep}{os.environ.get('PATH', '')}")
    try:
        done = subprocess.run([str(tool), 'installimage', '-i', str(wumad.resolve()), '-o', str(out), '-w', str(work)],
                              cwd=root, env=env, capture_output=True, text=True, errors='replace', timeout=900)
        images = [d for d in out.iterdir() if (d / 'title.tmd').is_file()]
        if done.returncode != 0 or len(images) != 1:
            tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
            raise OSError(f'makecfmaster installimage failed (exit {done.returncode}, {len(images)} images): '
                          + ' | '.join(tail))
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    print(f'  [catr] {wumad.name} -> install image {images[0].name}', file=sys.stderr, flush=True)
    return images[0], temp


def install(args):
    """Install an image folder: a console path, or a PC folder uploaded to catr staging first.

    A .wumad is converted to an install image first (makecfmaster installimage, temporary folder).
    """
    from .safety import STAGING, install_block
    if args.source.lower().endswith('.wumad'):
        import shutil
        wumad = Path(args.source)
        if not wumad.is_file():
            raise FileNotFoundError(args.source)
        image, temp = wumad_image(wumad)
        try:
            return install(argparse.Namespace(**dict(vars(args), source=str(image))))
        finally:
            shutil.rmtree(temp, ignore_errors=True)
    ip = console_ip(args)
    local = Path(args.source)
    staged = None
    if not args.source.startswith('/vol/'):
        if not (local / 'title.tmd').is_file():
            raise FileNotFoundError(f'{local} is not an install image folder (no title.tmd)')
        tmd = (local / 'title.tmd').read_bytes()
        console_path = f'{STAGING}/install/{local.name}'
    else:
        console_path = remote_path(args.source)
        with Session(ip, timeout=10) as session:
            tmd = session.read_file(console_path + '/title.tmd')
    tid, version, os_version = tmd_identity(tmd)
    with Session(ip, timeout=10) as session:
        installed = by_title(session.title_list())
        mlc = session.space('/vol/storage_mlc01')
    current = installed.get(tid)
    reason = install_block(tid, version, current['version'] if current else None)
    if reason:
        raise ValueError(f'{tid} v{version}: refused, {reason}')
    report = {'title': tid, 'version': version, 'os_version': os_version, 'source': args.source,
              'installed_before': f"v{current['version']} on {current['device']}" if current else None}
    if not local.is_dir() and not args.source.startswith('/vol/'):
        raise FileNotFoundError(args.source)
    size = sum(p.stat().st_size for p in local.iterdir() if p.is_file()) if not args.source.startswith('/vol/') else 0
    if size and size * 3 > mlc['free']:          # staging copy + installed copy + headroom
        raise OSError(f'not enough MLC space: image {size:,} bytes, free {mlc["free"]:,}')
    confirm(f'install {tid} v{version}' + (f' over v{current["version"]}' if current else '')
            + (f' (upload {size:,} bytes first)' if size else ''), args.yes)
    started = time.monotonic()
    if size:
        put(argparse.Namespace(source=str(local), dest=console_path, recursive=True, force=True, unsafe=False,
                               yes=True, ip=ip, readback=not args.size_only))
        staged = console_path
        report['upload_s'] = round(time.monotonic() - started, 1)
        report['upload_check'] = 'size only (content not checked)' if args.size_only else 'read-back'
    audit('install', console_path, title=tid, version=version)
    try:
        with Session(ip, timeout=900) as session:
            t0 = time.monotonic()
            report['install_info'] = list(session.install(console_path))
            report['install_s'] = round(time.monotonic() - t0, 1)
            after = by_title(session.title_list()).get(tid)
    finally:
        if staged and not args.keep:
            rm(argparse.Namespace(path=f'{STAGING}/install', recursive=True, unsafe=False, yes=True, ip=ip))
            report['staging'] = 'removed'
    report['installed_after'] = f"v{after['version']} on {after['device']}" if after else None
    report['outcome'] = 'installed' if after and after['version'] == version else 'not confirmed'
    output(report)
    return 0 if report['outcome'] == 'installed' else 1


def uninstall(args):
    from .safety import title_id as parse_tid, uninstall_block
    tid = parse_tid(args.title)
    ip = console_ip(args)
    with Session(ip, timeout=10) as session:
        current = by_title(session.title_list()).get(tid)
    if not current:
        raise ValueError(f'{tid} is not installed')
    if is_pcfs(current):
        raise ValueError(f"{tid} is only listed as a PCFS copy ({current['path']}); its files are on the PC, not uninstalled")
    reason = uninstall_block(tid, int(current['app_type'], 16), current.get('path', ''))
    if reason:
        raise ValueError(f'{tid}: refused, {reason}')
    confirm(f"uninstall {tid} v{current['version']} from {current['path']} (deletes it)", args.yes)
    audit('uninstall', current['path'], title=tid, version=current['version'])
    with Session(ip, timeout=300) as session:
        t0 = time.monotonic()
        session.uninstall(current['path'])
        seconds = round(time.monotonic() - t0, 1)
        gone = current['path'] not in {t['path'] for t in session.title_list()}
    report = {'title': tid, 'path': current['path'], 'uninstall_s': seconds,
              'outcome': 'removed' if gone else 'still listed'}
    output(report)
    return 0 if gone else 1


MAINTENANCE_FILE = config.HOME / 'maintenance.json'
# Wii U Menu title IDs by region; the installed one is used. Tiramisu starts from the Wii U Menu.
WII_U_MENU = ('0005001010040000', '0005001010040100', '0005001010040200')


def maintenance_target(name, installed):
    from .safety import SCT
    name = name.lower()
    if name == 'sct':
        tid = SCT
    elif name in ('menu', 'tiramisu'):
        tid = next((t for t in WII_U_MENU if t in installed), None)
        if not tid:
            raise ValueError('no Wii U Menu title is installed')
    else:
        extra = [t.lower() for t in config.load().get('maintenance_targets', [])]
        tid = title_id(name)
        if tid not in extra:
            raise ValueError(f'{tid} is not a maintenance target (sct, menu, tiramisu, or add it to '
                             f'maintenance_targets in {config.config_file()})')
    if tid not in installed:
        raise ValueError(f'{tid} is not installed')
    return tid


def maintenance(args):
    """Switch the boot title away from the Recovery Menu for a while, recorded so `end` restores it."""
    ip = console_ip(args)
    record = json.loads(MAINTENANCE_FILE.read_text('utf-8')) if MAINTENANCE_FILE.exists() else None
    with Session(ip, timeout=10) as session:
        current = session.default_title()
        installed = {tid for tid, t in by_title(session.title_list()).items() if not is_pcfs(t)}
    if args.target == 'status':
        output({'default_title': current, 'recorded': record,
                'state': 'normal' if current == RECOVERY and not record else 'maintenance'})
        return 0
    if args.target == 'end':
        goal, why = RECOVERY, 'restore the Recovery Menu as boot title'
        if current == RECOVERY and not record:
            output({'default_title': current, 'result': 'already the Recovery Menu'})
            return 0
        if RECOVERY not in installed:
            raise ValueError('the Recovery Menu is not installed; refusing to make it the boot title')
    else:
        goal = maintenance_target(args.target, installed)
        why = f'switch the boot title to {goal} for maintenance'
        if current != RECOVERY:
            raise ValueError(f'default title is {current}, not the Recovery Menu; run catr maintenance end first')
        if RECOVERY not in installed:
            raise ValueError('the Recovery Menu is not installed; refusing to switch away from it')
    confirm(f"{why}{'' if args.no_reboot else ', then reboot'}", args.yes)
    if goal != RECOVERY:
        MAINTENANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MAINTENANCE_FILE.write_text(json.dumps({'from': current, 'to': goal,
                                                'time': time.strftime('%Y-%m-%dT%H:%M:%S')}), encoding='utf-8')
    audit('default-title', goal, before=current)
    with Session(ip, timeout=10) as session:
        after = session.set_default_title(goal)
    if after != goal:
        raise OSError(f'default title reads back as {after}, not {goal}')
    report = {'default_title_before': current, 'default_title': after}
    if goal == RECOVERY:
        MAINTENANCE_FILE.unlink(missing_ok=True)
    if not args.no_reboot:
        report['reboot'] = reboot_console(ip, True)
    output(report)
    return 0 if args.no_reboot or report['reboot']['outcome'] == 'up' else 1


def default_title(args):
    with Session(console_ip(args), timeout=5) as session:
        tid = session.default_title()
    record = json.loads(MAINTENANCE_FILE.read_text('utf-8')) if MAINTENANCE_FILE.exists() else None
    output({'default_title': tid, 'is_recovery_menu': tid == RECOVERY, 'maintenance': record})
    return 0


def reboot_console(ip, yes, reason=''):
    """PM_Restart over wupserver, then follow the boot to the default title and wupserver.

    Returns a report; 'outcome' is 'up' only when the expected title started and wupserver
    answered again. PCFS doesn't survive a reboot: catr's server is stopped first.
    """
    with Session(ip, timeout=5) as session:
        expected = session.default_title()
    state = ensure_service()['title']
    confirm(f'{reason}reboot the console (PM_Restart); it boots the default title {expected}; '
            f'console now: {describe(state)}', yes)
    pcfs.Server(ip).stop()
    begin = serial.rpc({'op': 'status'})['cursor']
    audit('reboot', ip, expected=expected)
    started = time.monotonic()
    with Session(ip, timeout=5) as session:
        result = session.restart()
    report = {'expected_title': expected, 'pm_restart': 'connection dropped' if result is None else f'{result:#x}'}
    watch = Watch('launch', expected)
    outcome = follow({'begin': begin}, watch, 150)
    report['title'] = outcome
    report['boot_seen'] = watch.restarted
    report['title_s'] = round(time.monotonic() - started, 1)
    wup = None
    if outcome == 'launched':
        for _ in range(60):
            wup = probe(ip, timeout=2)['status']
            if wup == 'wupserver':
                break
            time.sleep(1)
        report['wupserver'] = wup
        report['wupserver_s'] = round(time.monotonic() - started, 1)
    else:
        report['console'] = console_health(ip)
    report['outcome'] = 'up' if outcome == 'launched' and wup == 'wupserver' else 'failed'
    return report


def reboot(args):
    report = reboot_console(console_ip(args), args.yes)
    output(report)
    return 0 if report['outcome'] == 'up' else 1


TITLE_ROOTS = ('/vol/storage_mlc01/sys/title', '/vol/storage_mlc01/usr/title')


NAMES_CACHE = config.HOME / 'title-names.json'
FS_NOT_FOUND = 0xFFFCFFE9      # FSA "not found" (-196631), as check() prints it: 4294770665


def title_names(session, found):
    """Fill item['name'] from each title's meta/meta.xml (longname_en, else shortname_en).

    A read costs ~1.8 s over wupserver, so names are cached per title, version and path in
    %APPDATA%/catr/title-names.json; only new or updated titles are read.
    """
    try:
        cache = json.loads(NAMES_CACHE.read_text('utf-8'))
    except (OSError, ValueError):
        cache = {}
    missing = [item for item in found if item.get('path')
               and f"{item['title']}:{item.get('version')}:{item['path']}" not in cache]
    if len(missing) > 3:
        print(f'[catr: reading {len(missing)} title names (~{len(missing) * 2} s); cached afterwards]',
              file=sys.stderr, flush=True)
    for item in found:
        if not item.get('path'):
            item['name_unavailable'] = 'no install path'
            continue
        key = f"{item['title']}:{item.get('version')}:{item['path']}"
        if key not in cache:
            try:
                meta = ET.fromstring(session.read_file(item['path'] + '/meta/meta.xml'))
                cache[key] = {'name': next((' '.join(meta.findtext(tag).split()) for tag in ('longname_en', 'shortname_en')
                                            if (meta.findtext(tag) or '').strip()), None)}
            except (OSError, ValueError, ET.ParseError) as exc:
                if f'IOSU result {FS_NOT_FOUND}' in str(exc):
                    # FSA "not found": the title has no meta.xml (000500001f701500 on SLC has only
                    # code/). Stable for this title, version and path, so cached.
                    cache[key] = {'unavailable': 'no meta.xml'}
                else:
                    # Not cached: other failures mean a busy or restarting console (2026-11-14 a
                    # restart mid-listing cached "no meta.xml" for most titles).
                    item['name_unavailable'] = f'meta.xml not read: {exc}'
                    continue
        if cache[key].get('name'):
            item['name'] = cache[key]['name']
        else:
            item['name_unavailable'] = cache[key].get('unavailable', 'no English name in meta.xml')
    try:
        NAMES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        NAMES_CACHE.write_text(json.dumps(cache, indent=1), encoding='utf-8')
    except OSError:
        pass


def titles(args):
    """Installed titles: MCP_TitleList by default, or the MLC title folders with --fsa."""
    found = []
    with Session(console_ip(args), timeout=10) as session:
        if not args.fsa:
            for item in session.title_list():
                if args.type and not item['title'].startswith(args.type.lower()):
                    continue
                item['location'] = item['device']
                found.append(item)
        for root in (TITLE_ROOTS if args.fsa else ()):
            for high in session.listdir(root):
                if not high['directory'] or len(high['name']) != 8:
                    continue
                if args.type and high['name'].lower() != args.type.lower():
                    continue
                for low in session.listdir(f"{root}/{high['name']}"):
                    if not low['directory'] or len(low['name']) != 8:
                        continue
                    item = {'title': (high['name'] + low['name']).lower(),
                            'location': root.split('/')[3], 'path': f"{root}/{high['name']}/{low['name']}"}
                    found.append(item)
        found.sort(key=lambda item: item['title'])
        if not args.no_names:
            title_names(session, found)
    if args.json:
        output(found)
    else:
        for item in found:
            label = item.get('name') or (f"({item['name_unavailable']})" if 'name_unavailable' in item else '')
            version = f"v{item['version']:<5}" if 'version' in item else ''
            print(f"{item['title']}  {item['location']:<4} {version} {label}".rstrip())
        print(f'{len(found)} titles')
    return 0


ICONS_DIR = config.HOME / 'icons'


def icons(args):
    """Title icons (meta/iconTex.tga) into %APPDATA%/catr/icons/<title>-v<version>.tga.

    Reads the console only. One JSON line per title on stdout as each icon is ready, so a GUI can
    show them while the rest load; already cached icons are reported without a read.
    """
    wanted = {t.lower() for t in args.titles}
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    with Session(console_ip(args), timeout=10) as session:
        found = [item for item in session.title_list()
                 if item.get('path') and (not wanted or item['title'] in wanted)]
        found.sort(key=lambda item: item['title'])
        fetched = 0
        for item in found:
            target = ICONS_DIR / f"{item['title']}-v{item.get('version', 0)}.tga"
            line = {'title': item['title'], 'file': str(target)}
            if target.exists() and not args.refresh:
                line['cached'] = True
            else:
                try:
                    data = session.read_file(item['path'] + '/meta/iconTex.tga')
                    temporary = target.with_suffix('.tmp')
                    temporary.write_bytes(data)
                    temporary.replace(target)
                    fetched += 1
                except (OSError, ValueError) as exc:
                    line = {'title': item['title'], 'error': str(exc)}
            print(json.dumps(line), flush=True)
    print(f'[catr: {fetched} icons read, {len(found) - fetched} cached or unavailable; {ICONS_DIR}]',
          file=sys.stderr, flush=True)
    return 0


def start_service(port=None):
    """Start the hidden serial owner (`serial --background`) and wait until it answers."""
    port = serial.pick_port(port)
    config.HOME.mkdir(parents=True, exist_ok=True)
    # Only remove the endpoint file when nothing owns the service port. Deleting it under a live
    # service (2026-11-14) left that service running, holding the COM port, with no way to reach it.
    try:
        socket.create_connection(('127.0.0.1', 47332), timeout=1).close()
        raise RuntimeError('a serial service is already listening on 127.0.0.1:47332'
                           + ('' if (config.HOME / 'serial.json').exists() else
                              ' but serial.json is missing; end that catr.py serial process first'))
    except OSError:
        pass
    (config.HOME / 'serial.json').unlink(missing_ok=True)    # stale file from a crashed run
    with (config.HOME / 'serial-service.log').open('ab') as log:
        child = subprocess.Popen([sys.executable, '-I', str(config.ROOT / 'catr.py'), 'serial', '--port', port],
                                 stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(50):
        if child.poll() is not None:
            raise RuntimeError(f'Serial service exited (is {port} in use?); see {config.HOME / "serial-service.log"}')
        try:
            return serial.rpc({'op': 'status'})
        except (OSError, ValueError, RuntimeError):
            time.sleep(.1)
    raise RuntimeError('Serial service startup unconfirmed; inspect serial-service.log')


def ensure_service():
    """Make sure the serial service answers; start it if it isn't running."""
    try:
        return serial.rpc({'op': 'status'}, timeout=3)
    except (FileNotFoundError, ConnectionRefusedError, json.JSONDecodeError):
        status = start_service()
        print(f"[catr: started serial service on {status.get('port', 'serial')}; stop with: catr serial --stop]",
              file=sys.stderr)
        return status


def confirm(question, yes):
    if yes:
        return
    if not sys.stdin.isatty():
        raise ValueError(f'{question}: needs confirmation; pass --yes when not at a terminal')
    try:
        answer = input(f'{question} [y/N] ')
    except EOFError:      # stdin claims a terminal but is closed (cmd /c under another tool)
        raise ValueError(f'{question}: no answer (stdin closed); nothing was sent. Pass --yes to confirm') from None
    if answer.strip().lower() not in ('y', 'yes'):
        raise ValueError('Cancelled; nothing was sent')


def describe(state):
    if state.get('state') == 'observed-title':
        return state['title'] + (' (Recovery Menu)' if state['title'] == RECOVERY else '')
    return state.get('state', 'unknown')


def follow(sent, watch, timeout, keeper=None):
    """Feed serial output from the send onward until the watch decides, or time runs out.

    With a PCFS keeper, the same text drives the per-boot reconnect, and polling is faster
    (the console's connection window is 10 s).
    """
    cursor, shown, shown_pcfs = sent['begin'], 0, 0
    deadline = time.monotonic() + timeout
    while True:
        result = serial.rpc({'op': 'log', 'cursor': cursor})
        if result['gap']:
            return 'uncertain (serial history gap)'
        if result['error']:
            return f"uncertain (serial error: {result['error']})"
        watch.feed(result['text'])
        cursor = result['cursor']
        events = watch.events
        if keeper:
            keeper.feed(result['text'])
            keeper.tick()
            for line in keeper.events[shown_pcfs:]:
                print(f'  [pcfs] {line}', file=sys.stderr, flush=True)
            shown_pcfs = len(keeper.events)
        for line in events[shown:]:
            print(f'  {line}', file=sys.stderr, flush=True)
        shown = len(events)
        if watch.outcome():
            return watch.outcome()
        if time.monotonic() > deadline:
            return 'uncertain (timed out)'
        time.sleep(.1 if keeper else .25)


def pcfs_keeper():
    """A keeper for catr's registered PCFS server, if one is running."""
    state = pcfs.Server.registered()
    return pcfs.Keeper(pcfs.Server(state['ip'], state['root'])) if state else None


def pcfs_report(keeper):
    boot = keeper.boot
    report = {'state': boot.state, 'server_starts': keeper.server.starts}
    if boot.init:
        report['tcp_init'] = boot.init
    if boot.detail:
        report['detail'] = boot.detail
    if boot.state in ('booting', 'unknown'):
        report['detail'] = 'no PCFSTCP plugin lines seen in this boot (plugin not loaded, or the console stopped logging)'
    return report


def launchable(ip, tid, slow=False):
    """The MCP record for tid, or raise why it can't be launched.

    A PCFS-staged copy (storage_hfiomlc01) is only listed while PCFS is mounted.
    """
    from .safety import boot_critical
    if tid != RECOVERY and boot_critical(tid):
        raise ValueError(f'{tid} is boot-critical (boot1, firmware, OS or system updater); not launched')
    with Session(ip, timeout=10) as session:
        listed = by_title(session.title_list())
    if tid in listed and is_pcfs(listed[tid]):
        # PCFS-staged: its files live on the PC. A Cafe restart drops PCFS, and launching it
        # with no server serving gave error 160-0101 on 2026-11-14. catr run handles both.
        state = ensure_service()['title']
        if not pcfs.Server.registered():
            raise ValueError(f'{tid} is a PCFS copy and no catr PCFS server is running; use catr run {tid}')
        if choose_method(state, slow) != 'launch':
            raise ValueError(f'{tid} is a PCFS copy and the console is not on the Recovery Menu, so launching '
                             f'would restart Cafe and drop PCFS; use catr run {tid}')
    if tid not in listed:
        hint = (' A PCFS-staged copy (..f0) is only listed while PCFS is mounted, and only if it was staged '
                f'before the mount: catr run {tid} remounts when needed.'
                if tid.endswith('f0') or tid.endswith('f1') else '')
        raise ValueError(f'{tid} is not installed (not in the MCP title list).{hint}')
    return listed[tid]


def launch(args):
    tid = title_id(args.tid)
    record = launchable(console_ip(args), tid, args.slow) if not args.unchecked else None
    state = ensure_service()['title']
    if state.get('state') == 'observed-title' and state['title'] == tid and not args.force:
        output({'outcome': 'already-running', 'title': tid, 'sent': None})
        return 0
    method = choose_method(state, args.slow)
    launch_args = list(getattr(args, 'args', None) or [])
    if launch_args:
        # `cos launchex tid args` works where `cos launch` does (Recovery Menu or ROOT in front);
        # slowlaunch takes no arguments.
        from .safety import launch_args as check_args
        check_args(launch_args)
        if method != 'launch':
            raise ValueError(f'arguments need the Recovery Menu or ROOT in front (cos launchex); console now: '
                             f'{describe(state)}. Run catr kill or catr stop first')
        method = 'launchex'
    confirm(f"{method} {tid}{' ' + ' '.join(launch_args) if launch_args else ''}; console now: {describe(state)}",
            args.yes)
    keeper = pcfs_keeper()
    sent = serial.rpc({'op': 'act', 'action': method, 'tid': tid, 'args': launch_args}, timeout=20)
    if not sent['proof']:
        print('  no echo seen for the command; watching anyway, not resending', file=sys.stderr)
    watch = Watch('launch', tid)
    outcome = follow(sent, watch, 90 if method == 'slowlaunch' else 60, keeper)
    report = {'outcome': outcome, 'title': tid, 'location': record['device'] if record else 'unchecked',
              'method': method, 'sent': sent['wire'],
              'proof': sent['proof'], 'before': describe(sent['before']),
              'now': describe(watch.title.value), 'cafe_restarted': watch.restarted}
    if watch.restarted and keeper:
        report['pcfs'] = pcfs_report(keeper)
        if keeper.boot.state != 'mounted':
            report['note'] = ('PCFS did not come back in this boot; run catr pcfs up, then launch '
                              'again from the Recovery Menu (fast, no restart)')
    elif watch.restarted:
        report['note'] = 'Cafe restarted; no catr PCFS server is registered (catr pcfs up starts one)'
    if outcome.startswith('uncertain'):
        report['next'] = 'check with: catr log --grep TitleId   (do not just resend)'
    output(report)
    return {'launched': 0}.get(outcome, 1 if outcome == 'other-title' else 2)


def syslog_title(args):
    """Master title from the IOSU syslog (this boot), by the same rule the serial service uses.

    For when the serial service started after the title did and so never saw its start lines.
    Reads MCP memory only (catr syslog); a later kill or restart in the log clears the result.
    """
    from . import syslog as sl
    with Session(console_ip(args), timeout=10) as session:
        text = sl.fetch(session.client.read)['text']
    state = serial.TitleState()
    state.feed(text.decode('utf-8', 'replace') + '\n')
    value = dict(state.value)
    if value['state'] == 'observed-title':
        value['reason'] = 'master title start seen in the IOSU syslog; not a foreground query'
    return value


def restart(args):
    """`cos killrestart`: abort the title in front and start it again; waits for its TitleId line."""
    state = ensure_service()['title']
    if state.get('state') == 'unknown' and state.get('reason') == 'daemon started':
        state = syslog_title(args)      # the service started after the title did
    if state.get('state') != 'observed-title':
        raise ValueError(f'console now: {describe(state)}; restart needs a known title in front '
                         '(after a fatal error cos kill/killrestart refuse: "only works when game is in foreground")')
    tid = state['title']
    confirm(f'kill and restart {describe(state)} (cos killrestart)', args.yes)
    sent = serial.rpc({'op': 'act', 'action': 'killrestart'}, timeout=20)
    watch = Watch('launch', tid)
    outcome = follow(sent, watch, 90)
    report = {'outcome': 'restarted' if outcome == 'launched' else outcome, 'title': tid, 'sent': sent['wire'],
              'proof': sent['proof'], 'now': describe(watch.title.value), 'cafe_restarted': watch.restarted}
    if outcome.startswith('uncertain'):
        report['next'] = 'check with: catr log --grep TitleId   (do not just resend)'
    output(report)
    return 0 if outcome == 'launched' else 2


def kill(args):
    state = ensure_service()['title']
    if state.get('state') == 'root' and not args.force:
        output({'outcome': 'already-root', 'sent': None})
        return 0
    held = debugger_holds_title()
    if held and not args.debugger_halted_ok:
        output({'outcome': 'refused', 'sent': None, 'debugger': held,
                'reason': 'the GDB stub is holding the title (stopped in the debugger). A kill of a '
                          'title stopped after a fault hung core 1 and needed a power cycle; a title '
                          'still waiting for its first debugger was killed cleanly (2026-11-21)',
                'next': 'detach or continue in GDB/MULTI first, then catr kill again '
                        '(--debugger-halted-ok sends it anyway)'})
        return 2
    question = f'kill the master title; console now: {describe(state)}'
    if held:
        question += f'. WARNING: the debugger stub holds the title ({held}); this kill may hang core 1'
    if state.get('title') == RECOVERY:
        question += '. The Recovery Menu will not restart by itself (catr launch recovery)'
    confirm(question, args.yes)
    sent = serial.rpc({'op': 'act', 'action': 'kill'}, timeout=20)
    watch = Watch('kill')
    outcome = follow(sent, watch, 30)
    report = {'outcome': outcome, 'sent': sent['wire'], 'proof': sent['proof'],
              'before': describe(sent['before'])}
    if outcome == 'root':
        report['next'] = 'console is WAITING IN ROOT; catr launch <tid> starts a title (cos launch, fast)'
    output(report)
    return 0 if outcome == 'root' else 2


def debugger_holds_title():
    """A short description when coreinit's GDB stub is holding the title in front, else None.

    Reads two words of coreinit data. When `cos modules` answers, the address must lie in a
    module's Data range as usual. While the stub holds every core, `cos modules` gets no reply
    at all (2026-11-21), but `cos memdump` of this one address still works; that address is
    coreinit 1000400A .data in every process, ROOT included, so it is read without the
    module check then. Any other failure counts as not held, so a title that is hung for
    other reasons can still be killed."""
    from . import debug as dbg
    try:
        modules = dbg.parse_modules(shell('cos modules'))
    except OSError:
        modules = []
    try:
        if modules:
            dbg.check_range(dbg.STUB_STATE, dbg.MEMDUMP_MIN_WORDS * 4, modules)
        parsed = dbg.parse_stub_state(shell(f'cos memdump {dbg.STUB_STATE:#010x} 8'))
    except (OSError, ValueError, RuntimeError):
        return None
    if not parsed or not dbg.stub_holds_title(*parsed):
        return None
    run_state, cores = parsed
    what = {0: 'all cores stopped in the stub', 3: 'stepping'}.get(run_state, f'run state {run_state}')
    return f'{what}, {cores} cores'


def pcfs_ready(need_recovery):
    """(registered server, console title) when PCFS is mounted and connected right now, else None."""
    status = ensure_service()
    boot = pcfs.current_boot(serial.rpc({'op': 'log', 'cursor': 0})['text'])
    registered = pcfs.Server.registered()
    if not (registered and boot.state == 'mounted'
            and pcfs.tcp_state(registered['pid'], registered['ip']) == 'ESTABLISHED'):
        return None
    if need_recovery and status['title'].get('title') != RECOVERY:
        return None
    return registered, status['title']


def ping(ip):
    """One ICMP echo (Windows ping); True if the console answered."""
    out = subprocess.run(['ping', '-n', '1', '-w', '1500', ip], capture_output=True, text=True, errors='replace',
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    return 'TTL=' in out.upper()


def console_health(ip):
    """What a failed restart left behind. Both hangs on 2026-11-14 ended with no serial output,
    no wupserver and (the second) no ping; neither left a fatal-error record in /vol/system/logs."""
    health = {'ping': ping(ip), 'wupserver': probe(ip, timeout=3)['status']}
    try:
        health['serial_quiet_s'] = serial.rpc({'op': 'status'}).get('quiet')
    except (OSError, RuntimeError, ValueError) as exc:
        health['serial'] = str(exc)
    try:
        from .actions import iosu_only_boot
        iosu_only = iosu_only_boot(normalize(serial.rpc({'op': 'log'})['text']))
    except (OSError, RuntimeError, ValueError, KeyError):
        iosu_only = False
    # Ping in recovery mode came and went (answered, then not, 2026-11-15), so it is not a condition.
    if health['wupserver'] != 'wupserver' and iosu_only and (health.get('serial_quiet_s') or 0) > 20:
        # The SDK's recovery mode (POWER + EJECT at power-on) re-flashes the system and erases
        # all data when an update is started; catr sends nothing in it.
        health['verdict'] = ('IOSU recovery mode (POWER + EJECT boot, no Cafe OS): nothing to do over '
                             'wupserver or the COS shell; power off and boot normally to leave it')
    elif not health['ping'] and health['wupserver'] != 'wupserver':
        health['verdict'] = 'console not responding (no ping, no wupserver): hung or off; power-cycle it'
    elif health['wupserver'] != 'wupserver' and (health.get('serial_quiet_s') or 0) > 30:
        health['verdict'] = 'console looks hung (serial silent, no wupserver); power-cycle it'
    return health


FRONT_WAIT_S = 30     # a boot reaches its master title ~20 s after "Starting System Log"

FRONT_NEXT = {
    'error-viewer': 'the console is showing (or starting) the error viewer after a fatal error. '
                    'Dismiss the error on the console; it then restarts into the Recovery Menu. '
                    'If it does not, or the screen is stuck, power-cycle it. Then run this again',
    'fatal': 'a fatal error was logged since the last boot. Dismiss the error on the console if one is '
             'shown; if the console is stuck or crashed, power-cycle it. Then run this again',
}


def front_guard(wait=FRONT_WAIT_S):
    """None when the serial log shows a restart is safe to send now; else a refusal report.

    Never restart over the error viewer or a launch in progress: on 2026-11-17 a slowlaunch sent
    1 s after the error viewer took the foreground crashed the kernel ("Launch or Call
    Outstanding", Process 1, Error 0xCAFEBADD). A boot, launch or shutdown in progress is waited
    out (up to `wait` s); the error viewer or a fatal error is refused at once.
    """
    from .actions import FRONT_SAFE, FRONT_SETTLING, front_check
    deadline, said = time.monotonic() + wait, None
    while True:
        front = front_check(normalize(serial.rpc({'op': 'log', 'cursor': 0})['text']))
        verdict = front['verdict']
        if verdict in FRONT_SAFE:
            return None
        if verdict not in FRONT_SETTLING or time.monotonic() >= deadline:
            break
        if verdict != said:
            print(f'  [catr] console {verdict.replace("-", " ")}; waiting up to {wait:g} s before restarting',
                  file=sys.stderr, flush=True)
            said = verdict
        time.sleep(.5)
    report = {'outcome': 'refused', 'sent': None,
              'reason': f'not restarting: console {verdict.replace("-", " ")}'
                        + (f' ({front["title"]})' if front['title'] else ''),
              'seen': front['seen']}
    if verdict in FRONT_SETTLING:
        report['reason'] += f', still after {wait:g} s'
        report['next'] = ('a boot or launch is still in progress; check catr log. If the console shows an '
                          'error, dismiss it; if it is stuck, power-cycle it. Then run this again')
    else:
        report['next'] = FRONT_NEXT[verdict]
    return report


def ensure_pcfs(ip, root=None, trace=False, tries=3, yes=False, force=False, need_recovery=False, purpose=''):
    """Get PCFS mounted with the Recovery Menu in front, restarting until a boot mounts it.

    Returns (ok, report). One confirmation covers all tries; each try is a new send made only
    after the previous one visibly finished (Recovery Menu up), never a blind resend.
    """
    server = pcfs.Server(ip, root, trace)
    server.check_conflicts()
    ready = None if force else pcfs_ready(need_recovery)
    if ready:
        registered, title = ready
        if root and Path(root).resolve() != Path(registered['root']).resolve():
            raise ValueError(f"PCFS is up on another root ({registered['root']}); catr pcfs down first")
        return True, {'outcome': 'already-up', 'server_pid': registered['pid'], 'root': registered['root'],
                      'console': describe(title)}
    ensure_service()
    refused = front_guard()
    if refused:
        return False, refused
    state = ensure_service()['title']
    if not ping(ip):
        raise RuntimeError(f'console {ip} does not answer ping; not sending a restart (power-cycle it if it is hung)')
    method = 'launch' if state.get('state') == 'root' else 'slowlaunch'
    confirm(f'{purpose}restart into the Recovery Menu ({method}) to mount PCFS, up to {tries} tries; '
            f'console now: {describe(state)}', yes)
    attempts = []
    for attempt in range(1, tries + 1):
        # Again right before every send: the console may have changed during the confirmation
        # prompt or the previous try (the previous try's server is already stopped).
        refused = front_guard()
        if refused:
            refused['attempts'] = attempts
            return False, refused
        keeper = pcfs.Keeper(server)
        sent = serial.rpc({'op': 'act', 'action': method, 'tid': RECOVERY}, timeout=20)
        watch = Watch('launch', RECOVERY)
        outcome = follow(sent, watch, 90, keeper)
        attempts.append({'attempt': attempt, 'sent': sent['wire'], 'proof': sent['proof'],
                         'title': outcome, 'pcfs': pcfs_report(keeper)})
        if outcome != 'launched':
            server.stop()
            return False, {'outcome': 'failed', 'reason': 'Recovery Menu did not come up; not retrying blindly',
                           'attempts': attempts, 'console': console_health(ip)}
        if keeper.boot.state == 'mounted':
            return True, {'outcome': 'up', 'server': pcfs.load_state(), 'attempts': attempts}
        print(f'  [pcfs] attempt {attempt}: {keeper.boot.detail or keeper.boot.state}', file=sys.stderr)
        server.stop()
        time.sleep(3)              # let the Recovery Menu settle before the next restart
        method = 'slowlaunch'
    return False, {'outcome': 'failed', 'reason': f'PCFS not mounted after {tries} tries', 'attempts': attempts}


def pcfs_up(args):
    ok, report = ensure_pcfs(console_ip(args), args.root, args.trace, args.tries, args.yes, args.force)
    if ok:
        report['next'] = 'catr launch <tid> starts a title from the Recovery Menu without a restart, so PCFS stays mounted'
    output(report)
    return 0 if ok else (2 if 'not retrying' in report.get('reason', '') else 1)


def stream_log(cursor, until=None, wait=None):
    """Print serial output from cursor until Ctrl+C, or until a line contains `until`.

    Returns True when `until` was seen, False when `wait` seconds ran out or on Ctrl+C.
    """
    deadline = time.monotonic() + wait if until and wait else None
    seen = ''
    try:
        while True:
            result = serial.rpc({'op': 'log', 'cursor': cursor})
            if result['gap']:
                print('[serial history gap]', file=sys.stderr)
            text = normalize(result['text'])
            print(text, end='', flush=True)
            cursor = result['cursor']
            if result['error']:
                raise OSError(result['error'])
            if until:
                seen = (seen + text)[-(len(until) + 4096):]
                if until in seen:
                    print(f'\n[catr: saw {until!r}; the title keeps running. catr stop returns to the Recovery Menu]',
                          file=sys.stderr)
                    return True
                if deadline and time.monotonic() > deadline:
                    print(f'\n[catr: {until!r} not seen in {wait:g} s; the title keeps running]', file=sys.stderr)
                    return False
            time.sleep(.25)
    except KeyboardInterrupt:
        print('\n[catr: stopped following the log; the title keeps running. catr stop returns to the Recovery Menu]',
              file=sys.stderr)
        return False


def pcfs_listed(ip, tid):
    """True if MCP lists tid at its PCFS location (wupserver title list, read-only).

    MCP reads the PCFS titles when PCFS mounts. A title staged after that is in the PC folder (the
    Recovery Menu lists the folder itself) but unknown to COS: `cos launch` then fails at once with
    "PrepareTitle ... returned error status -6" (2026-11-20). The next mount picks it up.
    """
    with Session(ip, timeout=10) as session:
        records = session.title_list()
    return any(record['title'] == tid and is_pcfs(record) for record in records)


def run(args):
    """Stage (optional), make sure PCFS is up with the Recovery Menu in front, launch fast, follow the log."""
    ip = console_ip(args)
    root = Path(args.root)          # checked_root() in main: always the one saved folder
    report = {}
    if args.target and Path(args.target).is_dir():
        report['staged'] = stage.stage(args.target, root, explicit=args.title_id, keep=args.keep_title_id,
                                       include_debug=args.include_debug)
        tid = report['staged']['title']
        print(f"  staged {report['staged']['original']} as {tid}: {report['staged']['files']} files, "
              f"{report['staged']['bytes'] / 1048576:.1f} MB -> {report['staged']['path']}", file=sys.stderr)
    else:
        tid = title_id(args.target)
        staged = root / 'data/mlc/usr/title' / tid[:8] / tid[8:]
        if not (staged / 'code/app.xml').is_file() and not args.installed:
            raise FileNotFoundError(f'Nothing staged for {tid} at {staged}; give the build folder, '
                                    'or --installed for a title installed on the console')
    fixed = stage.writable_save(root, tid)
    if fixed:
        report['save_made_writable'] = fixed
        print(f"  save folder: cleared read-only on {', '.join(fixed)} (ACP rewrites meta.xml at launch)",
              file=sys.stderr)
    remount = False
    if not args.installed and pcfs_ready(True):
        # PCFS is already up: was this title there when it mounted?
        try:
            remount = not pcfs_listed(ip, tid)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f'  could not read the title list ({exc}); launching without a remount', file=sys.stderr)
        if remount:
            report['remount'] = f'{tid} is not in the console\'s PCFS title list (staged after PCFS mounted)'
            print(f'  {tid} was staged after PCFS mounted; the console reads PCFS titles only at mount, '
                  'so PCFS is remounted first (restart into the Recovery Menu)', file=sys.stderr)
    ok, report['pcfs'] = ensure_pcfs(ip, str(root), args.trace, args.tries, args.yes, force=remount,
                                     need_recovery=True,
                                     purpose=f'run {tid}: ' + ('remount PCFS for the new title, ' if remount else ''))
    if not ok:
        output(report)
        return 1
    # Recovery Menu in front, PCFS mounted: a plain `cos launch` doesn't restart, so the mount survives.
    state = ensure_service()['title']
    sent = serial.rpc({'op': 'act', 'action': choose_method(state), 'tid': tid}, timeout=30)
    if not sent['proof']:
        print('  no echo seen for the launch; watching anyway, not resending', file=sys.stderr)
    watch = Watch('launch', tid)
    outcome = follow(sent, watch, 120, pcfs_keeper())
    deadline = time.monotonic() + 5            # the content mapping line follows the TitleId line
    cursor = sent['begin']
    while outcome == 'launched' and watch.source is None and time.monotonic() < deadline:
        time.sleep(.25)
        result = serial.rpc({'op': 'log', 'cursor': cursor})
        watch.feed(result['text'])
        cursor = result['cursor']
    report['launch'] = {'outcome': outcome, 'title': tid, 'sent': sent['wire'], 'proof': sent['proof'],
                        'loaded_from': watch.source, 'cafe_restarted': watch.restarted}
    if watch.failure:
        report['launch']['detail'] = watch.failure
    if outcome == 'launched' and watch.source == 'mlc' and not args.installed:
        report['launch']['warning'] = 'the console loaded its installed copy, not the PCFS one (stage without --keep-title-id)'
    output(report)
    if outcome != 'launched':
        return 1 if outcome in ('other-title', 'not-found') else 2
    if not args.no_follow:
        pcfs.release_lease()      # a background keeper may reconnect while the title runs
        print('[catr: following the serial log; Ctrl+C stops following]', file=sys.stderr)
        if getattr(args, 'until', None):
            return 0 if stream_log(sent['begin'], until=args.until, wait=args.wait) else 2
        stream_log(sent['begin'])
    return 0


def stalled_shutdown(sent):
    """A report when a slowlaunch stalled half-way through the PPC shutdown; None otherwise.

    Seen after error 160-0101 (2026-11-14, recreated 2026-11-15): "PPC TOLD TO SHUT DOWN", the
    error app (RAMPID 6) never exits, wupserver is gone (NET killed all sockets at the fatal
    error), the shell still answers. Nothing documented gets out of it: `cos kill` answers "kill
    command only works when game is in foreground." So catr sends nothing more and says so.
    """
    from .actions import stuck_shutdown
    text = serial.rpc({'op': 'log', 'cursor': sent['begin']})['text']
    if not stuck_shutdown(normalize(text)):
        return None
    return {'seen': 'PPC TOLD TO SHUT DOWN, no boot after it (error app blocks the shutdown)',
            'why_no_fallback': 'cos kill only works on a game in front; wupserver is down, so no reboot',
            'next': 'power-cycle the console'}


def stop(args):
    """Back to the Recovery Menu, then stop the host PCFS server (unless --keep-pcfs)."""
    state = ensure_service()['title']
    registered = pcfs.Server.registered()
    report = {'before': describe(state)}
    if state.get('title') != RECOVERY:
        method = 'launch' if state.get('state') == 'root' else 'slowlaunch'
        confirm(f'return to the Recovery Menu ({method}); console now: {describe(state)}', args.yes)
        # The server stays up until the console has restarted: cutting PCFS under a title that
        # reads from it hangs the console.
        keeper = pcfs_keeper() if args.keep_pcfs else None
        sent = serial.rpc({'op': 'act', 'action': method, 'tid': RECOVERY}, timeout=20)
        watch = Watch('launch', RECOVERY)
        outcome = follow(sent, watch, 90, keeper)
        report.update(outcome=outcome, sent=sent['wire'], proof=sent['proof'], cafe_restarted=watch.restarted)
        if keeper:
            report['pcfs'] = pcfs_report(keeper)
        if outcome != 'launched':
            # Fallback: a wupserver reboot, when wupserver still answers.
            # PM_Restart brought the Recovery Menu back in 33 s on 2026-11-14.
            if probe(ip := console_ip(args), timeout=3)['status'] == 'wupserver':
                print('  [catr] Recovery Menu not confirmed; falling back to a reboot', file=sys.stderr)
                report['reboot'] = reboot_console(ip, args.yes, 'slowlaunch did not confirm; ')
                if report['reboot']['outcome'] == 'up':
                    report['outcome'] = 'recovered by reboot'
                    if registered:
                        report['pcfs_server'] = 'stopped by the reboot'
                    output(report)
                    return 0
            else:
                stalled = stalled_shutdown(sent)
                if stalled:
                    report['stalled_shutdown'] = stalled
            report['next'] = ('Recovery Menu not confirmed and no working reboot; PCFS server left running. '
                              'Check catr log; if the console hangs, power cycle it')
            report['console'] = console_health(console_ip(args))
            output(report)
            return 2
    else:
        report['outcome'] = 'already-recovery'
    if not args.keep_pcfs and pcfs.stop_keeper():
        report['pcfs_keeper'] = 'stopped'
    if registered and not args.keep_pcfs:
        pcfs.Server(registered['ip']).stop()
        report['pcfs_server'] = f"stopped (PID {registered['pid']})"
    elif registered:
        report['pcfs_server'] = 'kept running'
    output(report)
    return 0


def pcfs_status(args):
    registered = pcfs.Server.registered()
    report = {'server': registered or 'not running', 'keeper': pcfs.keeper_running() or 'not running'}
    if registered:
        report['tcp'] = pcfs.tcp_state(registered['pid'], registered['ip'])
    if (config.HOME / 'serial.json').exists():
        try:
            boot = pcfs.current_boot(serial.rpc({'op': 'log', 'cursor': 0})['text'])
            report['console_boot'] = {'state': boot.state, 'detail': boot.detail, 'tcp_init': boot.init}
        except (OSError, RuntimeError, ValueError) as exc:
            report['console_boot'] = f'unavailable: {exc}'
    output(report)
    return 0 if registered and report.get('tcp') == 'ESTABLISHED' else 1


def keep_loop(ip, root):
    """`catr pcfs keep` in the foreground: reconnect the PCFS server after every Cafe restart."""
    ensure_service()
    server = pcfs.Server(ip, root)
    server.check_conflicts()
    pcfs.KEEP_FILE.parent.mkdir(parents=True, exist_ok=True)
    pcfs.KEEP_FILE.write_text(json.dumps({**pcfs.own_record(), 'ip': ip, 'root': str(server.root),
                                          'started': time.strftime('%Y-%m-%dT%H:%M:%S')}), encoding='utf-8')
    keeper = pcfs.Keeper(server)
    cursor, shown, paused = serial.rpc({'op': 'status'})['cursor'], 0, None

    def note(text):
        print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)
    note(f'keeping PCFS for {ip} (root {server.root}); following serial from cursor {cursor}')
    try:
        while True:
            try:
                result = serial.rpc({'op': 'log', 'cursor': cursor})
            except (OSError, RuntimeError, ValueError) as exc:
                note(f'serial service unavailable ({exc}); retrying in 2 s')
                time.sleep(2)
                try:
                    ensure_service()
                except (OSError, RuntimeError, ValueError):
                    pass
                continue
            if result['gap']:
                note('serial history gap: some lines were missed')
            cursor = result['cursor']
            holder = pcfs.lease_holder()
            if holder != paused:
                note(f'paused: catr command PID {holder} drives PCFS' if holder else 'resumed')
                paused = holder
            boots = keeper.boot.boots
            if holder:
                keeper.boot.feed(result['text'])        # track the boot, leave the server alone
                keeper._seen_boots = keeper.boot.boots
            else:
                keeper.feed(result['text'])
                keeper.tick()
            if keeper.boot.boots != boots:
                note(f'console boot {keeper.boot.boots} seen')
            for line in keeper.events[shown:]:
                note(f'[pcfs] {line}')
            shown = len(keeper.events)
            if keeper.boot.done and getattr(keeper, '_reported', None) != keeper.boot.boots:
                keeper._reported = keeper.boot.boots
                note(f'boot {keeper.boot.boots}: {keeper.boot.state}' + (f' ({keeper.boot.detail})' if keeper.boot.detail else ''))
            time.sleep(.1)
    finally:
        state = pcfs._load(pcfs.KEEP_FILE)
        if state and state.get('pid') == os.getpid():
            pcfs.KEEP_FILE.unlink(missing_ok=True)


def pcfs_keep(args):
    running = pcfs.keeper_running()
    if args.stop:
        output({'outcome': 'stopped' if pcfs.stop_keeper() else 'not-running',
                'pcfs_server': 'left as it is (catr pcfs down stops it)'})
        return 0
    root = args.root
    if running:
        if not pcfs.same_folder(running.get('root', ''), root):
            raise RuntimeError(f"the PCFS keeper (PID {running['pid']}) serves {running.get('root')}, not {root}; "
                               'catr pcfs keep --stop first')
        output({'outcome': 'already-running', 'keeper': running})
        return 0
    ip = console_ip(args)
    if not args.background:
        return keep_loop(ip, root)
    logdir = config.HOME / 'pcfs'
    logdir.mkdir(parents=True, exist_ok=True)
    pcfs.prune_logs_once()
    log = logdir / time.strftime('keep-%Y%m%d-%H%M%S.log')
    ensure_service()
    with log.open('ab') as out:
        child = subprocess.Popen([sys.executable, '-I', str(config.ROOT / 'catr.py'), 'pcfs', 'keep', '--ip', ip,
                                  '--root', root], stdout=out, stderr=out,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                 | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    for _ in range(50):
        if child.poll() is not None:
            raise RuntimeError(f'PCFS keeper exited at start; see {log}')
        state = pcfs.keeper_running()
        if state and state['pid'] == child.pid:
            output({'outcome': 'started', 'keeper': state, 'log': str(log),
                    'next': 'it reconnects after every restart; catr pcfs keep --stop ends it'})
            return 0
        time.sleep(.1)
    raise RuntimeError(f'PCFS keeper start unconfirmed; see {log}')


def pcfs_root(args):
    """Show or change the one PCFS host folder catr serves."""
    if args.path:
        path = pcfs.set_host_root(args.path)
        output({'outcome': 'saved', 'folder': str(path),
                'next': 'pcfs up, pcfs keep, run and Spark captures all use this folder now'})
    else:
        path = pcfs.host_root()
        output({'folder': str(path), 'exists': path.is_dir(),
                'source': 'catr config (pcfs_root)' if config.load().get('pcfs_root') else 'built-in default'})
    return 0


def pcfs_down(args):
    keeper = pcfs.stop_keeper()
    registered = pcfs.Server.registered()
    if not registered:
        output({'outcome': 'not-running', 'keeper': 'stopped' if keeper else 'not running'})
        return 0
    title = ensure_service()['title']
    if title.get('title') != RECOVERY:
        confirm(f'stop the PCFS server while {describe(title)} is in front (a title reading from PCFS '
                'hangs the console when the link drops)', args.yes)
    pcfs.Server(registered['ip']).stop()
    output({'outcome': 'stopped', 'pid': registered['pid'], 'keeper': 'stopped' if keeper else 'not running'})
    return 0


def spark_list(args):
    from . import spark as sp
    output({'folder': str(sp.SAVE_DIR), 'captures': [dict(item, modified=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(item['modified'])))
                                                      for item in sp.listing()]})
    return 0


def spark_wait(args):
    """Wait for capture requests on serial, then for each file to finish; copy it out with a hash."""
    import shutil
    from . import spark as sp
    ensure_service()
    if not pcfs.Server.registered():
        print('catr: warning: catr has no PCFS server running; captures need PCFS (catr run <spark title>)', file=sys.stderr)
    cursor = serial.rpc({'op': 'status'})['cursor']
    watch, done, results = sp.CaptureWatch(), 0, []
    print(f'[catr: waiting for {args.count} capture(s); press A in the SPARK title]', file=sys.stderr, flush=True)
    deadline = time.monotonic() + args.timeout
    seen, sizes = 0, {}
    while done < args.count:
        result = serial.rpc({'op': 'log', 'cursor': cursor})
        if result['error'] or result['gap']:
            raise OSError(f"serial {'error: ' + result['error'] if result['error'] else 'history gap'}")
        cursor = result['cursor']
        watch.feed(result['text'])
        if watch.rejected:
            raise RuntimeError('gx2 rejected the capture: "Capture not supported in production mode"')
        for capture in watch.captures[seen:]:
            print(f"  capture request {capture['request']} at frame {capture['frame']} -> {capture['path']}", file=sys.stderr, flush=True)
            deadline = time.monotonic() + args.timeout
        seen = len(watch.captures)
        for capture in watch.captures[done:]:
            if capture['resumed_frame'] is None:
                break
            path = Path(capture['path'])
            size = path.stat().st_size if path.exists() else -1
            last = sizes.get(capture['request'])
            sizes[capture['request']] = (size, last[1] if last and last[0] == size else time.monotonic())
            if size <= 0 or time.monotonic() - sizes[capture['request']][1] < 2:
                break           # resumed; wait until the file stops growing
            item = dict(sp.describe(path), request=capture['request'], frame=capture['frame'])
            item['modified'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(item['modified']))
            if not item['pm4']:
                item['warning'] = 'file does not start with PM4; capture may be incomplete'
            if not args.no_copy:
                dest_dir = Path(args.out) if args.out else config.WORKSPACE / 'captures'
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{path.name}"
                shutil.copy2(path, dest)
                item['copy'] = str(dest)
                item['sha256'] = hashlib.sha256(dest.read_bytes()).hexdigest()
            results.append(item)
            print(f"  capture {capture['request']} complete: {item['bytes']:,} bytes", file=sys.stderr, flush=True)
            done += 1
        if done < args.count and time.monotonic() > deadline:
            output({'captures': results, 'result': 'timed out'})
            return 2
        time.sleep(.25)
    output({'captures': results})
    return 0 if all(item['pm4'] for item in results) else 1


def spark_open(args):
    from . import spark as sp
    spark_exe = sp.spark_exe()
    target = args.file or (sp.listing() or [{}])[-1].get('path')
    if not target:
        raise FileNotFoundError(f'no .4mp capture in {sp.SAVE_DIR}')
    subprocess.Popen([str(spark_exe), str(target)], cwd=str(spark_exe.parent))
    output({'opened': str(target), 'with': str(spark_exe)})
    return 0


def progress_line(path, size):
    print(f'  {path}  {size:,} bytes', file=sys.stderr, flush=True)


def crashlogs(args):
    """Copy /vol/system/logs (COS fatal-error ring) to the workspace (logs/catr-slc-logs-<time>); reads only."""
    from . import backup as bk
    stamp = time.strftime('%Y%m%d-%H%M%S')
    folder = Path(args.out) if args.out else bk.LOGS_ROOT / f'catr-slc-logs-{stamp}'
    if folder.exists() and any(folder.iterdir()):
        raise FileExistsError(f'{folder} exists and is not empty')
    previous = bk.latest('catr-slc-logs', bk.LOGS_ROOT)
    with Session(console_ip(args), timeout=10) as session:
        files = bk.copy_out(session, [bk.CRASH_LOGS], folder, None if args.quiet else progress_line)
    manifest = bk.write_manifest(folder, 'crashlogs', [bk.CRASH_LOGS], files)
    report = {'folder': str(folder), 'files': len(files), 'bytes': manifest['total_bytes']}
    if previous:
        diff = bk.changes(bk.load(previous), manifest)
        report['since'] = {'previous': str(previous), **{k: [p.rsplit('/', 1)[1] for p in v] for k, v in diff.items()}}
    output(report)
    return 0


def backup(args):
    """Copy a named set (or --path roots) off the console with a manifest of SHA-1s; reads only."""
    from . import backup as bk
    if args.set in bk.SETS:
        roots, title = bk.SETS[args.set]['roots'], bk.SETS[args.set].get('title')
    elif args.path:
        roots, title = [remote_path(p) for p in args.path], None
    else:
        raise ValueError(f'unknown set {args.set!r}: one of {", ".join(bk.SETS)}, or a name with --path /vol/...')
    if args.path and args.set in bk.SETS:
        raise ValueError(f'{args.set} is a built-in set; --path is for your own set names')
    folder = Path(args.out) if args.out else bk.BACKUP_ROOT / f"catr-{args.set}-{time.strftime('%Y%m%d-%H%M%S')}"
    if folder.exists() and any(folder.iterdir()):
        raise FileExistsError(f'{folder} exists and is not empty')
    extra = {}
    with Session(console_ip(args), timeout=10) as session:
        if title:
            record = by_title(session.title_list()).get(title)
            if not record:
                raise ValueError(f'{title} is not installed; nothing to back up')
            extra['title'] = {'id': title, 'version': record['version'], 'path': record['path']}
        files = bk.copy_out(session, roots, folder, None if args.quiet else progress_line)
    manifest = bk.write_manifest(folder, args.set, roots, files, **extra)
    problems = bk.verify_local(folder, manifest)
    if problems:
        raise OSError(f'backup did not verify: {problems[0]}')
    output({'folder': str(folder), 'set': args.set, 'files': len(files), 'bytes': manifest['total_bytes'],
            **({'title': extra['title']} if extra else {}), 'verified': 'local copy matches the manifest'})
    return 0


def console_compare(session, manifest, mapping):
    """Per file: 'same', 'differs' or 'missing' on the console at its mapped path."""
    state = {}
    for item in manifest['files']:
        target = mapping[item['path']]
        try:
            data = session.read_file(target, limit=item['bytes'] + 1)
        except OSError:
            state[target] = 'missing'
            continue
        state[target] = 'same' if hashlib.sha1(data).hexdigest() == item['sha1'] and len(data) == item['bytes'] else 'differs'
    return state


def restore_files(session, folder, manifest, mapping, pending):
    """Write the pending backed-up files and read each one back against its SHA-1."""
    from . import backup as bk
    written = []
    by_target = {mapping[f['path']]: f for f in manifest['files']}
    made = set()
    for target in pending:
        item = by_target[target]
        data = bk.local_path(folder, item['path']).read_bytes()
        parts = target.rsplit('/', 1)[0].split('/')
        for i in range(4, len(parts) + 1):
            folder_path = '/'.join(parts[:i])
            if folder_path not in made:
                session.makedir(folder_path)
                made.add(folder_path)
        session.write_file(target, data)
        back = session.read_file(target, limit=len(data) + 1)
        if hashlib.sha1(back).hexdigest() != item['sha1']:
            raise OSError(f'read-back of {target} does not match the backup SHA-1; stopped '
                          f'({len(written)} files written before it)')
        written.append(target)
        print(f'  {target}  {len(data):,} bytes, SHA-1 matches', file=sys.stderr, flush=True)
    return written


def restore(args):
    """Put a backup back (or, with --to, into another folder), after checking its hashes; asks first."""
    from . import backup as bk
    from .safety import write_check
    folder = Path(args.folder)
    manifest = bk.load(folder)
    problems = bk.verify_local(folder, manifest)
    if problems:
        raise ValueError(f'backup is damaged, nothing written: {problems[0]} ({len(problems)} problems)')
    to = remote_path(args.to) if args.to else None
    mapping = bk.targets(manifest, to)
    in_place_recovery = False
    for target in ([] if args.check else mapping.values()):     # --check only reads, so any path is fine
        if args.recovery_menu and to is None:
            if manifest.get('set') != 'recovery-menu' or not bk.recovery_restore_path(target):
                raise ValueError('--recovery-menu restores only a recovery-menu backup into the Recovery Menu\'s code/ and meta/')
            in_place_recovery = True
        else:
            write_check(target, args.unsafe)
    ip = console_ip(args)
    with Session(ip, timeout=30) as session:
        state = console_compare(session, manifest, mapping)
    pending = [t for t, s in state.items() if s != 'same']
    report = {'backup': str(folder), 'set': manifest.get('set'), 'files': len(state),
              'same': len(state) - len(pending), 'differs': sum(s == 'differs' for s in state.values()),
              'missing': sum(s == 'missing' for s in state.values()), 'target': to or 'original paths'}
    if args.check or not pending:
        report['result'] = 'console matches the backup byte for byte' if not pending else 'check only; nothing written'
        if pending:
            report['pending'] = pending
        output(report)
        return 0 if not pending else 1
    if in_place_recovery:
        front = ensure_service()['title']
        if front.get('title') == RECOVERY or front.get('state') != 'observed-title':
            raise ValueError(f'console now: {describe(front)}; start another title first (the Recovery Menu '
                             'must not be in front while its files are rewritten)')
        confirm_typed(f'overwrite {len(pending)} Recovery Menu files from {folder}', 'restore recovery-menu', args.confirm)
    else:
        confirm(f'write {len(pending)} files to {to or "their original paths"} ({report["same"]} already match)', args.yes)
    audit('restore', to or 'original', backup=str(folder), files=len(pending), set=manifest.get('set'))
    with Session(ip, timeout=30) as session:
        report['written'] = len(restore_files(session, folder, manifest, mapping, pending))
        after = console_compare(session, manifest, mapping)
    report['result'] = ('restored; console matches the backup byte for byte' if all(s == 'same' for s in after.values())
                        else 'written, but the final compare differs')
    output(report)
    return 0 if all(s == 'same' for s in after.values()) else 1


def confirm_typed(question, phrase, given):
    """A confirmation the user types out (repair needs one); scripts pass --confirm PHRASE."""
    if given is not None:
        if given != phrase:
            raise ValueError(f'--confirm must be exactly "{phrase}"; nothing was sent')
        return
    if not sys.stdin.isatty():
        raise ValueError(f'{question}: needs a typed confirmation; pass --confirm "{phrase}" when not at a terminal')
    try:
        answer = input(f'{question}\nType "{phrase}" to continue: ')
    except EOFError:
        raise ValueError(f'{question}: no answer (stdin closed); nothing was sent') from None
    if answer.strip() != phrase:
        raise ValueError('Cancelled; nothing was sent')


RECOVERY_STARTING = re.compile(r'CAT Recovery Menu v([0-9.]+) starting')


def repair_recovery_menu(args):
    """Reinstall the Recovery Menu from an install image (the only command that installs over it).

    Same or newer version only; backs up code/ and meta/ first; runs only while another title is
    in front; typed confirmation; default title must stay the Recovery Menu. Afterwards checks the
    version, slowlaunches the menu and waits for its "CAT Recovery Menu vX starting" line.
    """
    from . import backup as bk
    from .safety import STAGING
    package = Path(args.package)
    if not (package / 'title.tmd').is_file():
        raise FileNotFoundError(f'{package} is not an install image folder (no title.tmd)')
    tid, version, os_version = tmd_identity((package / 'title.tmd').read_bytes())
    if tid != RECOVERY:
        raise ValueError(f'{package} is {tid}, not the Recovery Menu ({RECOVERY})')
    ip = console_ip(args)
    with Session(ip, timeout=10) as session:
        current = by_title(session.title_list()).get(RECOVERY)
        default = session.default_title()
        mlc = session.space('/vol/storage_mlc01')
    if current and version < current['version']:
        raise ValueError(f'package is v{version}, older than the installed v{current["version"]}; refused')
    if default != RECOVERY:
        raise ValueError(f'default title is {default}, not the Recovery Menu; run catr maintenance end first')
    front = ensure_service()['title']
    if front.get('state') != 'observed-title' or front.get('title') == RECOVERY:
        raise ValueError(f'console now: {describe(front)}. The repair runs only while another title is in front; '
                         'start one first: catr launch TITLE-ID (catr titles lists them)')
    size = sum(p.stat().st_size for p in package.iterdir() if p.is_file())
    if size * 3 > mlc['free']:
        raise OSError(f'not enough MLC space: image {size:,} bytes, free {mlc["free"]:,}')
    report = {'package': str(package), 'version': version, 'os_version': os_version,
              'installed_before': f"v{current['version']}" if current else None, 'front_title': describe(front)}
    confirm_typed(f'reinstall the Recovery Menu v{version} over '
                  f"{'v' + str(current['version']) if current else 'nothing installed'} "
                  f'(upload {size:,} bytes, back up code/ and meta/ first)', 'repair recovery-menu', args.confirm)
    # 1. backup
    stamp = time.strftime('%Y%m%d-%H%M%S')
    saved = bk.BACKUP_ROOT / f'catr-recovery-menu-{stamp}-before-repair'
    with Session(ip, timeout=10) as session:
        files = bk.copy_out(session, bk.SETS['recovery-menu']['roots'], saved)
    manifest = bk.write_manifest(saved, 'recovery-menu', bk.SETS['recovery-menu']['roots'], files,
                                 title={'id': RECOVERY, 'version': current['version'] if current else None,
                                        'path': bk.RECOVERY_DIR}, reason='before repair')
    if bk.verify_local(saved, manifest):
        raise OSError(f'backup {saved} did not verify; nothing installed')
    report['backup'] = str(saved)
    # 2. upload and install
    console_path = f'{STAGING}/repair/{package.name}'
    audit('repair', RECOVERY, version=version, package=str(package), backup=str(saved))
    started = time.monotonic()
    try:
        put(argparse.Namespace(source=str(package), dest=console_path, recursive=True, force=True, unsafe=False,
                               yes=True, ip=ip))
        report['upload_s'] = round(time.monotonic() - started, 1)
        with Session(ip, timeout=900) as session:
            t0 = time.monotonic()
            session.install(console_path)
            report['install_s'] = round(time.monotonic() - t0, 1)
            after = by_title(session.title_list()).get(RECOVERY)
            default_after = session.default_title()
    finally:
        try:
            rm(argparse.Namespace(path=f'{STAGING}/repair', recursive=True, unsafe=False, yes=True, ip=ip))
        except OSError as exc:        # nothing uploaded yet, or the console went away
            report['staging'] = f'not removed: {exc}'
    report['installed_after'] = f"v{after['version']}" if after else None
    report['default_title'] = default_after
    if not after or after['version'] != version:
        report['outcome'] = 'install not confirmed'
        report['next'] = (f'catr restore "{saved}" --recovery-menu (another title in front), or '
                          'catr maintenance sct and repair from SCT')
        output(report)
        return 1
    # 3. start it and wait for its own line
    sent = serial.rpc({'op': 'act', 'action': 'slowlaunch', 'tid': RECOVERY}, timeout=20)
    watch = Watch('launch', RECOVERY)
    report['launch'] = follow(sent, watch, 90)
    seen = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not seen:
        text = serial.rpc({'op': 'log', 'cursor': sent['begin']})['text']
        match = RECOVERY_STARTING.search(text)
        seen = match.group(0) if match else None
        if not seen:
            time.sleep(1)
    report['started_line'] = seen
    report['outcome'] = 'repaired' if report['launch'] == 'launched' and seen else 'installed, start not confirmed'
    if report['outcome'] != 'repaired':
        report['next'] = (f'check catr log; if the menu does not start: catr restore "{saved}" --recovery-menu '
                          'with another title in front, or catr maintenance sct and repair from SCT')
    output(report)
    return 0 if report['outcome'] == 'repaired' else 1


def syslog(args):
    """IOSU syslog from MCP memory over wupserver (no serial needed); reads only, every address checked."""
    from . import syslog as sl
    with Session(console_ip(args), timeout=10) as session:
        result = sl.fetch(session.client.read, previous=args.previous)
    text = result.pop('text')
    if args.out:
        Path(args.out).write_bytes(text)
    # COS lines end in a bare CR, IOSU lines in LF (as on the serial line).
    lines = text.decode('utf-8', 'replace').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    lines = [line for line in lines if line.strip()]
    if args.grep:
        lines = [line for line in lines if args.grep in line]
    if args.out:
        output(dict(result, out=args.out, bytes=len(text)))
    else:
        print('\n'.join(lines[-args.tail:] if args.tail else lines))
        print(f"[syslog: {result['boot']} boot, entry {result['entry']}, {len(text):,} bytes"
              f"{', wrapped (oldest lines lost)' if result['wrapped'] else ''}]", file=sys.stderr)
    return 0


DEBUG_DIR = config.LOGS / 'debug'


def shell(command):
    """Send one COS command through the serial service; returns the text, saved under logs/debug."""
    _, category, wire = user_command(command)
    ensure_service()
    if category != 'read-only':
        audit('debug', wire, category=category)
    result = serial.rpc({'op': 'cos', 'command': wire}, timeout=60)
    if not result['proof'] or result['error'] or result['gap']:
        raise OSError(f"{wire}: no clean reply (proof {result['proof']}, error {result['error']}, gap {result['gap']})")
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    name = time.strftime('%Y%m%d-%H%M%S-') + re.sub(r'[^A-Za-z0-9]+', '_', wire[4:])[:40] + '.txt'
    (DEBUG_DIR / name).write_text(normalize(result['text']), encoding='utf-8')
    return result['text']


def debug(args):
    """Thin wrappers over the COS shell debug commands, with parsed output."""
    from . import debug as dbg
    what = args.debug_command
    if what in ('mem', 'disasm', 'sym'):
        address = dbg.parse_address(args.address)
        words = getattr(args, 'words', dbg.MEMDUMP_MIN_WORDS)
        if not 1 <= words <= 1024:
            raise ValueError('words must be 1-1024')
        size = {'mem': max(words, dbg.MEMDUMP_MIN_WORDS) * 4, 'disasm': dbg.DISASM_SPAN, 'sym': 4}[what]
        modules = dbg.parse_modules(shell('cos modules'))
        if not modules:
            raise OSError('cos modules listed nothing; not sending an address command')
        module, kind = dbg.check_range(address, size, modules)
        if what == 'sym':
            text = shell(f'cos getsym {address:#010x}')
            output(dict(dbg.parse_getsym(text) or {'raw': dbg.lines(text)}, module=module, segment=kind))
            return 0
        if what == 'disasm' and kind != 'code':
            raise ValueError(f'{address:#010x} is in {module} data, not code; use catr debug mem')
        text = shell(f'cos memdump {address:#010x} {words}' if what == 'mem' else f'cos disasm {address:#010x}')
        print(f'[{module} {kind}]')
        print('\n'.join(dbg.dump_lines(text)))
        return 0
    if what == 'crashdump':
        confirm('force a crashdump (cos crashdump); it may stop the title in front', args.yes)
        print('\n'.join(dbg.lines(shell('cos crashdump'))))
        return 0
    command = {'threads': 'cos threads', 'stacks': 'cos coretrace' if getattr(args, 'cores', False) else 'cos ttrace',
               'heaps': 'cos heaps', 'cpu': 'cos cpuusage', 'modules': 'cos modules'}[what]
    text = shell(command)
    if what == 'threads' and not getattr(args, 'raw', False):
        threads = dbg.parse_threads(text)
        if getattr(args, 'json', False):
            output(threads)
            return 0
        for t in threads:
            wait = f"  waits on {t['waiting_on']}" if t['waiting_on'] else ''
            print(f"{t['state']}  core {dbg.cores(t['affinity']):<3} pri {t['priority']:>2}  {t['pc']}  "
                  f"{t['symbol']:<40} {t['name']}{wait}")
        print(f'{len(threads)} threads', file=sys.stderr)
        return 0
    if what == 'modules' and not getattr(args, 'raw', False):
        modules = dbg.parse_modules(text)
        for m in modules:
            print(f"{m['name']:<24} code {m['code'][0]:#010x}-{m['code'][1]:#010x}  data {m['data'][0]:#010x}-{m['data'][1]:#010x}")
        return 0
    if what == 'cpu':
        output(dbg.parse_cpu(text) or {'raw': dbg.lines(text)})
        return 0
    if what == 'heaps':
        print('\n'.join(dbg.heap_lines(text)))
        return 0
    print('\n'.join(dbg.lines(text)))
    return 0


def gdb(args):
    """Read-only GDB remote server; every shell command goes through shell() (saved, audited)."""
    from . import gdbserver
    ensure_service()
    gdbserver.serve(lambda: gdbserver.Target(shell), host=args.host, port=args.port, once=args.once,
                    log=lambda text: print(text, file=sys.stderr, flush=True))
    return 0


def hio(args):
    """HostBridge stand-in for HIO-over-TCP titles (SparkCube v0.6+): 6003 registry + channel ports."""
    from . import hiobridge, spark as sp
    ip = console_ip(args)
    if args.list:
        channels = hiobridge.list_channels(ip)
        if not channels:
            print('no HIO channels open')
        for name, connected in channels:
            print(f"{name}{'  (PC connected)' if connected else ''}")
        return 0
    if not pcfs.Server.registered():
        print('catr: warning: catr has no PCFS server running; captures are written over PCFS', file=sys.stderr)
    print('[catr hio: Ctrl+C stops; start SPARK (catr spark open) and use Capture]', file=sys.stderr, flush=True)
    hiobridge.serve(ip, sp.SAVE_DIR, rewrite=not args.no_rewrite,
                    log=lambda text: print(text, flush=True))
    return 0


def config_get(args):
    from . import sysconfig
    path = sysconfig.FILES[args.file]
    with Session(console_ip(args), timeout=10) as session:
        data = session.read_file(path)
    _, values = sysconfig.leaves(data)
    if args.key:
        if args.key not in values:
            raise ValueError(f'{args.file}:{args.key} does not exist')
        values = {args.key: values[args.key]}
    if args.json:
        output({'file': path, 'sha256': hashlib.sha256(data).hexdigest(), 'values': values})
    else:
        width = max(map(len, values), default=0)
        for key, leaf in values.items():
            mark = '' if (args.file, key) in sysconfig.ALLOWED else '  (--unsafe)' if not any(
                f == args.file and k in ('*', key) for f, k in sysconfig.REFUSED) else '  (refused)'
            print(f'{key:<{width}}  {leaf["value"]:<20} {leaf["type"]}{mark}')
    return 0


def config_set(args):
    """One leaf: plan + check, back up off the console, write, read back; restore on mismatch."""
    from . import sysconfig
    path = sysconfig.FILES[args.file]
    with Session(console_ip(args), timeout=10) as session:
        original = session.read_file(path)
        new_data, old, new = sysconfig.plan_set(original, args.file, args.key, args.value, args.unsafe)
        report = {'file': path, 'key': args.key, 'old': old, 'new': new,
                  'sha256_before': hashlib.sha256(original).hexdigest(),
                  'sha256_after': hashlib.sha256(new_data).hexdigest()}
        if old == new:
            output(dict(report, result='unchanged (already set)'))
            return 0
        if args.dry_run:
            output(dict(report, result='dry run; nothing written'))
            return 0
        confirm(f'Set {args.file}:{args.key} from {old} to {new} on the console?', args.yes)
        backup = config.WORKSPACE / 'backups' / time.strftime('catr-config-%Y%m%d-%H%M%S') / path.lstrip('/')
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(original)
        if backup.read_bytes() != original:
            raise OSError(f'backup {backup} did not read back; nothing written')
        report['backup'] = str(backup)
        audit('config-set', f'{path} {args.key}', old=old, new=new, backup=str(backup))
        session.write_file(path, new_data)
        readback = session.read_file(path)
        if readback != new_data:
            session.write_file(path, original)
            restored = session.read_file(path) == original
            audit('config-restore', path, restored=restored)
            raise OSError(f'read-back mismatch after writing {path}; original '
                          + ('restored and verified' if restored else f'NOT verified - restore from {backup}'))
        report['result'] = 'written and read back; takes effect on the next boot'
    output(report)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='catr', description='Command-line toolkit for the CAT-R. Full guide: catr/README.md')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    sub = commands.add_parser('find', help='discover and verify wupserver; save selected endpoint')
    sub.add_argument('--ip', help="the console's IP address; without it, catr searches this PC's own networks")
    sub.add_argument('--network', help='search this network instead, e.g. 192.168.1.0/24')
    sub.add_argument('--port', help='USB serial port to save, e.g. COM3 (default: the saved one, else the only one found)')
    sub = commands.add_parser('info', help='read console identity, title metadata and local artifact hashes')
    sub.add_argument('--ip')
    sub.add_argument('--iosu', action='store_true', help='hash IOSU fw.img and kernel.img (about 80 s); records a baseline the first time')
    sub.add_argument('--iosu-file', action='append', default=[], help='extra console path to hash with --iosu; repeatable')
    sub = commands.add_parser('serial', help='own the serial port and provide loopback service')
    sub.add_argument('--port')
    sub.add_argument('--background', action='store_true')
    sub.add_argument('--status', action='store_true')
    sub.add_argument('--stop', action='store_true', help='stop only the host serial service; release COM port')
    sub = commands.add_parser('cos', help='send one COS shell command (any; only read-only ones are ever resent)')
    sub.add_argument('words', nargs=argparse.REMAINDER)
    sub = commands.add_parser('ls', help='list a console directory (read-only)')
    sub.add_argument('path')
    sub.add_argument('--json', action='store_true')
    sub.add_argument('--ip')
    sub = commands.add_parser('stat', help='show a console path\'s FSA stat (read-only)')
    sub.add_argument('path')
    sub.add_argument('--ip')
    sub = commands.add_parser('get', help='copy a console file or directory to the PC (reads the console only)')
    sub.add_argument('path')
    sub.add_argument('dest', nargs='?')
    sub.add_argument('-r', '--recursive', action='store_true')
    sub.add_argument('--force', action='store_true', help='overwrite existing local files')
    sub.add_argument('--limit', type=int, default=256 * 1024 * 1024, help='per-file byte limit')
    sub.add_argument('--ip')
    sub = commands.add_parser('put', help='copy a PC file or folder to the console (read back and compared)')
    sub.add_argument('source')
    sub.add_argument('dest', help='console path; catr staging (/vol/storage_mlc01/usr/tmp/catr/...) unless --unsafe')
    sub.add_argument('-r', '--recursive', action='store_true')
    sub.add_argument('--force', action='store_true', help='overwrite existing console files')
    sub.add_argument('--unsafe', action='store_true', help='allow paths outside catr staging (never system or title folders)')
    sub.add_argument('--yes', action='store_true')
    sub = commands.add_parser('rm', help='delete a console file or (-r) folder; never system or title folders')
    sub.add_argument('path')
    sub.add_argument('-r', '--recursive', action='store_true')
    sub.add_argument('--unsafe', action='store_true')
    sub.add_argument('--yes', action='store_true')
    sub = commands.add_parser('mkdir', help='create a console folder')
    sub.add_argument('path')
    sub.add_argument('--unsafe', action='store_true')
    sub = commands.add_parser('reboot', help='restart the console (PM_Restart), wait for the default title and wupserver')
    sub.add_argument('--yes', action='store_true')
    sub = commands.add_parser('install', help='install an image folder (PC folder: uploaded to staging first) onto MLC')
    sub.add_argument('source', help='PC install-image folder, a .wumad (converted with makecfmaster), or a /vol/... console path')
    sub.add_argument('--keep', action='store_true', help='keep the uploaded staging copy')
    sub.add_argument('--size-only', action='store_true',
                     help='check only each uploaded file\'s size (title.tmd is still read back): about twice as '
                          'fast, but a damaged content file installs unnoticed (MCP does not check content hashes)')
    sub.add_argument('--yes', action='store_true')
    sub = commands.add_parser('uninstall', help='remove an installed game, update or DLC (never SCT, Recovery Menu, system)')
    sub.add_argument('title')
    sub.add_argument('--yes', action='store_true')
    sub = commands.add_parser('maintenance', help='switch the boot title for maintenance (sct, menu, tiramisu); end restores the Recovery Menu')
    sub.add_argument('target', help='sct | menu | tiramisu | <tid from maintenance_targets> | end | status')
    sub.add_argument('--no-reboot', action='store_true')
    sub.add_argument('--yes', action='store_true')
    commands.add_parser('default-title', help='show the boot (default) title and any recorded maintenance switch')
    sub = commands.add_parser('titles', help='list installed titles via MCP (read-only)')
    sub.add_argument('--type', help='title id prefix, e.g. 00050000 (games) or 00050010 (system apps)')
    sub.add_argument('--fsa', action='store_true', help='walk the MLC title folders instead of asking MCP (slow)')
    sub.add_argument('--names', action='store_true', help=argparse.SUPPRESS)   # names are the default now
    sub.add_argument('--no-names', action='store_true', help='skip reading names (meta.xml); names are cached after the first read')
    sub.add_argument('--json', action='store_true')
    sub.add_argument('--ip')
    sub = commands.add_parser('icons', help='copy title icons (meta/iconTex.tga) to %%APPDATA%%/catr/icons; JSON lines (reads only)')
    sub.add_argument('titles', nargs='*', help='title IDs (default: all installed)')
    sub.add_argument('--refresh', action='store_true', help='read again even if cached')
    sub.add_argument('--ip')
    sub = commands.add_parser('launch', help='start a title over serial (sent once, then watched)')
    sub.add_argument('tid', help="title ID, e.g. 0005000010c0be00, '0x00050000 0x10C0BE00' or recovery")
    sub.add_argument('args', nargs='*', help='arguments for the title (cos launchex; Recovery Menu or ROOT in front)')
    sub.add_argument('--slow', action='store_true', help='always use cos slowlaunch (Cafe restart)')
    sub.add_argument('--force', action='store_true', help='send even if the title already runs')
    sub.add_argument('--yes', action='store_true', help='skip the confirmation prompt')
    sub.add_argument('--unchecked', action='store_true', help='skip the installed-title check (wupserver down)')
    sub = commands.add_parser('restart', help='kill and restart the title in front (cos killrestart)')
    sub.add_argument('--yes', action='store_true')
    sub = commands.add_parser('kill', help='end the master title over serial; leaves the console in ROOT')
    sub.add_argument('--force', action='store_true', help='send even if the console already waits in ROOT')
    sub.add_argument('--yes', action='store_true', help='skip the confirmation prompt')
    sub.add_argument('--debugger-halted-ok', action='store_true',
                     help='kill even while the GDB stub holds the title (that can hang the console)')
    sub = commands.add_parser('run', help='stage a build (or use a staged/installed title), mount PCFS, launch, follow the log')
    sub.add_argument('target', help='unpacked build folder (code/ content/ meta/), or a title ID already staged')
    sub.add_argument('--title-id', help='stage under this ID instead of the PCFS-only ...F0 one')
    sub.add_argument('--keep-title-id', action='store_true', help='stage under the build\'s own ID')
    sub.add_argument('--include-debug', action='store_true', help='also copy .elf/.map/.dla/.dnm')
    sub.add_argument('--installed', action='store_true', help='the title is installed on the console; PCFS only serves its writes')
    sub.add_argument('--root', help='must be the saved PCFS folder (catr pcfs root shows and changes it)')
    sub.add_argument('--tries', type=int, default=3)
    sub.add_argument('--trace', action='store_true')
    sub.add_argument('--no-follow', action='store_true', help='return after the launch instead of following the log')
    sub.add_argument('--until', metavar='TEXT', help='stop following once a log line contains TEXT (exit 0; 2 if --wait runs out)')
    sub.add_argument('--wait', type=float, default=120, help='seconds --until waits (default 120)')
    sub.add_argument('--yes', action='store_true')
    sub.add_argument('--ip')
    sub = commands.add_parser('stop', help='return to the Recovery Menu and stop the PCFS server')
    sub.add_argument('--keep-pcfs', action='store_true', help='keep the server and reconnect it after the restart')
    sub.add_argument('--yes', action='store_true')
    sub = commands.add_parser('pcfs', help='PCFS host server: up (mount with reconnects), status, down')
    pcfs_commands = sub.add_subparsers(dest='pcfs_command', required=True)
    sub = pcfs_commands.add_parser('up', help='restart into the Recovery Menu until PCFS mounts')
    sub.add_argument('--root', help='must be the saved PCFS folder (catr pcfs root shows and changes it)')
    sub.add_argument('--tries', type=int, default=3, help='restarts to try (about half of boots fail TCP init)')
    sub.add_argument('--trace', action='store_true', help='PCFS_TRACE request log in the server log')
    sub.add_argument('--force', action='store_true', help='restart even if PCFS is already up')
    sub.add_argument('--yes', action='store_true')
    sub.add_argument('--ip')
    pcfs_commands.add_parser('status', help='host server, its connection, and the console boot\'s PCFS state')
    sub = pcfs_commands.add_parser('root', help='show or change the one PC folder catr serves over PCFS')
    sub.add_argument('path', nargs='?', help='new folder (refused while a server or keeper serves another one)')
    sub = pcfs_commands.add_parser('down', help='stop catr\'s PCFS server (and the background keeper)')
    sub.add_argument('--yes', action='store_true')
    sub = pcfs_commands.add_parser('keep', help='reconnect the PCFS server after every restart, also ones catr did not start')
    sub.add_argument('--background', action='store_true', help='run hidden; log under %%APPDATA%%\\catr\\pcfs')
    sub.add_argument('--stop', action='store_true', help='end the background keeper (the server is left as it is)')
    sub.add_argument('--root', help='must be the saved PCFS folder (catr pcfs root shows and changes it)')
    sub.add_argument('--ip')
    sub = commands.add_parser('config', help='console XML config: get values, set one allowlisted key (backed up, read back)')
    config_commands = sub.add_subparsers(dest='config_command', required=True)
    sub = config_commands.add_parser('get', help='show the values of a console config file')
    sub.add_argument('file', nargs='?', default='system', choices=['system', 'rtc', 'sys_prod'])
    sub.add_argument('key', nargs='?')
    sub.add_argument('--json', action='store_true')
    sub = config_commands.add_parser('set', help='change one key: check, back up, write, read back')
    sub.add_argument('key')
    sub.add_argument('value')
    sub.add_argument('--file', default='system', choices=['system', 'rtc'])
    sub.add_argument('--unsafe', action='store_true', help='allow keys outside the allowlist (never the refused ones)')
    sub.add_argument('--dry-run', action='store_true')
    sub.add_argument('--yes', action='store_true')
    sub = commands.add_parser('spark', help='SPARK frame captures over PCFS (file capture only)')
    spark_commands = sub.add_subparsers(dest='spark_command', required=True)
    spark_commands.add_parser('list', help='captures in the PCFS save folder')
    sub = spark_commands.add_parser('wait', help='wait for capture requests, then copy each finished file out with a hash')
    sub.add_argument('--count', type=int, default=1)
    sub.add_argument('--timeout', type=float, default=600, help='seconds to wait for each capture (default 600)')
    sub.add_argument('--out', help='copy folder (default: the workspace captures folder)')
    sub.add_argument('--no-copy', action='store_true')
    sub = spark_commands.add_parser('open', help='open a capture (default: newest) in SPARK')
    sub.add_argument('file', nargs='?')
    sub = commands.add_parser('crashlogs', help='copy /vol/system/logs (COS fatal errors) to the workspace logs folder (reads only)')
    sub.add_argument('--out', help='folder (default: workspace/logs/catr-slc-logs-<time>)')
    sub.add_argument('--quiet', action='store_true')
    sub.add_argument('--ip')
    sub = commands.add_parser('backup', help='copy a named set off the console with SHA-1s (reads only)')
    sub.add_argument('set', help='recovery-menu | sct | sct-code | config, or your own name with --path')
    sub.add_argument('--path', action='append', help='console folder or file for your own set; repeatable')
    sub.add_argument('--out', help='folder (default: backups/catr-<set>-<time> in the workspace)')
    sub.add_argument('--quiet', action='store_true')
    sub.add_argument('--ip')
    sub = commands.add_parser('restore', help='check a backup hashes, then write it back (or --to elsewhere); asks first')
    sub.add_argument('folder', help='backup folder (has manifest.json)')
    sub.add_argument('--to', help='write under this console folder instead of the original paths (keeps folder names)')
    sub.add_argument('--check', action='store_true', help='only compare the console with the backup; write nothing')
    sub.add_argument('--unsafe', action='store_true', help='allow targets outside catr staging (never system or title folders)')
    sub.add_argument('--recovery-menu', action='store_true', help='put a recovery-menu backup back in place (typed confirmation)')
    sub.add_argument('--confirm', help='the typed confirmation, for scripts')
    sub.add_argument('--yes', action='store_true')
    sub.add_argument('--ip')
    sub = commands.add_parser('repair', help='reinstall a protected title from an install image (recovery-menu only)')
    sub.add_argument('what', choices=['recovery-menu'])
    sub.add_argument('package', help='install image folder, e.g. titles/recovery-menu/Install/CAT-Recovery-Menu-v11_p01')
    sub.add_argument('--confirm', help='the typed confirmation, for scripts: "repair recovery-menu"')
    sub.add_argument('--ip')
    sub = commands.add_parser('syslog', help='IOSU syslog from console memory over wupserver (no serial needed)')
    sub.add_argument('--previous', action='store_true', help='the previous boot (kept across a warm restart)')
    sub.add_argument('--grep', default='')
    sub.add_argument('--tail', type=int, default=0, help='only the last N lines')
    sub.add_argument('--out', help='save the raw text to a file instead of printing it')
    sub.add_argument('--ip')
    sub = commands.add_parser('debug', help='COS shell debug commands with parsed output (saved under logs/debug)')
    debug_commands = sub.add_subparsers(dest='debug_command', required=True)
    sub = debug_commands.add_parser('threads', help='threads of the title in front: state, core, priority, PC symbol')
    sub.add_argument('--json', action='store_true')
    sub.add_argument('--raw', action='store_true')
    sub = debug_commands.add_parser('stacks', help='all thread stacks (ttrace), or --cores for registers of the 3 cores (coretrace)')
    sub.add_argument('--cores', action='store_true')
    sub = debug_commands.add_parser('modules', help='loaded RPLs with code and data ranges')
    sub.add_argument('--raw', action='store_true')
    debug_commands.add_parser('heaps', help='heaps and the code area')
    debug_commands.add_parser('cpu', help='per-core CPU use')
    sub = debug_commands.add_parser('mem', help='memory words (only inside a loaded module code or data)')
    sub.add_argument('address')
    sub.add_argument('words', nargs='?', type=int, default=32, help='32-bit words (default and minimum shown: 32)')
    sub = debug_commands.add_parser('disasm', help='disassembly from an address in a loaded module code')
    sub.add_argument('address')
    sub = debug_commands.add_parser('sym', help='module and function for an address (getsym)')
    sub.add_argument('address')
    sub = debug_commands.add_parser('crashdump', help='force a crashdump (asks first; may stop the title)')
    sub.add_argument('--yes', action='store_true')
    sub = commands.add_parser('gdb', help='read-only GDB remote server from the COS shell (snapshot; no stop/step/break)')
    sub.add_argument('--port', type=int, default=2331)
    sub.add_argument('--host', default='127.0.0.1', help='listen address (default: this PC only)')
    sub.add_argument('--once', action='store_true', help='exit after the first GDB client disconnects')
    sub = commands.add_parser('hio', help='HostBridge stand-in: SPARK live Capture for HIO-over-TCP titles (SparkCube v0.6+)')
    sub.add_argument('--list', action='store_true', help="list the title's open HIO channels and exit")
    sub.add_argument('--no-rewrite', action='store_true',
                     help="pass SPARK's capture path to the console unchanged")
    sub = commands.add_parser('log', help='read or follow shared serial output')
    sub.add_argument('--follow', action='store_true')
    sub.add_argument('--grep', default='')
    args = parser.parse_args(argv)
    # Commands that restart Cafe and drive the PCFS server themselves pause a background keeper.
    # (reboot only stops the server; a background keeper reconnects after it.)
    drives_pcfs = args.command in ('launch', 'run', 'stop') or (
        args.command == 'pcfs' and args.pcfs_command == 'up')
    try:
        if hasattr(args, 'root'):       # run, pcfs up, pcfs keep: only ever the one saved folder
            args.root = str(pcfs.checked_root(args.root))
        if drives_pcfs:
            pcfs.take_lease()
        if args.command == 'find':
            return find(args)
        if args.command == 'info':
            return info(args)
        if args.command in ('run', 'stop'):
            return {'run': run, 'stop': stop}[args.command](args)
        if args.command == 'spark':
            return {'list': spark_list, 'wait': spark_wait, 'open': spark_open}[args.spark_command](args)
        if args.command == 'debug':
            return debug(args)
        if args.command == 'gdb':
            return gdb(args)
        if args.command == 'hio':
            return hio(args)
        if args.command in ('crashlogs', 'backup', 'restore', 'repair', 'syslog'):
            return {'crashlogs': crashlogs, 'backup': backup, 'restore': restore, 'syslog': syslog,
                    'repair': repair_recovery_menu}[args.command](args)
        if args.command == 'config':
            return {'get': config_get, 'set': config_set}[args.config_command](args)
        if args.command == 'pcfs':
            return {'up': pcfs_up, 'status': pcfs_status, 'down': pcfs_down, 'keep': pcfs_keep,
                    'root': pcfs_root}[args.pcfs_command](args)
        if args.command == 'icons':
            return icons(args)
        if args.command in ('ls', 'stat', 'get', 'titles', 'launch', 'kill', 'restart', 'put', 'rm', 'mkdir', 'reboot', 'install', 'uninstall', 'maintenance',
                                 'default-title'):
            return {'ls': ls, 'stat': stat, 'get': get, 'titles': titles, 'launch': launch, 'kill': kill, 'restart': restart,
                    'put': put, 'rm': rm, 'mkdir': mkdir, 'reboot': reboot,
                    'install': install, 'uninstall': uninstall,
                    'maintenance': maintenance, 'default-title': default_title}[args.command](args)
        if args.command == 'serial':
            if args.stop:
                output(serial.rpc({'op': 'shutdown'}))
            elif args.status:
                output(serial.rpc({'op': 'status'}))
            elif args.background:
                output(start_service(args.port))
            else:
                serial.serve(serial.pick_port(args.port))
        elif args.command == 'cos':
            _, category, wire = user_command(' '.join(args.words))
            ensure_service()
            if category != 'read-only':
                audit('cos', wire, category=category)
            result = serial.rpc({'op': 'cos', 'command': wire})
            print(normalize(result['text']))
            ok = result['proof'] and not result['error'] and not result['gap']
            if not ok:   # only when something is off: the reply alone is the normal output
                print(f'catr: cos {wire}: {result["outcome"]}'
                      f'{"; " + str(result["error"]) if result["error"] else ""}'
                      f'{"; part of the log was lost" if result["gap"] else ""}', file=sys.stderr)
            return 0 if ok else 1
        elif args.command == 'log':
            ensure_service()
            cursor, pending = None, ''
            while True:
                request = {'op': 'log'}
                if cursor is not None:
                    request['cursor'] = cursor
                result = serial.rpc(request)
                if result['gap']:
                    print('[serial history gap]', file=sys.stderr)
                text = normalize(result['text'])
                if args.grep:
                    lines = (pending + text).split('\n')
                    pending = lines.pop()
                    print('\n'.join(line for line in lines if args.grep in line), end='\n' if any(args.grep in line for line in lines) else '', flush=True)
                else:
                    print(text, end='', flush=True)
                cursor = result['cursor']
                if result['error']:
                    raise OSError(result['error'])
                if not args.follow:
                    if pending and args.grep in pending:
                        print(pending)
                    break
                time.sleep(.25)
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'catr: {exc}', file=sys.stderr)
        return 1
    finally:
        if drives_pcfs:
            pcfs.release_lease()
