"""`catr gdb`: a read-only GDB remote server answered from the COS debugging shell.

No console changes: everything comes from shell commands catr already sends (2026-11-15 formats,
Recovery Menu, SDK 21213):

- threads   `cos threads`   -> one GDB thread per OS thread, id = its context address (ThredCtx)
- registers `cos ctxdump C` -> r0-r31, LR, CR, SRR0 (pc), SRR1 (msr), fr0-fr31; CTR, XER and
                               FPSCR aren't printed and are reported as unavailable
- memory    `cos memdump A 32` -> aligned 128-byte blocks, cached per snapshot
- symbols   `cos getsym A`  (monitor sym)

The title keeps running: what GDB sees is a snapshot. A running thread's context is the one saved
at its last switch. `continue` lets the title run on; Ctrl+C takes a new snapshot. Writes,
breakpoints and stepping are refused.

Memory safety (`memdump 0xDEAD0000` crashed the kernel on 2026-11-14): memdump
always reads at least 32 words, so a block is only sent when all 128 bytes lie inside a range
known to be mapped for the title in front: a module's Code or Data range (`cos modules`), a heap
range (`cos heaps`), or the process data area from 0x10000000 up to the first heap above it
(static data and thread stacks). Ranges are rebuilt at every snapshot. Anything else answers E14
without sending a command.
"""
import re
import socket

from . import debug as dbg

BLOCK = dbg.MEMDUMP_MIN_WORDS * 4          # 128 bytes, what one memdump prints at minimum
DATA_AREA = 0x10000000
HEAP = re.compile(r'^0x[0-9A-Fa-f]{8}\s+(Exp|Frame|Unit)\s+0x([0-9A-Fa-f]{8}) - 0x([0-9A-Fa-f]{8})')
MEM_ROW = re.compile(r'^0x([0-9A-Fa-f]{8}): ((?:[0-9A-Fa-f]{8} ?){4})')
GPR = re.compile(r'\br(\d{1,2})\s*= 0x([0-9a-fA-F]{8})')
SPR = re.compile(r'\b(LR|CR|SRR0|SRR1|CTR|XER)\s*= 0x([0-9a-fA-F]{8})')
FPR = re.compile(r'\bfr(\d{1,2})\s*= 0x([0-9a-fA-F]{16})')

# GDB's standard PowerPC layout (regnum order = 'g' packet order): r0-r31, f0-f31, pc, msr, cr,
# lr, ctr, xer, fpscr.
REGISTERS = ([(f'r{i}', 4) for i in range(32)] + [(f'f{i}', 8) for i in range(32)]
             + [('pc', 4), ('msr', 4), ('cr', 4), ('lr', 4), ('ctr', 4), ('xer', 4), ('fpscr', 4)])
CTXDUMP_NAMES = {'LR': 'lr', 'CR': 'cr', 'SRR0': 'pc', 'SRR1': 'msr', 'CTR': 'ctr', 'XER': 'xer'}


def target_xml():
    core = '\n'.join(f'  <reg name="r{i}" bitsize="32" type="{"data_ptr" if i == 1 else "uint32"}" regnum="{i}"/>'
                     for i in range(32))
    fpu = '\n'.join(f'  <reg name="f{i}" bitsize="64" type="ieee_double" regnum="{32 + i}"/>' for i in range(32))
    return f'''<?xml version="1.0"?>
<!DOCTYPE target SYSTEM "gdb-target.dtd">
<target version="1.0">
<architecture>powerpc:common</architecture>
<feature name="org.gnu.gdb.power.core">
{core}
  <reg name="pc" bitsize="32" type="code_ptr" regnum="64"/>
  <reg name="msr" bitsize="32" type="uint32" regnum="65"/>
  <reg name="cr" bitsize="32" type="uint32" regnum="66"/>
  <reg name="lr" bitsize="32" type="code_ptr" regnum="67"/>
  <reg name="ctr" bitsize="32" type="uint32" regnum="68"/>
  <reg name="xer" bitsize="32" type="uint32" regnum="69"/>
</feature>
<feature name="org.gnu.gdb.power.fpu">
{fpu}
  <reg name="fpscr" bitsize="32" group="float" regnum="70"/>
</feature>
</target>
'''


def parse_heaps(text):
    return [(int(m.group(2), 16), int(m.group(3), 16)) for m in (HEAP.match(l.strip()) for l in dbg.lines(text)) if m]


def readable_ranges(modules, heaps):
    """Sorted, merged (low, high) ranges that are mapped for the title in front."""
    ranges = [m[kind] for m in modules for kind in ('code', 'data')] + list(heaps)
    above = [low for low, _ in heaps if low >= DATA_AREA]
    if above:
        ranges.append((DATA_AREA, min(above)))
    merged = []
    for low, high in sorted(r for r in ranges if r[1] > r[0]):
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def block_start(address, ranges):
    """Start of a 128-byte memdump that covers address and stays inside one range, or None."""
    for low, high in ranges:
        if low <= address < high and high - low >= BLOCK:
            start = address & ~(BLOCK - 1)
            return min(max(start, low), high - BLOCK)
    return None


def parse_memdump(text, start):
    """The 128 bytes from start, or raise if any row is missing (title log lines interleave)."""
    rows = {}
    for line in dbg.dump_lines(text):
        m = MEM_ROW.match(line)
        if m:
            rows[int(m.group(1), 16)] = bytes.fromhex(m.group(2).replace(' ', ''))
    try:
        return b''.join(rows[start + offset] for offset in range(0, BLOCK, 16))
    except KeyError as missing:
        raise OSError(f'memdump {start:#010x}: row {missing} missing from the reply') from None


def parse_ctxdump(text):
    regs = {}
    for line in dbg.lines(text):
        for m in GPR.finditer(line):
            regs[f'r{int(m.group(1))}'] = int(m.group(2), 16)
        for m in SPR.finditer(line):
            regs[CTXDUMP_NAMES[m.group(1)]] = int(m.group(2), 16)
        for m in FPR.finditer(line):
            regs[f'f{int(m.group(1))}'] = int(m.group(2), 16)
    if 'pc' not in regs or 'r1' not in regs:
        raise OSError('ctxdump: no SRR0/r1 in the reply')
    return regs


def encode_registers(regs, names=REGISTERS):
    out = []
    for name, size in names:
        value = regs.get(name)
        out.append('xx' * size if value is None else f'{value:0{size * 2}x}')
    return ''.join(out)


class Target:
    """A snapshot of the title in front, read through shell(command) -> text."""

    def __init__(self, shell):
        self.shell = shell
        self.refresh()

    def refresh(self):
        self.modules = dbg.parse_modules(self.shell('cos modules'))
        if not self.modules:
            raise OSError('cos modules listed nothing; not reading memory')
        self.ranges = readable_ranges(self.modules, parse_heaps(self.shell('cos heaps')))
        self.threads = [t for t in dbg.parse_threads(self.shell('cos threads'))]
        if not self.threads:
            raise OSError('cos threads listed nothing')
        self.blocks, self.contexts = {}, {}

    def thread_ids(self):
        return [int(t['context'], 16) for t in self.threads]

    def thread(self, tid):
        return next((t for t in self.threads if int(t['context'], 16) == tid), None)

    def registers(self, tid):
        if tid not in self.contexts:
            if self.thread(tid) is None or block_start(tid, self.ranges) is None:
                raise OSError(f'thread {tid:#x} unknown or outside readable memory')
            self.contexts[tid] = parse_ctxdump(self.shell(f'cos ctxdump {tid:#010x}'))
        return self.contexts[tid]

    def read(self, address, length):
        data = bytearray()
        while length > 0:
            start = block_start(address, self.ranges)
            if start is None:
                if not data:
                    raise PermissionError(f'{address:#010x} is outside readable memory')
                break                      # partial read: GDB accepts a shorter reply
            if start not in self.blocks:
                self.blocks[start] = parse_memdump(self.shell(f'cos memdump {start:#010x} {dbg.MEMDUMP_MIN_WORDS}'), start)
            chunk = self.blocks[start][address - start:address - start + length]
            data += chunk
            address, length = address + len(chunk), length - len(chunk)
        return bytes(data)


def checksum(payload):
    return sum(payload.encode('latin-1')) & 0xFF


def frame(payload):
    return f'${payload}#{checksum(payload):02x}'.encode('latin-1')


class Session:
    """One GDB connection: packet framing plus the handlers. io is a socket-like object."""

    def __init__(self, io, make_target, log=print):
        self.io, self.make_target, self.log = io, make_target, log
        self.ack, self.target, self.current, self.buffer = True, None, None, b''
        self.running = False

    # --- framing ---------------------------------------------------------------------------
    def send(self, payload):
        self.io.sendall(frame(payload))

    def packets(self):
        """Yield payload strings; '\\x03' (interrupt) is yielded on its own."""
        while True:
            while True:
                if self.buffer[:1] in (b'+', b'-'):
                    self.buffer = self.buffer[1:]
                    continue
                if self.buffer[:1] == b'\x03':
                    self.buffer = self.buffer[1:]
                    yield '\x03'
                    continue
                start = self.buffer.find(b'$')
                end = self.buffer.find(b'#', start + 1) if start >= 0 else -1
                if start >= 0 and end >= 0 and len(self.buffer) >= end + 3:
                    payload = self.buffer[start + 1:end].decode('latin-1')
                    good = f'{checksum(payload):02x}' == self.buffer[end + 1:end + 3].decode('latin-1').lower()
                    self.buffer = self.buffer[end + 3:]
                    if self.ack:
                        self.io.sendall(b'+' if good else b'-')
                    if good:
                        yield payload
                    continue
                if start > 0:
                    self.buffer = self.buffer[start:]
                break
            data = self.io.recv(4096)
            if not data:
                return
            self.buffer += data

    def serve(self):
        for packet in self.packets():
            reply = self.handle(packet)
            if reply is not None:
                self.send(reply)
            if packet in ('D', 'k') or packet.startswith('D;'):
                return

    # --- handlers --------------------------------------------------------------------------
    def snapshot(self):
        self.target = self.make_target()
        ids = self.target.thread_ids()
        if self.current not in ids:
            self.current = ids[0]

    def stop_reply(self):
        return f'T02thread:{self.current:x};'

    def handle(self, packet):
        try:
            return self._handle(packet)
        except PermissionError as error:
            self.log(f'gdb: {error}')
            return 'E14'
        except (OSError, ValueError) as error:
            self.log(f'gdb: {packet[:40]}: {error}')
            return 'E05'

    def _handle(self, packet):
        if packet == '\x03':
            if not self.running:
                return None
            self.running = False
            self.snapshot()
            return self.stop_reply()
        if self.running:
            return None
        if self.target is None:
            self.snapshot()
        kind = packet[:1]
        if packet.startswith('qSupported'):
            return 'PacketSize=4000;qXfer:features:read+;QStartNoAckMode+;vContSupported+'
        if packet == 'QStartNoAckMode':
            self.send('OK')
            self.ack = False
            return None
        if packet.startswith('qXfer:features:read:target.xml:'):
            offset, length = (int(x, 16) for x in packet.rsplit(':', 1)[1].split(','))
            data = target_xml()[offset:offset + length]
            return ('l' if offset + length >= len(target_xml()) else 'm') + data
        if packet == '?':
            return self.stop_reply()
        if packet == 'qAttached':
            return '1'
        if packet == 'qC':
            return f'QC{self.current:x}'
        if packet == 'qfThreadInfo':
            return 'm' + ','.join(f'{tid:x}' for tid in self.target.thread_ids())
        if packet == 'qsThreadInfo':
            return 'l'
        if packet.startswith('qThreadExtraInfo,'):
            t = self.target.thread(int(packet.split(',', 1)[1], 16))
            text = f"{t['name'] or 'thread'} [{t['state']} core {dbg.cores(t['affinity'])} pri {t['priority']}]" if t else '?'
            return text.encode().hex()
        if packet.startswith('qRcmd,'):
            return self.monitor(bytes.fromhex(packet[6:]).decode('latin-1').strip())
        if kind == 'H':
            tid = packet[2:]
            if tid not in ('0', '-1', ''):
                tid = int(tid, 16)
                if self.target.thread(tid) is None:
                    return 'E01'
                self.current = tid
            return 'OK'
        if kind == 'T':
            return 'OK' if self.target.thread(int(packet[1:], 16)) else 'E01'
        if kind == 'g':
            return encode_registers(self.target.registers(self.current))
        if kind == 'p':
            number = int(packet[1:], 16)
            if number >= len(REGISTERS):
                return 'E01'
            return encode_registers(self.target.registers(self.current), [REGISTERS[number]])
        if kind == 'm':
            address, length = (int(x, 16) for x in packet[1:].split(','))
            return self.target.read(address, min(length, 0x800)).hex()
        if packet == 'vCont?':
            return 'vCont;c;C'
        if kind in ('c', 'C') or packet.startswith(('vCont;c', 'vCont;C')):
            self.running = True            # the title never stopped; wait for Ctrl+C
            return None
        if kind in ('G', 'P', 'M', 'X', 'Z', 'z', 's', 'S') or packet.startswith(('vCont;s', 'vCont;S', 'vCont;t')):
            return 'E01'                   # read-only: no writes, breakpoints or stepping
        if packet.startswith(('D', 'k')):
            return 'OK'
        return ''

    def monitor(self, command):
        word, _, rest = command.partition(' ')
        if word == 'refresh':
            self.snapshot()
            text = f'new snapshot: {len(self.target.threads)} threads, {len(self.target.modules)} modules\n'
        elif word == 'threads':
            text = ''.join(f"{t['context']}  {t['state']}  core {dbg.cores(t['affinity']):<3} pri {t['priority']:>2}  "
                           f"pc {t['pc']}  {t['symbol']}  {t['name']}\n" for t in self.target.threads)
        elif word == 'modules':
            text = ''.join(f"{m['name']:<24} code {m['code'][0]:#010x}  data {m['data'][0]:#010x}   "
                           f"add-symbol-file <{m['name']}.elf> -s .text {m['code'][0]:#x} -s .data {m['data'][0]:#x}\n"
                           for m in self.target.modules)
        elif word == 'ranges':
            text = ''.join(f'{low:#010x}-{high:#010x}\n' for low, high in self.target.ranges)
        elif word == 'sym' and rest:
            address = dbg.parse_address(rest.strip())
            if block_start(address, self.target.ranges) is None:
                raise PermissionError(f'{address:#010x} is outside readable memory')
            found = dbg.parse_getsym(self.target.shell(f'cos getsym {address:#010x}'))
            text = f"{found['address']}  {found['symbol']}\n" if found else 'no symbol\n'
        else:
            text = ('catr gdb monitor commands: refresh (new snapshot), threads, modules (with add-symbol-file '
                    'lines), ranges (readable memory), sym ADDR\n')
        for start in range(0, len(text), 400):
            self.send('O' + text[start:start + 400].encode().hex())
        return 'OK'


def serve(make_target, host='127.0.0.1', port=2331, once=False, log=print):
    with socket.create_server((host, port)) as server:
        log(f'catr gdb: listening on {host}:{port}; in GDB: target remote {host}:{port}')
        while True:
            conn, peer = server.accept()
            log(f'catr gdb: client {peer[0]}:{peer[1]}')
            with conn:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # PC-side socket only
                try:
                    Session(conn, make_target, log).serve()
                except (ConnectionError, OSError) as error:
                    log(f'catr gdb: connection ended: {error}')
            log('catr gdb: client gone')
            if once:
                return
