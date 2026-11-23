import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = Path(os.environ['CATR_LOGS']) if os.environ.get('CATR_LOGS') else ROOT / 'logs'
HOME = Path(os.environ.get('CATR_CONFIG_DIR', str(Path(os.environ.get('APPDATA', Path.home())) / 'catr')))
# The settings (IP, serial port, workspace, PCFS folder): CATR_CONFIG_FILE, else the file that
# HOME/config.json points to with "config_file" (CatR-Tools.exe keeps its config.json next to itself
# and points HOME's at it, so catr.cmd and the exe share one set of settings), else HOME/config.json.
# The running services' state (serial.json, pcfs.json, leases) always stays in HOME.
# Looked up at each use, so a test that points HOME elsewhere takes the settings file with it.
def _read(path):
    # utf-8-sig: a config.json edited by hand (Notepad, Windows PowerShell) may start with a BOM.
    return json.loads(path.read_text('utf-8-sig'))


def config_file():
    if os.environ.get('CATR_CONFIG_FILE'):
        return Path(os.environ['CATR_CONFIG_FILE'])
    home = HOME / 'config.json'
    try:
        pointer = _read(home).get('config_file')
    except (OSError, ValueError, AttributeError):
        pointer = None
    if isinstance(pointer, str) and pointer and Path(pointer).is_file():
        return Path(pointer)
    return home


def _workspace():
    """The workspace: backups, captures and logs.
    CATR_WORKSPACE (CatR-Tools.exe sets it for its built-in copy), else "workspace" in config_file(),
    else the workspace folder next to catr (ignored by git, since backups are console-specific)."""
    if os.environ.get('CATR_WORKSPACE'):
        return Path(os.environ['CATR_WORKSPACE'])
    try:
        saved = _read(config_file()).get('workspace')
    except (OSError, ValueError, AttributeError):
        saved = None
    return Path(saved) if saved else ROOT.parent / 'workspace'


WORKSPACE = _workspace()


def load():
    path = config_file()
    return _read(path) if path.exists() else {}


def save(value):
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')   # one per writer
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def windows_path(text):
    """A Windows path from C:\\x, /c/x (MSYS) or /cygdrive/c/x: CAFE_ROOT may be in any of these forms."""
    match = re.match(r'^(?:/cygdrive)?/([A-Za-z])(?:/(.*))?$', text)
    return Path(f"{match[1].upper()}:/{match[2] or ''}") if match else Path(text)


def cafe_root():
    """Where the Cafe SDK is: "cafe_root" in config_file(), else the CAFE_ROOT variable; None without either."""
    configured = load().get('cafe_root') or os.environ.get('CAFE_ROOT')
    return windows_path(configured) if configured else None


def cafe_path(relative):
    """(SDK root, root / relative); raises when the Cafe SDK or that part of it is missing."""
    root = cafe_root()
    if root and (root / relative).exists():
        return root, root / relative
    raise FileNotFoundError((f'{relative} not found in the Cafe SDK ({root})' if root else
                             f'{relative} is needed from the Cafe SDK, which is not set up')
                            + f'; set CAFE_ROOT, or cafe_root in {config_file()}')


def audit(kind, detail, **fields):
    """One JSON line per console change or non-read-only shell command (safety rule: everything is logged)."""
    from datetime import datetime
    path = LOGS / 'audit.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {'time': datetime.now().isoformat(timespec='seconds'), 'kind': kind, 'detail': detail, **fields}
    with path.open('a', encoding='utf-8') as log:
        log.write(json.dumps(entry) + '\n')
