"""`catr debug`: COS shell debug commands with parsed output.

Address commands (memdump, disasm, getsym) dereference whatever they are given: on 2026-11-14
`memdump 0xDEAD0000 4` crashed the kernel. They are only sent for addresses whose whole range lies
inside a Code or Data range from a fresh `cos modules` of the title in front.
Formats below were captured on 2026-11-15 in the Recovery Menu (SDK 21213).
"""
import re

# 00;00;54;646: Code:0x02000000-0x02016d90 Data:0x10502200-0x1053cacc LoadInfo:... FileInfo:... Module:cat_recovery_menu
MODULE = re.compile(r'Code:0x([0-9a-fA-F]{8})-0x([0-9a-fA-F]{8}) Data:0x([0-9a-fA-F]{8})-0x([0-9a-fA-F]{8}).*?Module:(\S+)')
# ... 0000000000028BF6 RUNN 10096208 000A 00 00 100950B0 0103D2FC 0103D2FC coreinit.rpl|__OSUnlockScheduler {SYS CDS Thread} Waiting on ...
THREAD = re.compile(r'\b([0-9A-F]{16}) (\w{4}) ([0-9A-F]{8}) ([0-9A-F]{4}) ([0-9A-F]{2}) ([0-9A-F]{2}) '
                    r'([0-9A-F]{8}) ([0-9A-F]{8}) ([0-9A-F]{8}) (\S+)\s?(.*)$')
GETSYM = re.compile(r'addr: 0x([0-9A-Fa-f]{8}) symbol: (.+)')
STAMP = re.compile(r'^\d\d[;:]\d\d[;:]\d\d[;:]\d{3}: ?')
MEMDUMP_MIN_WORDS = 32    # `memdump addr N` prints N words, four per row, never fewer than 32 (2026-11-15)
DISASM_SPAN = 0x100       # `disasm` ignores its count and prints about 50 instructions (0xC8 bytes)


def lines(text):
    """Shell output without timestamps, echo, prompts or catr's outcome note."""
    out = []
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = STAMP.sub('', line.lstrip('# ')).rstrip()      # the prompt "# " comes before the stamp
        if not line.strip() or line.startswith(('cos ', '[outcome:')):
            continue
        out.append(line)
    return out


CPU = re.compile(r'COS CPU Utilization core0:([\d.]+) core1:([\d.]+) core2:([\d.]+)')


# memdump rows "0x031CDE3C: 83FF120C ..." and disasm rows "0x031cde3c    0x83ff120c    lwz ...",
# plus disasm's symbol line "unity-master|SetBackfaceMode__15GfxDeviceClientFb+0x84"
DUMP_ROW = re.compile(r'^(0x[0-9a-fA-F]{8}[:\s]|\S+\|\S+\+0x[0-9a-fA-F]+$)')


def dump_lines(text):
    """memdump/disasm rows only: the title in front prints its own log lines in between."""
    return [line for line in lines(text) if DUMP_ROW.match(line)]


def parse_cpu(text):
    m = CPU.search(text)
    return {f'core{i}': float(m.group(i + 1)) for i in range(3)} if m else None


def heap_lines(text):
    """The heaps table only: the title in front prints its own log lines in between."""
    keep = ('Heap Addr', 'Code Addr', 'Showing', '0x')
    return [line for line in lines(text) if line.lstrip().startswith(keep)]


def parse_modules(text):
    return [{'name': m.group(5), 'code': (int(m.group(1), 16), int(m.group(2), 16)),
             'data': (int(m.group(3), 16), int(m.group(4), 16))} for m in MODULE.finditer(text)]


def parse_threads(text):
    threads = []
    for line in lines(text):
        m = THREAD.search(line)
        if not m:
            continue
        rest = m.group(11)
        name, _, wait = rest.partition('Waiting on ')
        threads.append({'state': m.group(2), 'context': '0x' + m.group(3), 'affinity': int(m.group(4), 16),
                        'priority': int(m.group(5), 16), 'stack': '0x' + m.group(7), 'lr': '0x' + m.group(8),
                        'pc': '0x' + m.group(9), 'symbol': m.group(10), 'name': name.strip(),
                        'waiting_on': wait.strip() or None})
    return threads


def cores(affinity):
    return ''.join(str(c) for c in range(3) if affinity & (1 << c)) or '-'


def parse_address(text):
    value = int(text, 0)
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f'{text} is not a 32-bit address')
    return value


def check_range(address, size, modules):
    """(module, 'code'|'data') holding [address, address+size), or raise without sending anything."""
    for module in modules:
        for kind in ('code', 'data'):
            low, high = module[kind]
            if low <= address and address + size <= high:
                return module['name'], kind
    near = [f"{m['name']} code {m['code'][0]:#010x}-{m['code'][1]:#010x}" for m in modules[:4]]
    raise ValueError(f'{address:#010x}+{size:#x} is not inside a loaded module\'s Code or Data range; not sent '
                     f'(the kernel crashes on a bad address). Ranges: catr debug modules ({", ".join(near)}, ...)')


def parse_getsym(text):
    m = GETSYM.search(text)
    return {'address': '0x' + m.group(1).upper(), 'symbol': m.group(2).strip()} if m else None


# coreinit 1000400A's GDB stub state block (.data, same address in every title on that OS):
#   +0 run state: 0 = all cores held in the stub's command loop, 2 = running, 3 = stepping
#   +4 core count: 0 until the stub's transport init ran (SparkCube `dbgtry`), then 3
# Killing a title while the stub holds its cores hung core 1 ("FAIL: PPC Core 1 Heartbeat",
# 2026-11-21); a kill after `detach` is clean.
STUB_STATE = 0x1004F9B4
STUB_RUNNING = 2


def parse_stub_state(text):
    """(run state, core count) from a memdump of STUB_STATE, or None."""
    for line in dump_lines(text):
        m = re.match(r'^0x([0-9A-Fa-f]{8}):\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})', line)
        if m and int(m.group(1), 16) == STUB_STATE:
            return int(m.group(2), 16), int(m.group(3), 16)
    return None


def stub_holds_title(run_state, cores):
    """True when the debugger stub is set up and is not letting the title run."""
    return 1 <= cores <= 3 and run_state != STUB_RUNNING
