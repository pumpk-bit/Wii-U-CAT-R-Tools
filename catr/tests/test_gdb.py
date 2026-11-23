import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from catr import gdbserver as gs
from shell_replies import CONTEXT, CTXDUMP, HEAPS, MEMSTACK, MODULES, STACK, THREADS


def memdump_reply(start, words):
    """A memdump reply in the console's format: 4 words per row; word value = its own address."""
    rows = []
    for row in range(start, start + max(words, 32) * 4, 16):
        values = ' '.join(f'{row + i * 4:08X}' for i in range(4))
        rows.append(f'00;11;12;483: 0x{row:08X}: {values}  ................\r\n')
    return (f'cos memdump {start:#010x} {words}\r\n# 00;11;12;483: \r\n---- COS Debugging Shell Command: memdump ----\r\n'
            + ''.join(rows) + '\r\n[outcome: observed; address; sent once, never resent]\r\n')


class FakeShell:
    def __init__(self):
        self.sent = []

    def __call__(self, command):
        self.sent.append(command)
        word = command.split()[1]
        if word == 'memdump':
            return memdump_reply(int(command.split()[2], 16), int(command.split()[3]))
        if word == 'getsym':
            return f'addr: {command.split()[2][2:].upper()} symbol: coreinit.rpl|OSSleepThread+0x38'
        return {'modules': MODULES, 'heaps': HEAPS, 'threads': THREADS, 'ctxdump': CTXDUMP}[word]


class FakeSocket:
    def __init__(self, incoming):
        self.incoming, self.out = list(incoming), b''

    def recv(self, size):
        return self.incoming.pop(0) if self.incoming else b''

    def sendall(self, data):
        self.out += data


def replies(raw):
    out, i = [], 0
    while True:
        start = raw.find(b'$', i)
        if start < 0:
            return out
        end = raw.find(b'#', start)
        out.append(raw[start + 1:end].decode('latin-1'))
        i = end + 3


class ParserTests(unittest.TestCase):
    def test_heaps_and_readable_ranges(self):
        heaps = gs.parse_heaps(HEAPS)
        self.assertIn((0x1057B000, 0x41FA0000), heaps)
        self.assertEqual(len(heaps), 3)
        ranges = gs.readable_ranges(gs.dbg.parse_modules(MODULES), heaps)
        self.assertIsNotNone(gs.block_start(STACK, ranges))          # stacks sit below the first heap
        self.assertEqual(gs.block_start(STACK, ranges), 0x1055EC00)
        self.assertIsNone(gs.block_start(0xDEAD0000, ranges))        # the 2026-11-14 kernel crash
        self.assertIsNone(gs.block_start(0x0FFFF000, ranges))        # code area outside any module
        self.assertIsNotNone(gs.block_start(0x0101C400, ranges))     # coreinit code

    def test_block_never_crosses_a_range_end(self):
        ranges = [(0x1000, 0x1100)]
        self.assertEqual(gs.block_start(0x10F0, ranges), 0x1080)
        self.assertIsNone(gs.block_start(0x1000, [(0x1000, 0x1040)]))   # smaller than one memdump

    def test_memdump_rows(self):
        data = gs.parse_memdump(MEMSTACK, STACK)
        self.assertEqual(len(data), 128)
        self.assertEqual(struct.unpack('>II', data[:8]), (0x1055EC78, 0x0103D2FC))
        with self.assertRaisesRegex(OSError, 'missing'):
            gs.parse_memdump(MEMSTACK, STACK - 16)

    def test_ctxdump_registers(self):
        regs = gs.parse_ctxdump(CTXDUMP)
        self.assertEqual((regs['r1'], regs['pc'], regs['lr'], regs['msr'], regs['cr']),
                         (STACK, 0x0103D2FC, 0x0103D2FC, 0x00007072, 0x20000022))
        self.assertEqual(regs['r31'], 0)
        self.assertEqual(regs['f0'], 0x3F911570C0000000)
        encoded = gs.encode_registers(regs)
        self.assertEqual(len(encoded), (32 * 4 + 32 * 8 + 7 * 4) * 2)
        self.assertEqual(encoded[8:16], f'{STACK:08x}')                 # r1
        pc_at = (32 * 4 + 32 * 8) * 2
        self.assertEqual(encoded[pc_at:pc_at + 8], '0103d2fc')
        self.assertEqual(encoded[-24:], 'x' * 24)                       # ctr, xer, fpscr unknown

    def test_target_xml_order_matches_g_packet(self):
        xml = gs.target_xml()
        self.assertIn('regnum="64"', xml)
        self.assertEqual([name for name, _ in gs.REGISTERS][64:], ['pc', 'msr', 'cr', 'lr', 'ctr', 'xer', 'fpscr'])


class SessionTests(unittest.TestCase):
    def run_session(self, packets):
        shell = FakeShell()
        sock = FakeSocket([b''.join(p if isinstance(p, bytes) else gs.frame(p) for p in packets)])
        gs.Session(sock, lambda: gs.Target(shell), log=lambda text: None).serve()
        return replies(sock.out), shell

    def test_attach_threads_registers_memory(self):
        out, shell = self.run_session(['qSupported:xmlRegisters=i386', '?', 'qfThreadInfo', 'qsThreadInfo',
                                       f'Hg{CONTEXT:x}', 'g', f'm{STACK:x},8', 'D'])
        self.assertIn('qXfer:features:read+', out[0])
        self.assertTrue(out[1].startswith('T02thread:'))
        self.assertIn(f'{CONTEXT:x}', out[2].split('m', 1)[1].split(','))
        self.assertEqual(out[3], 'l')
        self.assertEqual(out[4], 'OK')
        self.assertEqual(out[5][8:16], f'{STACK:08x}')
        self.assertEqual(out[6], f'{STACK:08x}{STACK + 4:08x}')        # fake memory: word = address
        self.assertEqual(out[7], 'OK')
        self.assertIn(f'cos ctxdump {CONTEXT:#010x}', shell.sent)
        self.assertIn('cos memdump 0x1055ec00 32', shell.sent)

    def test_bad_address_sends_nothing(self):
        out, shell = self.run_session(['mdead0000,4', 'm0fff0000,4', 'D'])
        self.assertEqual(out[:2], ['E14', 'E14'])
        self.assertFalse([c for c in shell.sent if 'memdump' in c])

    def test_writes_breakpoints_steps_refused(self):
        out, shell = self.run_session(['M1055ec58,4:00000000', 'Z0,2001a3c,4', 's', 'vCont;s', 'G00', 'D'])
        self.assertEqual(out[:5], ['E01'] * 5)
        self.assertFalse([c for c in shell.sent if 'setword' in c or 'setiabr' in c])

    def test_continue_then_interrupt_takes_new_snapshot(self):
        out, shell = self.run_session(['c', b'\x03', 'D'])
        self.assertTrue(out[0].startswith('T02thread:'))
        self.assertEqual(shell.sent.count('cos threads'), 2)

    def test_reads_cached_per_snapshot(self):
        out, shell = self.run_session([f'm{STACK:x},4', f'm{STACK + 8:x},4', 'D'])
        self.assertEqual(sum('memdump' in c for c in shell.sent), 1)

    def test_target_xml_transfer_and_monitor(self):
        xml = gs.target_xml()
        out, _ = self.run_session([f'qXfer:features:read:target.xml:0,{len(xml) + 10:x}', 'qRcmd,' + b'ranges'.hex(), 'D'])
        self.assertEqual(out[0], 'l' + xml)
        console = [p for p in out[1:] if p.startswith('O') and p != 'OK']
        self.assertTrue(console)
        self.assertIn('0x10000000-', ''.join(bytes.fromhex(p[1:]).decode() for p in console))
        self.assertEqual(out[1 + len(console)], 'OK')

    def test_bad_checksum_is_nacked(self):
        sock = FakeSocket([b'$?#00', gs.frame('D')])
        gs.Session(sock, lambda: gs.Target(FakeShell()), log=lambda text: None).serve()
        self.assertTrue(sock.out.startswith(b'-+'))


if __name__ == '__main__':
    unittest.main()
