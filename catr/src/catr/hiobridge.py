"""catr hio: a HostBridge stand-in for titles that carry HIO over TCP (SparkCube v0.6+).

On a CAT-DEV, HIO channels go console -> /dev/dk -> SDIO -> MION -> HostBridge, and HostBridge
lists them for PC tools on 127.0.0.1:6003. A CAT-R has no MION, so SparkCube links
`hio_socket.c`, which serves its HIO channels on console TCP 6100: the PC connects, sends the
channel name and a newline, gets "+\\n", and from then on the socket is that channel's byte
stream ("-\\n": no such channel open; "?\\n" lists them).

This module is the PC half:

- **Registry** on 127.0.0.1:6003, in the format SPARK's discovery loop parses (Spark.exe
  0x1401166a0): each connection gets one packet of records `+Name:port` / `-Name:port`, packed
  with no separator (the byte after a port is read as the next record's marker), then close.
  SPARK polls it every second.
- **One console link per channel**, kept open and retried while the title isn't running.
- **One local port per channel** that SPARK connects to; bytes are bridged both ways.
- **SparkCapture path rewrite.** SPARK's capture command (type 1) names a Windows path, and this
  console's pcfsserver only resolves `/cygdrive/%SAVE_EMU_DIR/...`. So type 1 is rewritten to
  `/cygdrive/%SAVE_EMU_DIR/spark/<CAPTURE_NAME>`. When the console reports completion (type 3) the
  file is copied from pcfs-root\\data\\save\\spark to the path SPARK asked for, before SPARK sees
  the message.

Message framing (both channels): big-endian u32 type, u32 payload length, payload.
"""
import re
import shutil
import socket
import struct
import threading
import time
from pathlib import Path, PureWindowsPath

CONSOLE_PORT = 6100
REGISTRY_PORT = 6003
CHANNELS = {'SparkCapture': 6201, 'SparkPlay005': 6202}
CAPTURE_NAME = 'hio_capture.4mp'
CAFE_SAVE_PREFIX = '/cygdrive/%SAVE_EMU_DIR/'
HEADER = struct.Struct('>II')
MAX_PAYLOAD = 16 * 1024 * 1024
RETRY = 2.0

# SparkCapture message types (gx2spark commsManager.o)
CAPTURE, CANCEL, COMPLETE, PROGRESS, OPTION = 1, 2, 3, 4, 5
CAPTURE_TYPES = {1: 'capture', 2: 'cancel', 3: 'complete', 4: 'progress', 5: 'option'}
PLAY_TYPES = {1: 'load', 2: 'progress', 3: 'loaded', 4: 'times', 5: 'metrics', 6: 'out of memory',
              7: 'update resource', 8: 'shader experiment', 9: 'pixel history', 10: 'pixel history file'}


def registry_records(channels):
    """[(name, port, up)] -> the packed record bytes SPARK parses."""
    return b''.join(f"{'+' if up else '-'}{name}:{port}".encode('ascii') for name, port, up in channels)


def read_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError('closed')
        data += chunk
    return bytes(data)


def read_message(sock):
    kind, length = HEADER.unpack(read_exact(sock, HEADER.size))
    if length > MAX_PAYLOAD:
        raise ConnectionError(f'message type {kind} claims {length} bytes')
    return kind, read_exact(sock, length) if length else b''


def frame(kind, payload=b''):
    return HEADER.pack(kind, len(payload)) + payload


def c_string(payload):
    return payload.split(b'\0', 1)[0].decode('utf-8', 'replace')


def windows_path(spark_path):
    """The PC file a SPARK capture path names, or None if it isn't one we can place.

    Accepts DOS (C:\\x or C:/x), Cygwin (/cygdrive/c/x) and UNC (\\\\host\\share\\x) forms.
    """
    p = spark_path.strip()
    m = re.match(r'^/cygdrive/([A-Za-z])(/.*)?$', p)
    if m:
        return Path(f'{m.group(1)}:' + (m.group(2) or '/').replace('/', '\\'))
    if re.match(r'^[A-Za-z]:[\\/]', p) or p.startswith('\\\\'):
        return Path(PureWindowsPath(p))
    return None


class Channel:
    def __init__(self, name, port):
        self.name, self.port = name, port
        self.console = None          # socket to the console, while up
        self.spark = None            # socket from SPARK, while connected
        self.lock = threading.Lock()
        self.wanted_path = None      # SparkCapture: where SPARK wants the current capture

    @property
    def up(self):
        return self.console is not None


class Bridge:
    def __init__(self, console_ip, *, console_port=CONSOLE_PORT, registry_port=REGISTRY_PORT,
                 channels=None, save_dir=None, rewrite=True, log=print, listen_host='127.0.0.1'):
        self.console_ip, self.console_port = console_ip, console_port
        self.registry_port, self.listen_host = registry_port, listen_host
        self.channels = [Channel(n, p) for n, p in (channels or CHANNELS).items()]
        self.save_dir = Path(save_dir) if save_dir else None
        self.rewrite = rewrite and self.save_dir is not None
        self.log = log
        self.stopping = threading.Event()
        self.listeners = []
        self.captures = []           # (spark path, copied-to path or None, size)

    # --- lifecycle -------------------------------------------------------------------------
    def start(self):
        self.listeners.append(self._listen(self.registry_port, self._serve_registry))
        for ch in self.channels:
            self.listeners.append(self._listen(ch.port, lambda s, ch=ch: self._accept_spark(ch, s)))
            self._thread(self._console_loop, ch)
        self.log(f'catr hio: registry on {self.listen_host}:{self.registry_port}; channels '
                 + ', '.join(f'{c.name}:{c.port}' for c in self.channels)
                 + f'; console {self.console_ip}:{self.console_port}')

    def stop(self):
        self.stopping.set()
        for s in self.listeners:
            _close(s)
        for ch in self.channels:
            with ch.lock:
                _close(ch.console)
                _close(ch.spark)
                ch.console = ch.spark = None

    def _thread(self, target, *args):
        t = threading.Thread(target=target, args=args, daemon=True)
        t.start()
        return t

    def _listen(self, port, handler):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if hasattr(socket, 'SO_EXCLUSIVEADDRUSE')
                          else socket.SO_REUSEADDR, 1)
        try:
            server.bind((self.listen_host, port))
        except OSError as exc:
            server.close()
            raise OSError(f'port {port} is in use ({exc}); is HostBridge or another catr hio running?') from exc
        server.listen(4)

        def loop():
            while not self.stopping.is_set():
                try:
                    client, _ = server.accept()
                except OSError:
                    return
                handler(client)
        self._thread(loop)
        return server

    # --- registry ---------------------------------------------------------------------------
    def _serve_registry(self, client):
        try:
            client.sendall(registry_records([(c.name, c.port, c.up) for c in self.channels]))
        except OSError:
            pass
        finally:
            _close(client)

    # --- console side -----------------------------------------------------------------------
    def _console_loop(self, ch):
        last_state = None
        while not self.stopping.is_set():
            state, sock = self._open_console(ch)
            if state != last_state and state != 'up':
                self.log(f'catr hio: {ch.name}: {state}')
            last_state = state
            if sock is None:
                self.stopping.wait(RETRY)
                continue
            with ch.lock:
                ch.console = sock
            self.log(f'catr hio: {ch.name}: console connected')
            try:
                while True:
                    kind, payload = read_message(sock)
                    self._from_console(ch, kind, payload)
            except (OSError, ConnectionError, struct.error):
                pass
            with ch.lock:
                if ch.console is sock:
                    ch.console = None
                spark, ch.spark = ch.spark, None
            _close(sock)
            _close(spark)
            if not self.stopping.is_set():
                self.log(f'catr hio: {ch.name}: console link closed')
                last_state = 'closed'
                self.stopping.wait(RETRY)

    def _open_console(self, ch):
        try:
            sock = socket.create_connection((self.console_ip, self.console_port), timeout=3)
        except OSError:
            return 'waiting for the title (console port closed)', None
        try:
            sock.sendall(ch.name.encode('ascii') + b'\n')
            reply = b''
            while not reply.endswith(b'\n') and len(reply) < 8:
                chunk = sock.recv(1)
                if not chunk:
                    break
                reply += chunk
        except OSError:
            _close(sock)
            return 'console did not answer', None
        if reply != b'+\n':
            _close(sock)
            return 'title is running but has not opened this channel', None
        sock.settimeout(None)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        return 'up', sock

    def _from_console(self, ch, kind, payload):
        if ch.name == 'SparkCapture':
            if kind == COMPLETE:
                self._finish_capture(ch)
            elif kind != PROGRESS:
                self.log(f'catr hio: console -> SPARK {CAPTURE_TYPES.get(kind, kind)} ({len(payload)} bytes)')
        else:
            self.log(f'catr hio: {ch.name}: console -> SPARK {PLAY_TYPES.get(kind, kind)}'
                     + (f' {c_string(payload)!r}' if kind in (4, 5, 10) else f' ({len(payload)} bytes)'))
        with ch.lock:
            spark = ch.spark
        if spark is not None:
            try:
                spark.sendall(frame(kind, payload))
            except OSError:
                pass

    # --- SPARK side ---------------------------------------------------------------------------
    def _accept_spark(self, ch, client):
        with ch.lock:
            if ch.console is None:
                _close(client)
                return
            old, ch.spark = ch.spark, client
        _close(old)
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # PC-local socket
        self.log(f'catr hio: {ch.name}: SPARK connected')
        self._thread(self._spark_reader, ch, client)

    def _spark_reader(self, ch, client):
        try:
            while True:
                kind, payload = read_message(client)
                kind, payload = self._to_console(ch, kind, payload)
                with ch.lock:
                    console = ch.console
                if console is None:
                    break
                # One sendall per message: the console reads header and payload separately.
                console.sendall(frame(kind, payload))
        except (OSError, ConnectionError, struct.error):
            pass
        with ch.lock:
            if ch.spark is client:
                ch.spark = None
        _close(client)
        if not self.stopping.is_set():
            self.log(f'catr hio: {ch.name}: SPARK disconnected')

    def _to_console(self, ch, kind, payload):
        if ch.name == 'SparkCapture':
            if kind == CAPTURE:
                return CAPTURE, self._start_capture(ch, c_string(payload))
            if kind == OPTION and len(payload) >= 8:
                index, value = struct.unpack('>II', payload[:8])
                self.log(f'catr hio: SPARK option {index} = {value}')
            else:
                self.log(f'catr hio: SPARK -> console {CAPTURE_TYPES.get(kind, kind)}')
        else:
            self.log(f'catr hio: {ch.name}: SPARK -> console {PLAY_TYPES.get(kind, kind)}'
                     + (f' {c_string(payload)!r}' if kind == 1 else f' ({len(payload)} bytes)'))
        return kind, payload

    def _start_capture(self, ch, path):
        ch.wanted_path = path
        if not self.rewrite or path.startswith(CAFE_SAVE_PREFIX):
            self.log(f'catr hio: SPARK capture -> {path}')
            return path.encode('utf-8') + b'\0'
        (self.save_dir / 'spark').mkdir(parents=True, exist_ok=True)
        local = self.save_dir / 'spark' / CAPTURE_NAME
        try:
            local.unlink()
        except FileNotFoundError:
            pass
        console_path = f'{CAFE_SAVE_PREFIX}spark/{CAPTURE_NAME}'
        self.log(f'catr hio: SPARK capture -> {path} (console writes {console_path})')
        return console_path.encode('utf-8') + b'\0'

    def _finish_capture(self, ch):
        wanted, ch.wanted_path = ch.wanted_path, None
        if wanted is None:
            self.log('catr hio: console reports capture complete')
            return
        if not self.rewrite or wanted.startswith(CAFE_SAVE_PREFIX):
            self.log(f'catr hio: capture complete: {wanted}')
            self.captures.append((wanted, None, None))
            return
        local = self.save_dir / 'spark' / CAPTURE_NAME
        target = windows_path(wanted)
        size = local.stat().st_size if local.exists() else None
        if size is None:
            self.log(f'catr hio: capture complete, but {local} is missing (is PCFS up?)')
            return
        if target is None:
            self.log(f'catr hio: capture complete ({size} bytes) at {local}; '
                     f'cannot place SPARK path {wanted!r}')
            self.captures.append((wanted, None, size))
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, target)
        except OSError as exc:
            self.log(f'catr hio: capture complete ({size} bytes) at {local}; copy to {target} failed: {exc}')
            self.captures.append((wanted, None, size))
            return
        self.log(f'catr hio: capture complete: {size} bytes -> {target}')
        self.captures.append((wanted, target, size))


def _close(sock):
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass


def list_channels(console_ip, port=CONSOLE_PORT, timeout=3):
    """Ask the console which HIO channels are open: [(name, connected)]."""
    with socket.create_connection((console_ip, port), timeout=timeout) as sock:
        sock.sendall(b'?\n')
        data = b''
        while not data.endswith(b'.\n'):
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    out = []
    for line in data.decode('utf-8', 'replace').splitlines():
        if line[:1] in '+*' and len(line) > 1:
            out.append((line[1:], line[0] == '*'))
    return out


def serve(console_ip, save_dir, *, rewrite=True, log=print, stop_after=None):
    bridge = Bridge(console_ip, save_dir=save_dir, rewrite=rewrite, log=log)
    bridge.start()
    started = time.monotonic()
    try:
        while stop_after is None or time.monotonic() - started < stop_after:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
    return bridge
