"""Launch and kill over serial (catr launch, kill, restart): pick the method, send once, watch the log."""
import re
from .safety import RECOVERY
from .transport.serial import TitleState

# Lines worth showing while waiting; all seen on 2026-11-14.
EVENT = re.compile(r'MASTER TITLE KILLED|WAITING IN ROOT|ROOT IS STARTING MASTER TITLE|'
                   r'Power Transition -> PM_RESTART|TitleId 0x[0-9a-fA-F]{16} Version')


# 2026-11-14, after error 160-0101: a slowlaunch from the error screen got as far as
# "***PPC TOLD TO SHUT DOWN (m8,f0)***" plus SHUTINFO lines naming the processes still alive,
# then nothing more: no boot, wupserver gone (NET had killed all sockets), shell still up.
SHUTDOWN_TOLD = '***PPC TOLD TO SHUT DOWN'
BOOT_LINE = re.compile(r'TitleId 0x[0-9a-fA-F]{16} Version|Starting System Log|ROOT IS STARTING MASTER TITLE|WAITING IN ROOT')


# IOSU lines use colons (00:00:13:611), Cafe/PPC lines semicolons (00;00;31;566).
PPC_LINE = re.compile(r'\d\d;\d\d;\d\d;\d{3}:')
BOOT_START = 'Starting System Log'


def iosu_only_boot(text):
    """True when the latest boot in text has IOSU lines but no Cafe (PPC) line at all.

    2026-11-15: holding POWER + EJECT at power-on starts the SDK's "recovery mode" (480p menu,
    no PPC, no wupserver, shell prompt but no COS replies; serial ends at
    CDC_SIGNAL_STA_ASSOCIATED_NOTIFY). Early in a normal boot looks the same for ~20 s, so the
    caller also needs the line to have been quiet for a while.
    """
    at = text.rfind(BOOT_START)
    return at >= 0 and not PPC_LINE.search(text, at)


def stuck_shutdown(text):
    """True when the PPC was told to shut down and nothing booted afterwards."""
    at = text.rfind(SHUTDOWN_TOLD)
    return at >= 0 and not BOOT_LINE.search(text, at)


# The error viewer (error.rpx; 0005003010011[0-2]0A by region). After a fatal error the console
# reboots and ROOT prepares it as master WITHOUT a "ROOT IS STARTING MASTER TITLE" line, so
# TitleState stays "unknown". 2026-11-17: a `cos slowlaunch` sent ~1 s after it gained the
# foreground gave "***Launch or Call Outstanding. Cannot do another concurrently.", then
# "ReleaseForeground when no message sent" and a kernel crash (Process 1, Error 0xCAFEBADD).
ERROR_VIEWER = re.compile(r'0005003010011[0-2]0a')
PREPARE = re.compile(r'KiPrepareTitleAsync\(0x([0-9a-fA-F]{16})\)')
FOREGROUND = re.compile(r'-PROCESS (\d+) GAIN FOREGROUND-')
FATAL = re.compile(r'life time number of fatal errors|COS encountered unrecoverable error|'
                   r'Process \d+, Error 0x[0-9A-Fa-f]+|Kernel Non-recoverable Exception|AppPanic 0x')
BOOT_MARKERS = (BOOT_START, 'PPC USER-LEVEL FAST RESTART')
HBM = '0005003010010100'
FRONT_SAFE = ('title', 'root', 'unknown')       # a restart may be sent
FRONT_SETTLING = ('booting', 'outstanding', 'shutting-down', 'root-transition')   # wait, then refuse


def front_check(text):
    """What the serial log says is in front right now, judged from its latest lines.

    Returns {'verdict', 'title', 'seen'}; verdict is one of:
      unknown          no boot, restart or launch line in the history (service started late)
      title            a title gained the foreground and nothing is outstanding
      root             WAITING IN ROOT
      booting          a boot or fast restart with no master title in front yet
      outstanding      a KiPrepareTitleAsync with no title in front after it (launch in progress),
                       or ROOT's "Launch or Call Outstanding"
      shutting-down    PPC TOLD TO SHUT DOWN and no boot after it yet
      root-transition  ROOT (process 1) took the foreground and nothing settled after it
      error-viewer     the error viewer is being prepared or is in front
      fatal            a fatal error since the last boot (error shown, or the kernel crashed)
    `seen` holds the lines that decided it, for the report.
    """
    verdict, title, pending, seen, fatal = 'unknown', None, None, [], None
    master = False      # a master title has taken the foreground in this boot
    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            continue
        if any(marker in line for marker in BOOT_MARKERS):
            verdict, title, pending, seen, fatal, master = 'booting', None, None, [line], None, False
            continue
        if SHUTDOWN_TOLD in line:
            verdict, pending, seen = 'shutting-down', None, [line]
            continue
        if FATAL.search(line):
            fatal = fatal or line
            continue
        if 'Launch or Call Outstanding' in line:
            verdict, seen = 'outstanding', seen[-3:] + [line]
            continue
        if 'WAITING IN ROOT' in line:
            verdict, title, pending, seen, master = 'root', None, None, [line], True
            continue
        match = PREPARE.search(line)
        if match:
            pending = match[1].lower()
            verdict, seen = 'outstanding', [line]
            continue
        match = FOREGROUND.search(line)
        if not match:
            continue
        if match[1] == '1':                     # ROOT itself: it hands over or waits next
            if verdict != 'outstanding':
                verdict, title, seen = 'root-transition', None, [line]
            continue
        if pending:
            title, pending = pending, None
            # HBM is loaded at every boot before ROOT starts the master title.
            verdict = 'booting' if title == HBM and not master else 'title'
            master = master or verdict == 'title'
            seen.append(line)
    if any(tid and ERROR_VIEWER.fullmatch(tid) for tid in (title, pending)):
        verdict = 'error-viewer'
    if fatal:
        verdict, seen = 'fatal', [fatal] + seen
    return {'verdict': verdict, 'title': pending or title, 'seen': seen[-4:]}


def choose_method(state, slow=False):
    """`cos launch` only works from the Recovery Menu or ROOT; a running game ignores it silently.

    Anything else, including an unknown state (daemon just started, history gap), uses
    `cos slowlaunch`: a Cafe restart, about 23 s, that worked from a running title.
    """
    if slow:
        return 'slowlaunch'
    if state.get('state') == 'root':
        return 'launch'
    if state.get('state') == 'observed-title' and state.get('title') == RECOVERY:
        return 'launch'
    return 'slowlaunch'


class Watch:
    """Follows serial output after a send and decides when the action is visibly done."""

    RELAUNCH_WINDOW = 8    # non-empty lines after cosxml(0) in which the Relaunch line must appear

    def __init__(self, goal, tid=None):
        self.goal, self.tid = goal, tid
        self.title = TitleState()
        self.title.reset('watching from send')
        self.restarted = False
        self.failure = None
        self.source = None      # 'pcfs' or 'mlc': where the console mapped /vol/content from
        self._cosxml_zero = False
        self.events = []
        self._partial = ''

    def feed(self, text):
        self.title.feed(text)
        lines = re.split('[\r\n]', self._partial + text)
        self._partial = lines.pop()[-4096:]
        for line in lines:
            if 'PM_RESTART' in line or 'PON_RESTART' in line:
                self.restarted = True
            if EVENT.search(line):
                self.events.append(line.strip())
            if self.tid:
                # "DIAG:Result PrepareTitle(0x...) came back with cosxml(0x...)". cosxml(0) directly followed
                # by "PrepareTitle(Relaunch) Succeeded" is the normal Cafe-restart path (every slowlaunch and
                # launch from ROOT, 2026-11-14). cosxml(0) without it: title not found. A relaunch that
                # never restarts (os_version mismatch, hangs) ends as a timeout, i.e. uncertain.
                # Other lines can come in between: on 2026-11-14 "p2 received EXIT while in foreground."
                # sat between the two, and a check on the next line only gave a false not-found.
                if self._cosxml_zero and line.strip():
                    if 'PrepareTitle(Relaunch) Succeeded' in line or self.restarted:
                        self._cosxml_zero = False
                    else:
                        self._cosxml_lines += 1
                        if self._cosxml_lines >= self.RELAUNCH_WINDOW:
                            self._cosxml_zero = False
                            self.failure = 'not-found (cosxml(0) with no relaunch: title missing on the console or PCFS)'
                match = re.search(rf'PrepareTitle\(0x{self.tid}\) came back with cosxml\(0x([0-9a-fA-F]+)\)', line, re.I)
                if match and int(match[1], 16) == 0:
                    self._cosxml_zero, self._cosxml_lines = True, 0
                    self.events.append(line.strip())
                path = f'usr/title/{self.tid[:8]}/{self.tid[8:]}'
                if 'Mapping /vol/content to' in line and path in line.lower():
                    self.source = 'pcfs' if 'storage_hfio' in line else 'mlc'

    def outcome(self):
        value = self.title.value
        if self.failure and not self.restarted:     # a Cafe restart means the title was found
            return 'not-found'
        if self.goal == 'kill':
            return 'root' if value['state'] == 'root' else None
        if value['state'] == 'observed-title':
            return 'launched' if value['title'] == self.tid else 'other-title'
        return None
