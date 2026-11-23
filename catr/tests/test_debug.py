import argparse
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from catr import cli, debug as dbg
from catr.safety import action
from shell_replies import MODULES, THREADS


class DebugTests(unittest.TestCase):
    def test_modules_parsed(self):
        modules = dbg.parse_modules(MODULES)
        self.assertEqual(len(modules), 3)
        coreinit = next(m for m in modules if m['name'] == 'coreinit.rpl')
        self.assertEqual(coreinit['code'], (0x0101C400, 0x01090F00))

    def test_threads_parsed(self):
        threads = dbg.parse_threads(THREADS)
        cds = next(t for t in threads if t['name'] == '{SYS CDS Thread}')
        self.assertEqual((cds['state'], cds['symbol']), ('RUNN', 'coreinit.rpl|__OSUnlockScheduler'))
        alarm = next(t for t in threads if t['name'] == '{SYS Alarm Driver Core 0}')
        self.assertEqual((alarm['waiting_on'], dbg.cores(alarm['affinity'])), ('OSAlarmQueue 0x10037D2C', '0'))
        self.assertEqual(len(threads), 4)

    def test_address_gate(self):
        modules = dbg.parse_modules(MODULES)
        self.assertEqual(dbg.check_range(0x0103D2FC, 4, modules), ('coreinit.rpl', 'code'))
        self.assertEqual(dbg.check_range(0x10502200, 64, modules), ('cat_recovery_menu', 'data'))
        for address, size in ((0xDEAD0000, 4), (0x00000000, 4), (0x02016D80, 0x20)):   # last one runs past the end
            with self.assertRaisesRegex(ValueError, 'not sent'):
                dbg.check_range(address, size, modules)
        # memdump shows at least 32 words: 1 word 0x40 bytes before the end still reads past it
        for address, size in ((0x02016D90 - 0x40, max(1, dbg.MEMDUMP_MIN_WORDS) * 4),):
            with self.assertRaisesRegex(ValueError, 'not sent'):
                dbg.check_range(address, size, modules)

    def test_bad_address_sends_only_modules(self):
        sent = []

        def shell(command):
            sent.append(command)
            return MODULES
        args = argparse.Namespace(debug_command='mem', address='0xDEAD0000', words=1)
        with patch.object(cli, 'shell', shell), self.assertRaisesRegex(ValueError, 'not sent'):
            cli.debug(args)
        self.assertEqual(sent, ['cos modules'])

    def test_good_address_sends_getsym(self):
        sent = []

        def shell(command):
            sent.append(command)
            return MODULES if command == 'cos modules' else 'addr: 0x0103D2FC symbol: coreinit.rpl|__OSUnlockScheduler'
        out = io.StringIO()
        with patch.object(cli, 'shell', shell), redirect_stdout(out):
            cli.debug(argparse.Namespace(debug_command='sym', address='0x0103D2FC'))
        self.assertEqual(sent, ['cos modules', 'cos getsym 0x0103d2fc'])
        self.assertIn('__OSUnlockScheduler', out.getvalue())

    def test_dump_rows_without_title_log(self):
        text = ('# 00;01;02;003: 0x031CDE3C: 83FF120C 819F0100 380C0004 91810010           8\n'
                'UnloadTime: 17.730670 ms\n'
                'unity-master|SetBackfaceMode__15GfxDeviceClientFb+0x84\n'
                '0x031cde3c    0x83ff120c    lwz        r31, 0x120c(r31)\n'
                'Unloading 119 unused Assets to reduce memory usage. Loaded Objects now: 816.\n')
        rows = dbg.dump_lines(text)
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0].startswith('0x031CDE3C:'))

    def test_launchex_and_killrestart_wire(self):
        self.assertEqual(action('launchex', '000500001f6ff000', ['mode=test', '-v'])[1],
                         'cos launchex 0x000500001F6FF000 mode=test -v')
        self.assertEqual(action('killrestart')[1], 'cos killrestart')
        for bad in (['two words'], ['a;b'], ["it's"], ['x' * 201]):
            with self.assertRaises(ValueError):
                action('launchex', '000500001f6ff000', bad)


class StubGuardTests(unittest.TestCase):
    def test_stub_state_guard(self):
        text = ('00;13;44;236: 0x1004F9B4: 00000002 00000003 00000000 00000000\n'
                '00;13;44;236: 0x1004F9C4: 00000001 00000000 00000001 00000000\n')
        assert dbg.parse_stub_state(text) == (2, 3)
        assert not dbg.stub_holds_title(2, 3)          # running under the debugger
        assert dbg.stub_holds_title(0, 3)              # all cores in the stub's command loop
        assert dbg.stub_holds_title(3, 3)              # stepping
        assert not dbg.stub_holds_title(0, 0)          # stub never set up (any normal title, ROOT)
        assert dbg.parse_stub_state('0x10000000: 00000000 00000003\n') is None


if __name__ == '__main__':
    unittest.main()

