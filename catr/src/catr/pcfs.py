"""PCFS over TCP (V10 plugin): host server lifecycle and the per-boot reconnect.

Every Cafe restart resets PCFS on the console. What one boot looks like (logs, 2026-11-13/14):

    MCP: Boot PM flags - PON_...                                 boot starts
    ~2.8 s  PCFSTCP[V10]: selected native TCP transport=3 port=7500   (V11: same line, [V11] tag,
            then "TCP_SM_Init result=.. attempts=.. retries=..")
            then either (about half of all boots, cold or restart alike)
    ~2.8 s  __DKT_OpenTCP: TCP_SM_Init() failed / PCFS: Failed to start transport   -> lost
            or
    ~9.0 s  PCFS: Waiting for PC connection on port 7500...
            <1 s with a server dialing: Ping complete ... Attached volume to hfio01 (pcfs)
    +10 s   PCFS: No response from PCFSServer ...                                   -> lost
    ~12 s   master title starts

The modified pcfsserver dials out to console:7500 and exits about 2 s after a refused
connect, so a fresh one is kept dialing from the transport line until mount or failure.
"""
import csv
import io
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from . import config

# CATR_PCFS_SERVER (CatR-Tools.exe sets it), else the sprig build in this repository.
SERVER = Path(os.environ.get('CATR_PCFS_SERVER') or config.ROOT.parent / 'pcfsserver' / 'pcfsserver.exe')
FALLBACK_ROOT = config.WORKSPACE / 'PCFS'


def host_root():
    """The one PCFS host folder every catr command serves: `pcfs_root` in catr's config, else
    FALLBACK_ROOT. Set it with `catr pcfs root PATH`."""
    value = config.load().get('pcfs_root')
    return Path(value) if value else FALLBACK_ROOT


def same_folder(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def checked_root(requested=None):
    """The saved folder; a --root naming any other folder is refused rather than served."""
    saved = host_root()
    if requested and not same_folder(requested, saved):
        raise ValueError(f'catr serves one PCFS folder, {saved}; to switch to {requested}, '
                         f'run: catr pcfs root "{requested}"')
    return saved


def set_host_root(path):
    path = Path(path).resolve()
    if not path.is_dir():
        raise FileNotFoundError(f'{path} is not a folder')
    for state, what in ((Server.registered(), 'PCFS server'), (keeper_running(), 'PCFS keeper')):
        if state and not same_folder(state.get('root', ''), path):
            raise RuntimeError(f"the {what} (PID {state['pid']}) serves {state.get('root')}; "
                               'run catr pcfs down first')
    values = config.load()
    values['pcfs_root'] = str(path)
    config.save(values)
    return path
PORT = 7500
STATE_FILE = config.HOME / 'pcfs.json'
# A server still dialing (SYN_SENT) is replaced by a fresh one, but only at a moment when none of
# its SYNs can be in flight. Windows retransmits the SYN at about 1, 3, 7, 15 s after connect
# (Windows 11: initial RTO 1 s, 4 retries; the older 3/6/12 schedule sends at 3 and 9 s). Hang 2
# (2026-11-14) was most likely a kill at 3.0 s, right on the 3 s retransmit: that SYN reached
# the console, which had just started listening, the server answered the ping and PCFS mounted,
# and the kill ~140 ms later broke the link (PCFS: PANIC, console fully down). The windows below
# are in seconds since connect and avoid every SYN of both schedules by at least 0.5 s.
SYN_KILL_WINDOWS = ((3.5, 6.5), (7.5, 8.5))

BOOT = 'Boot PM flags'
# Plugin lines carry their build tag: PCFSTCP[V10], PCFSTCP[V11] (TCP_SM_Init retry), ...
SELECTED = re.compile(r'PCFSTCP\[V\d+\]: selected native TCP transport')
INIT_RESULT = re.compile(r'PCFSTCP\[(V\d+)\]: TCP_SM_Init result=(-?\d+) attempts=(\d+) retries=(\d+)')   # V11+
WAITING = 'PCFS: Waiting for PC connection on port'
MOUNTED = 'Attached volume to hfio01 (pcfs)'
PING = 'PCFS: Ping complete'
FAILURES = {'TCP_SM_Init() failed': 'transport-failed (TCP_SM_Init; console-side, retry with a restart)',
            'PCFS: Failed to start transport': 'transport-failed (console-side, retry with a restart)',
            'No response from PCFSServer': 'no-response (server missed the 10 s window)',
            'null selector state; refusing TCP': 'plugin-refused (null selector state; PCFS not on TCP this boot)',
            'signature mismatch; no patch applied': 'plugin-refused (signature mismatch: plugin does not match this IOSU)'}


class PcfsBoot:
    """PCFS state for the current boot, from serial text."""

    def __init__(self):
        self.partial = ''
        self.state, self.detail, self.boots = 'unknown', None, 0
        self.init = None        # V11+: {'plugin', 'result', 'attempts', 'retries'} for this boot

    def feed(self, text):
        lines = re.split('[\r\n]', self.partial + text)
        self.partial = lines.pop()[-4096:]
        for line in lines:
            match = INIT_RESULT.search(line)
            if match:
                self.init = {'plugin': match[1], 'result': int(match[2]),
                             'attempts': int(match[3]), 'retries': int(match[4])}
            if BOOT in line:
                self.state, self.detail, self.init = 'booting', None, None
                self.boots += 1
            elif SELECTED.search(line) and self.state in ('booting', 'unknown'):
                self.state = 'transport'
            elif WAITING in line and self.state in ('booting', 'transport', 'unknown'):
                self.state = 'waiting'
            elif PING in line and self.state != 'mounted':
                self.state = 'pinged'
            elif MOUNTED in line:
                self.state = 'mounted'
            else:
                for marker, reason in FAILURES.items():
                    if marker in line and self.state not in ('mounted', 'failed'):
                        self.state, self.detail = 'failed', reason
                        break

    @property
    def dialing(self):
        """The window in which a server should be trying to connect."""
        return self.state in ('transport', 'waiting', 'pinged')

    @property
    def done(self):
        return self.state in ('mounted', 'failed')


def current_boot(history_text):
    """PCFS state of the latest boot in the service's history ('unknown' if no boot is in it)."""
    boot = PcfsBoot()
    start = history_text.rfind(BOOT)
    if start >= 0:
        line_start = max(history_text.rfind('\r', 0, start), history_text.rfind('\n', 0, start)) + 1
        boot.feed(history_text[line_start:] + '\r')
    return boot


# --- host processes -------------------------------------------------------------------------

def _tasklist(image):
    out = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {image}', '/FO', 'CSV', '/NH'],
                         capture_output=True, text=True, errors='replace',
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    return [int(row[1]) for row in csv.reader(io.StringIO(out)) if len(row) > 1 and row[0].lower() == image.lower()]


def server_pids():
    return _tasklist(SERVER.name)


TCP_STATES = {1: 'CLOSED', 2: 'LISTENING', 3: 'SYN_SENT', 4: 'SYN_RECEIVED', 5: 'ESTABLISHED', 6: 'FIN_WAIT_1',
              7: 'FIN_WAIT_2', 8: 'CLOSE_WAIT', 9: 'CLOSING', 10: 'LAST_ACK', 11: 'TIME_WAIT', 12: 'DELETE_TCB'}


def tcp_connections():
    """[(state, remote IP, remote port, PID)] of this PC's IPv4 TCP connections.

    Asks Windows directly (GetExtendedTcpTable): netstat's state names are translated on a
    non-English Windows.
    """
    import ctypes
    import socket
    import struct
    api = ctypes.WinDLL('iphlpapi').GetExtendedTcpTable
    size, table = ctypes.c_ulong(0), None
    for _ in range(8):              # the table can grow between asking for its size and reading it
        result = api(table, ctypes.byref(size), False, socket.AF_INET.value, 5, 0)     # 5: TCP_TABLE_OWNER_PID_ALL
        if result == 0 and table is not None:
            break
        if result not in (0, 122):  # ERROR_INSUFFICIENT_BUFFER
            raise OSError(f'GetExtendedTcpTable failed ({result})')
        table = ctypes.create_string_buffer(size.value + 1024)
        size.value = len(table)
    else:
        raise OSError('GetExtendedTcpTable: the table kept growing')
    count = struct.unpack_from('<I', table)[0]
    rows = (struct.unpack_from('<I4sI4sII', table, 4 + 24 * i) for i in range(count))
    return [(TCP_STATES.get(state, str(state)), socket.inet_ntoa(remote), socket.ntohs(port & 0xFFFF), pid)
            for state, _, _, remote, port, pid in rows]


def tcp_state(pid, ip):
    """'ESTABLISHED', 'SYN_SENT', 'CLOSE_WAIT', ... for pid's connection to ip:7500, or None."""
    states = [state for state, remote, port, owner in tcp_connections() if (remote, port, owner) == (ip, PORT, pid)]
    for wanted in ('ESTABLISHED', 'SYN_SENT', 'CLOSE_WAIT'):
        if wanted in states:
            return wanted
    return states[0] if states else None


def load_state():
    return _load(STATE_FILE)


def _load(path):
    try:
        return json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    """True if a process with this PID is running (Windows; any image)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    import ctypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5                  # access denied: exists, not ours
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_identity(pid):
    """(creation time, image file name) of a running process this user can open, else None.
    Windows reuses PIDs, so a PID alone does not prove a recorded catr process is still alive."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        size = wintypes.DWORD(1024)
        name = ctypes.create_unicode_buffer(size.value)
        image = Path(name.value).name.lower() if kernel32.QueryFullProcessImageNameW(
            handle, 0, name, ctypes.byref(size)) else ''
        return created, image
    finally:
        kernel32.CloseHandle(handle)


def own_record():
    """Fields that let a later check tell this process from one that reused its PID."""
    identity = process_identity(os.getpid())
    return {'pid': os.getpid(), 'created': identity[0] if identity else None}


def recorded_alive(state):
    """True if the catr process a state file names still runs (not a reused PID)."""
    identity = state and process_identity(state.get('pid'))
    if not identity:
        return False
    if state.get('created') is not None:
        return identity[0] == state['created']
    return identity[1].startswith(('python', 'py.exe', 'pyw'))   # older file: no creation time recorded


# --- background keeper and the lease that pauses it ------------------------------------------
#
# `catr pcfs keep` follows serial and reconnects the server after every restart, including ones
# catr did not start (power button, crash, a title restarting Cafe). catr commands that drive
# PCFS themselves (pcfs up, launch, run, stop) hold a lease while they do; the keeper keeps
# reading the log but neither stops nor starts the server until the lease is gone.

KEEP_FILE = config.HOME / 'pcfs-keep.json'
LEASE_FILE = config.HOME / 'pcfs-lease.json'


def keeper_running():
    state = _load(KEEP_FILE)
    return state if recorded_alive(state) else None


def stop_keeper():
    """Stop the background keeper (it holds no console state; the server is stopped separately)."""
    state = keeper_running()
    if state:
        subprocess.run(['taskkill', '/PID', str(state['pid']), '/F'], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    KEEP_FILE.unlink(missing_ok=True)
    return state


def lease_holder():
    """PID of another live catr command driving PCFS, or None."""
    state = _load(LEASE_FILE)
    pid = state and state.get('pid')
    return pid if pid and pid != os.getpid() and recorded_alive(state) else None


def take_lease():
    holder = lease_holder()
    if holder:
        raise RuntimeError(f'another catr command (PID {holder}) is driving PCFS; wait for it to finish')
    LEASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEASE_FILE.write_text(json.dumps(own_record()), encoding='utf-8')


def release_lease():
    state = _load(LEASE_FILE)
    if state and state.get('pid') == os.getpid():
        LEASE_FILE.unlink(missing_ok=True)


LOG_DAYS = 30           # any PCFS log older than this is removed
SHORT_LOG_DAYS = 1      # a server log of one line (a dial that never connected) after this
_pruned = False


def prune_logs(folder=None, now=None):
    """Remove old logs from %APPDATA%\\catr\\pcfs; returns how many went.

    catr starts a fresh pcfsserver.exe for every dial, each with its own log, so most logs are one
    line ("Connecting PCFS host to console at ..."). Those go after a day, every log after 30 days.
    The newest keeper log and the running server's log (pcfs.json) are always kept, and a log that
    is still open (a live keeper or server) can't be removed on Windows and is skipped.
    """
    folder = Path(folder) if folder else config.HOME / 'pcfs'
    if not folder.is_dir():
        return 0
    now = time.time() if now is None else now
    logs = [path for path in folder.glob('*.log') if path.is_file()]
    keep = set()
    def modified(path):
        try:
            return path.stat().st_mtime
        except OSError:     # removed meanwhile (another catr pruning at the same time)
            return 0
    keepers = sorted((path for path in logs if path.name.startswith('keep-')), key=modified)
    if keepers:
        keep.add(keepers[-1])
    current = (_load(STATE_FILE) or {}).get('log')
    if current:
        keep.add(Path(current))
    removed = 0
    for path in logs:
        if any(same_folder(path, kept) for kept in keep):
            continue
        try:
            stat = path.stat()
            age_days = (now - stat.st_mtime) / 86400
            short = (not path.name.startswith('keep-') and stat.st_size < 512
                     and path.read_bytes().strip().count(b'\n') == 0)
            if age_days > LOG_DAYS or (short and age_days > SHORT_LOG_DAYS):
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def prune_logs_once():
    """prune_logs at most once per catr process (Server.start runs at every dial); never raises OSError."""
    global _pruned
    if not _pruned:
        _pruned = True
        try:
            prune_logs()
        except OSError:     # pruning never stops a server or keeper from starting
            pass


class Server:
    """The one pcfsserver.exe that catr owns (recorded in pcfs.json)."""

    def __init__(self, ip, root=None, trace=False):
        self.ip, self.root, self.trace = ip, str(root or host_root()), trace
        self.process, self.syn_since, self.starts = None, None, 0
        self.started_at = None      # monotonic time of our Popen; the connect comes after it

    @staticmethod
    def registered():
        state = load_state()
        if state and state.get('pid') in server_pids():
            return state
        return None

    def check_conflicts(self):
        if not Path(self.root).is_dir():
            raise FileNotFoundError(f'The PCFS folder {self.root} does not exist; create it, or choose one: catr pcfs root PATH')
        mine = (load_state() or {}).get('pid')
        others = [pid for pid in server_pids() if pid != mine]
        if others:
            raise RuntimeError(f'another pcfsserver.exe is running (PID {others[0]}); close it first')

    def start(self):
        if not SERVER.is_file():
            raise FileNotFoundError(f'Missing PCFS server: {SERVER}')
        logdir = config.HOME / 'pcfs'
        logdir.mkdir(parents=True, exist_ok=True)
        prune_logs_once()
        log = logdir / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.log')
        env = dict(os.environ)
        if self.trace:
            env['PCFS_TRACE'] = '1'
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        with log.open('ab') as out:
            try:   # survive the launching shell's job object where allowed
                self.process = subprocess.Popen([str(SERVER), self.ip, str(PORT), self.root], stdout=out, stderr=out,
                                                env=env, creationflags=flags | 0x01000000)
            except OSError:
                self.process = subprocess.Popen([str(SERVER), self.ip, str(PORT), self.root], stdout=out, stderr=out,
                                                env=env, creationflags=flags)
        self.syn_since, self.starts, self.started_at = None, self.starts + 1, time.monotonic()
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({'pid': self.process.pid, 'ip': self.ip, 'root': self.root, 'log': str(log),
                                          'started': datetime.now().isoformat(timespec='seconds')}), encoding='utf-8')

    def stop(self):
        """Stop catr's server (the one in pcfs.json, if it is still a pcfsserver.exe)."""
        pid = self.process.pid if self.process and self.process.poll() is None else (load_state() or {}).get('pid')
        if pid and pid in server_pids():
            subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.process = None

    def tick(self, boot):
        """Called on every poll while following the log; keeps a server dialing in the window."""
        if boot.state == 'failed' and self.process is not None:
            self.stop()            # this boot will never accept; don't leave it dialing (seen 2026-11-14)
        if not boot.dialing:
            return
        adopted = False
        if self.process is None or self.process.poll() is not None:
            # Adopt the registered server (another catr command may have started it) rather
            # than starting a second one next to it.
            registered = self.registered()
            if registered and registered.get('ip') == self.ip:
                pid, adopted = registered['pid'], True
            else:
                self.start()
                return
        else:
            pid = self.process.pid
        state = tcp_state(pid, self.ip)
        if state == 'SYN_SENT':
            self.syn_since = self.syn_since or time.monotonic()
            if not adopted and self.may_replace_dialing(boot) and tcp_state(pid, self.ip) == 'SYN_SENT':
                self.stop()      # checked once more just above: a connect may have completed
                self.start()
        elif state == 'CLOSE_WAIT':
            self.stop()
            self.start()
        else:
            self.syn_since = None

    def may_replace_dialing(self, boot, now=None):
        """True if our SYN_SENT server can be replaced without hitting a SYN that is in flight.

        Only while the console is not yet listening as far as serial shows (state 'transport'):
        once it is listening or pinged, the server's next retransmit connects, and a kill could
        land on a connection that has just mounted PCFS. The connect happened after started_at
        and before syn_since, so the time since connect lies between those two ages.
        """
        if boot.state != 'transport' or self.syn_since is None or self.started_at is None:
            return False
        now = time.monotonic() if now is None else now
        at_least, at_most = now - self.syn_since, now - self.started_at
        return any(at_least >= low and at_most <= high for low, high in SYN_KILL_WINDOWS)


class Keeper:
    """Feeds serial text to PcfsBoot and drives the Server: stop at each boot, dial in the window."""

    def __init__(self, server):
        self.server, self.boot, self.events = server, PcfsBoot(), []
        self._seen_boots = 0
        self._partial = ''

    def feed(self, text):
        self.boot.feed(text)
        if self.boot.boots != self._seen_boots:
            self._seen_boots = self.boot.boots
            self.server.stop()                     # the old connection belongs to the previous boot
        lines = re.split('[\r\n]', self._partial + text)
        self._partial = lines.pop()[-4096:]
        for line in lines:
            if (SELECTED.search(line) or INIT_RESULT.search(line)
                    or any(m in line for m in (WAITING, PING, MOUNTED, *FAILURES))):
                self.events.append(line.strip())

    def tick(self):
        self.server.tick(self.boot)
