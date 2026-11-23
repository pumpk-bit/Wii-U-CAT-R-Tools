"""One serial owner, authenticated loopback RPC and bounded output history."""
import json
import re
import secrets
import socket
import socketserver
import threading
import time
from datetime import datetime
from .. import config
from ..safety import action, user_command

MAX_HISTORY = 2 * 1024 * 1024
ACTION_SETTLE = 5.0     # seconds after a master title start before launch/slowlaunch/kill go out


def echo_proof(text, wire):
    """The exact echo of `wire` as a complete line, optionally after a leftover '# ' prompt.

    Used for launch/slowlaunch/kill: the title switch starts before the shell prints its next
    prompt, so the read-only echo+prompt rule never fires. Seen 2026-11-14:
    `\\rcos launch 0x00050000 0x1F6FF000\\r00:00:12:632: MCP: ...` and
    `\\r# cos slowlaunch 0x000500101F700700\\r00:00:23:998: ...`. Garbled input never echoes exactly.
    """
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return any(line.lstrip('# ').strip() == wire for line in lines[:-1])


def command_proof(name, text, wire=None):
    """True once the shell has visibly taken the command.

    The general proof is the shell echoing the exact command line, then a '#' prompt line.
    Seen for help, sdkversion, threads, modules, launch, slowlaunch and kill on 2026-11-14.
    A garbled input showed a mangled echo and no prompt. Only some commands also print a
    '---- COS Debugging Shell Command: <name> ----' header (modules and help don't).
    """
    if wire is not None:
        lines = [line.strip() for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
        for i, line in enumerate(lines[:-1]):
            if line == wire and lines[i + 1].startswith('#'):
                return True
    if f'---- COS Debugging Shell Command: {name} ----' in text:
        return True
    # This OS prints help directly, without the usual debugging-shell header.
    return name == 'help' and 'cos help' in text and 'List all commands' in text and 'cos sdkversion' in text and 'cos killrestart' in text


class TitleState:
    """Master title as observed on serial; never a foreground query.

    Only this sequence sets a title (seen on every boot, fast restart and launch on 2026-11-14):
        ROOT IS STARTING MASTER TITLE
        DIAG:Issued KiPrepareTitleAsync(0x<tid>)
        TitleId 0x<tid> Version ...
    Other TitleId lines are ignored. During boot, error.rpx (000500301001110a) and
    0005003010010100 print TitleId lines before the master title starts.
    """
    RESET_MARKERS = ('RESTART', 'MASTER TITLE KILLED', 'PPC TOLD TO SHUT DOWN', 'Restarting Cafe')

    def __init__(self):
        self.partial = ''
        self.reset('daemon started')

    def reset(self, reason):
        self.value = {'state': 'unknown', 'title': None, 'reason': reason}
        self.armed = False      # saw ROOT IS STARTING MASTER TITLE
        self.prepared = None    # tid from the KiPrepareTitleAsync that followed it

    def feed(self, text):
        lines = re.split('[\r\n]', self.partial + text)
        self.partial = lines.pop()[-4096:]
        for line in lines:
            if any(marker in line for marker in self.RESET_MARKERS):
                self.reset('restart or title transition')
            if 'ROOT IS STARTING MASTER TITLE' in line:
                self.reset('master title starting')
                self.armed = True
                continue
            if 'WAITING IN ROOT' in line:
                self.reset(line.strip())
                self.value['state'] = 'root'
                continue
            if not self.armed:
                continue
            match = re.search(r'KiPrepareTitleAsync\(0x([0-9a-fA-F]{16})\)', line)
            if match and self.prepared is None:
                self.prepared = match[1].lower()
                continue
            match = re.search(r'TitleId 0x([0-9a-fA-F]{16}) Version', line)
            if match and self.prepared is not None and match[1].lower() == self.prepared:
                self.value = {'state': 'observed-title', 'title': self.prepared,
                              'reason': 'master title start seen on serial; not a foreground query'}
                self.armed, self.prepared = False, None
                self.started_at = time.monotonic()

    started_at = None   # monotonic time of the last observed master title start


class History:
    def __init__(self):
        self.condition = threading.Condition()
        self.data = bytearray()
        self.end = 0
        self.last = time.monotonic()
        self.title = TitleState()
        self.error = None

    def append(self, data):
        with self.condition:
            self.data.extend(data)
            self.end += len(data)
            del self.data[:-MAX_HISTORY]
            self.last = time.monotonic()
            self.title.feed(data.decode('latin-1'))
            self.condition.notify_all()

    def snapshot(self, cursor):
        with self.condition:
            start = self.end - len(self.data)
            gap = cursor < start
            offset = max(start, min(cursor, self.end)) - start
            return {'cursor': self.end, 'text': self.data[offset:].decode('latin-1'),
                    'gap': gap, 'title': dict(self.title.value), 'error': self.error,
                    'quiet': round(time.monotonic() - self.last, 1)}   # seconds since the last byte


def rpc(request, timeout=35):
    endpoint = json.loads((config.HOME / 'serial.json').read_text('utf-8'))
    request = dict(request, token=endpoint['token'])
    with socket.create_connection(('127.0.0.1', endpoint['port']), timeout=timeout) as sock:
        sock.sendall(json.dumps(request).encode() + b'\n')
        response = sock.makefile('rb').readline(16 * MAX_HISTORY)
    if not response:
        raise ConnectionError('Serial service closed without a reply')
    result = json.loads(response)
    if 'rpc_error' in result:
        raise RuntimeError(result['rpc_error'])
    return result


def pick_port(requested=None):
    """The COM port to use: the requested one, else the saved one, else the only USB serial port here."""
    chosen = requested or config.load().get('serial_port')
    if chosen:
        return chosen
    from .winserial import available_ports
    ports = available_ports()
    if len(ports) == 1:
        return ports[0]
    raise ValueError('No serial port saved, and '
                     + (f'this PC has several ({", ".join(ports)})' if ports else 'no USB serial port found')
                     + '; connect the cable and run: catr find --ip ADDRESS --port COMx (COMx is in Device Manager > Ports)')


def serve(port, baud=57600):
    from .winserial import Port
    config.HOME.mkdir(parents=True, exist_ok=True)
    token, history = secrets.token_hex(32), History()
    command_lock = threading.Lock()
    stop = threading.Event()
    logdir = config.WORKSPACE / 'logs/serial'
    logdir.mkdir(parents=True, exist_ok=True)
    logpath = logdir / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.log')

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(5)
            try:
                line = self.rfile.readline(8193)
                if len(line) > 8192:
                    raise ValueError('Request too large')
                request = json.loads(line)
                if not secrets.compare_digest(request.get('token', ''), token):
                    raise ValueError('Invalid local service token')
                operation = request.get('op')
                if operation == 'status':
                    result = history.snapshot(history.end)
                    result.update(port=port, baud=baud, log=str(logpath))
                elif operation == 'log':
                    result = history.snapshot(int(request.get('cursor', max(0, history.end - 16384))))
                elif operation == 'cos':
                    name, category, wire = user_command(request['command'])
                    resend = category == 'read-only'     # anything else may already have run
                    with command_lock:
                        if history.error:
                            raise OSError(history.error)
                        begin = history.end
                        started = time.monotonic()
                        serial_port.write((wire + '\r\n').encode('ascii'))
                        proof, retried = False, False
                        while time.monotonic() - started < 25:
                            result = history.snapshot(begin)
                            proof = command_proof(name, result["text"], wire) or (
                                not resend and echo_proof(result['text'], wire))
                            if result['error'] or result['gap']:
                                break
                            elapsed = time.monotonic() - started
                            if proof and time.monotonic() - history.last >= 1.5:
                                break
                            if resend and not proof and not retried and elapsed >= 3:
                                serial_port.write((wire + '\r\n').encode('ascii'))
                                retried = True
                            if not proof and elapsed >= 6:
                                break
                            time.sleep(.05)
                        result = history.snapshot(begin)
                        result.update(proof=proof, retried=retried, category=category,
                                      outcome='observed' if proof else 'uncertain')
                elif operation == 'act':
                    # Disruptive: sent exactly once, never resent (a late launch could run twice).
                    name, wire = action(request['action'], request.get('tid'), request.get('args'))
                    with command_lock:
                        if history.error:
                            raise OSError(history.error)
                        # A title that has only just started isn't ready for the shell: on 2026-11-14 a
                        # `cos launch` sent right after the Recovery Menu's TitleId line got no echo and
                        # was lost, and a launch sent in mid-boot ended in fatal error 160-0103.
                        since = history.title.started_at
                        if since is not None and time.monotonic() - since < ACTION_SETTLE:
                            time.sleep(ACTION_SETTLE - (time.monotonic() - since))
                        before = dict(history.title.value)
                        begin = history.end
                        started = time.monotonic()
                        serial_port.write((wire + '\r\n').encode('ascii'))
                        proof = False
                        while time.monotonic() - started < 6 and not proof:
                            time.sleep(.05)
                            proof = echo_proof(history.snapshot(begin)['text'], wire)
                        result = history.snapshot(begin)
                        result.update(proof=proof, wire=wire, before=before, begin=begin,
                                      outcome='observed' if proof else 'uncertain')
                elif operation == 'shutdown':
                    result = {'stopping': True}
                    threading.Thread(target=server.shutdown, daemon=True).start()
                else:
                    raise ValueError('Unknown serial operation')
            except Exception as exc:
                result = {'rpc_error': str(exc)}
            self.wfile.write(json.dumps(result).encode() + b'\n')

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = False

    def read_loop(log):
        try:
            while not stop.is_set():
                data = serial_port.read()
                if data:
                    log.write(data)
                    log.flush()
                    history.append(data)
                else:
                    time.sleep(.02)
        except Exception as exc:
            with history.condition:
                history.error = str(exc)
                history.title.reset('serial read error')
                history.condition.notify_all()

    # Fixed loopback port enforces one daemon per host. Bind before opening COM.
    with Server(('127.0.0.1', 47332), Handler) as server:
        serial_port = Port(port, baud)
        endpoint = config.HOME / 'serial.json'
        try:
            endpoint.write_text(json.dumps({'port': server.server_address[1], 'token': token}), encoding='utf-8')
            with logpath.open('xb') as log:
                reader = threading.Thread(target=read_loop, args=(log,), daemon=True)
                reader.start()
                print(f'Serial service: {port}, {baud} baud; raw log: {logpath}', flush=True)
                try:
                    server.serve_forever(poll_interval=.2)
                except KeyboardInterrupt:
                    pass
                finally:
                    stop.set()
                    reader.join(3)
        finally:
            serial_port.close()
            endpoint.unlink(missing_ok=True)
