"""What catr may send and change: COS command classes, protected titles and console paths."""
import shlex

RECOVERY = '000500101f700700'
# Tested inside System Config Tool and a game on 2026-11-14; none changes console state.
# iostats/intstats/osstats/kstats/iodump need a core ID on this OS ("Missing needed argument.").
READ_ONLY = set('help sdkversion threads ttrace tcheck coretrace modules heaps cpuusage logdump fslogdump bootperf iostats intstats osstats kstats iodump'.split())
# Take an address the kernel dereferences without checking. 2026-11-14: `memdump 0x00000000 4`
# printed nothing, but `memdump 0xDEAD0000 4` caused "Kernel Non-recoverable Exception 2" and a
# fatal error (console crash, CBL log to SD). `catr debug mem` and `catr debug disasm` check the
# address against a fresh `cos modules` map first; `catr cos` sends it as typed.
ADDRESS = set('memdump disasm getsym stacktrace ctxdump heapcheck heapdump'.split())
DISRUPTIVE = set('launch launchex slowlaunch kill killrestart crashdump logsave setfslog runonthread timestamp debug'.split())


def classify(command):
    if any(c in command for c in '\r\n\x00;|&') or any(ord(c) < 32 or ord(c) > 126 for c in command):
        raise ValueError('Only a single ASCII COS command is allowed')
    words = shlex.split(command)
    if words and words[0] == 'cos':
        words.pop(0)
    if not words:
        raise ValueError('Missing COS command')
    name = words[0]
    if name in READ_ONLY:
        category = 'read-only'
    elif name in ADDRESS:
        category = 'address'
    elif name in DISRUPTIVE:
        category = 'disruptive'
    else:
        category = 'dangerous'
    return name, category, 'cos ' + ' '.join(words)


ACTIONS = ('launch', 'launchex', 'slowlaunch', 'kill', 'killrestart')
LAUNCH_ARGS_MAX = 200     # characters of arguments on one launchex line


def title_id(text):
    """'000500101f700700', '0x00050010 0x1F700700', '00050010-1f700700' or 'recovery' -> 16 hex."""
    if text.strip().lower() == 'recovery':
        return RECOVERY
    tid = ''.join(part[2:] if part.lower().startswith('0x') else part
                  for part in text.replace('-', ' ').split()).lower()
    if len(tid) != 16 or any(c not in '0123456789abcdef' for c in tid):
        raise ValueError(f'Not a 64-bit title ID: {text!r}')
    return tid


def launch_args(args):
    """Arguments for `cos launchex`: printable tokens without spaces or shell separators."""
    args = list(args or [])
    for arg in args:
        if not arg or any(c in arg for c in ';|&"\'') or any(ord(c) <= 32 or ord(c) > 126 for c in arg):
            raise ValueError(f'launch argument {arg!r}: printable ASCII without spaces, quotes or ;|& only')
    if len(' '.join(args)) > LAUNCH_ARGS_MAX:
        raise ValueError(f'launch arguments longer than {LAUNCH_ARGS_MAX} characters')
    return args


def action(name, tid=None, args=None):
    """Wire form of a disruptive command catr itself builds (never free text).

    Forms seen working: `cos launch 0x00050000 0x1F6FF000`, `cos slowlaunch 0x000500101F700700`,
    `cos kill` (2026-11-14); `cos launchex 0x000500001F6FF000 catrmarker42` (2026-11-15, SDK form
    `launchex tid [args]`; the title started, the console does not print the arguments).
    """
    if name not in ACTIONS:
        raise ValueError(f'Unknown action {name!r}')
    if name in ('kill', 'killrestart'):
        return name, f'cos {name}'
    tid = title_id(tid or '').upper()
    if name == 'launch':
        return name, f'cos launch 0x{tid[:8]} 0x{tid[8:]}'
    if name == 'launchex':
        return name, ' '.join([f'cos launchex 0x{tid}', *launch_args(args)])
    return name, f'cos slowlaunch 0x{tid}'


def user_command(command):
    """Any single COS command the user types (`catr cos`), with its class.

    `catr cos` sends whatever the user types, by design. The class still decides one thing: only
    read-only commands are ever resent automatically.
    """
    return classify(command)


def readonly(command):
    name, category, wire = classify(command)
    if category == 'address':
        raise ValueError(f'{name} takes a raw address; a bad one crashed the kernel on 2026-11-14. '
                         'Not allowed until addresses are checked against loaded modules')
    if category != 'read-only':
        raise ValueError(f'{name} is {category}; only read-only COS commands are allowed here')
    return name, wire


# --- titles and console paths that catr changes -----------------

SCT = '000500101f700500'


def boot_critical(tid):
    """Never installed over or removed (same list as Recovery Menu v0.9's is_boot_critical, plus itself)."""
    tid = tid.lower()
    if tid == RECOVERY:
        return True
    if not tid.startswith('00050010'):
        return False
    lo = int(tid[8:], 16)
    return (lo in (0x10000100, 0x10000200, 0x1000400A, 0x1000800A, 0x100040FF, 0x1F700300, 0x1F700400)
            or lo & 0xFFFFF0FF == 0x10045000)


def install_block(tid, version, installed_version=None):
    """Why a package may not be installed, or None. Recovery Menu reinstall is `repair` only."""
    if tid.lower() == RECOVERY:
        return 'the Recovery Menu is only reinstalled through catr repair recovery-menu'
    if boot_critical(tid):
        return 'boot-critical title (boot1, firmware, OS or system updater)'
    if installed_version is not None and installed_version > version:
        return f'older than the installed version (v{version} < v{installed_version})'
    return None


# app types catr may uninstall: game, game update, game DLC (wut MCPAppType)
UNINSTALLABLE = {0x80000000: 'game', 0x0800001B: 'update', 0x0800000E: 'DLC'}


def uninstall_block(tid, app_type, path=None):
    if tid.lower() in (SCT, RECOVERY) or boot_critical(tid):
        return 'protected title (SCT, Recovery Menu or boot-critical)'
    if app_type not in UNINSTALLABLE:
        return f'app type {app_type:08x} is not a game, update or DLC'
    if path is not None and not path.startswith(('/vol/storage_mlc01/', '/vol/storage_usb')):
        # e.g. 000500001f701500 is a game-type title on /vol/system_slc: never removed from there.
        return f'installed at {path}, not on MLC or USB storage'
    return None


STAGING = '/vol/storage_mlc01/usr/tmp/catr'
# Never written or removed by put/rm/mkdir, even with --unsafe: system volumes (config goes
# through `catr config set`) and every title folder (titles go through install/uninstall).
WRITE_REFUSED = ('/vol/system', '/vol/system_slc', '/vol/storage_mlc01/sys', '/vol/storage_mlc01/usr/title')


def write_check(path, unsafe=False):
    """A console path put/rm/mkdir may change: refused areas raise; outside staging needs --unsafe."""
    import posixpath
    if not path.startswith('/vol/') or '\\' in path or '\x00' in path:
        raise ValueError(f'console paths start with /vol/: {path!r}')
    norm = posixpath.normpath(path)
    if norm != path.rstrip('/') or '/../' in path + '/':
        raise ValueError(f'path must be normalised (no .., //, trailing /): {path!r}')
    for root in WRITE_REFUSED:
        if norm == root or norm.startswith(root + '/'):
            raise ValueError(f'{norm} is under {root}, which catr never writes or removes')
    if norm.count('/') < 3:
        raise ValueError(f'{norm} is a volume or top-level folder; not changed')
    if not (norm == STAGING or norm.startswith(STAGING + '/')) and not unsafe:
        raise ValueError(f'{norm} is outside catr staging ({STAGING}); add --unsafe to change it')
    return norm
