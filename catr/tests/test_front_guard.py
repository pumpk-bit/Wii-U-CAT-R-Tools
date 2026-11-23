"""The front-title guard in front of every PCFS restart (catr pcfs up, catr run, CatR-Tools).

The logs are in serial_lines.py:
  INCIDENT  boot after a fatal error: ROOT prepares the error viewer (000500301001110A) as master, a
            `cos slowlaunch` lands 1 s after it took the foreground, kernel crash (0xCAFEBADD)
  NORMAL    HBM preload, then ROOT starts the Recovery Menu
  FAST      Recovery Menu launches a title (fast restart)
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from catr import cli
from catr.actions import front_check
from serial_lines import FAST, INCIDENT, NORMAL


def upto(text, marker, include=True):
    at = text.index(marker)
    return text[:text.index('\n', at) + 1] if include else text[:at]


SLOWLAUNCH = 'cos slowlaunch 0x000500101F700700'


class FrontCheckTests(unittest.TestCase):
    def verdict(self, text):
        return front_check(text)['verdict']

    def test_incident_boot_is_never_safe(self):
        before = upto(INCIDENT, SLOWLAUNCH, include=False)       # what catr saw when it sent
        self.assertEqual(self.verdict(upto(before, 'Starting System Log')), 'booting')
        self.assertEqual(self.verdict(upto(before, 'PCFS: No response from PCFSServer')), 'booting')
        starting = front_check(upto(before, 'KiPrepareTitleAsync(0x000500301001110A)'))
        self.assertEqual(starting['verdict'], 'error-viewer')
        self.assertEqual(starting['title'], '000500301001110a')
        front = front_check(before)
        self.assertEqual(front['verdict'], 'error-viewer')
        self.assertTrue(any('PROCESS 6 GAIN FOREGROUND' in line for line in front['seen']))

    def test_crash_after_the_slowlaunch_is_fatal(self):
        conflict = upto(INCIDENT, 'Launch or Call Outstanding')
        self.assertEqual(self.verdict(conflict), 'outstanding')
        crashed = front_check(INCIDENT)
        self.assertEqual(crashed['verdict'], 'fatal')
        self.assertIn('0xCAFEBADD', crashed['seen'][0])

    def test_normal_boot_settles_on_the_recovery_menu(self):
        self.assertEqual(self.verdict(upto(NORMAL, 'KiPrepareTitleAsync(0x0005003010010100)')), 'outstanding')
        self.assertEqual(self.verdict(upto(NORMAL, 'PROCESS 5 GAIN FOREGROUND')), 'booting')   # HBM preload
        self.assertEqual(self.verdict(upto(NORMAL, 'ROOT IS STARTING MASTER TITLE')), 'root-transition')
        self.assertEqual(self.verdict(upto(NORMAL, 'KiPrepareTitleAsync(0x000500101F700700)')), 'outstanding')
        done = front_check(NORMAL)
        self.assertEqual((done['verdict'], done['title']), ('title', '000500101f700700'))

    def test_launch_from_the_recovery_menu(self):
        self.assertEqual(self.verdict(upto(FAST, 'ROOT: Issuing reboot.')), 'outstanding')
        self.assertEqual(self.verdict(upto(FAST, 'PPC USER-LEVEL FAST RESTART')), 'booting')
        done = front_check(FAST)
        self.assertEqual((done['verdict'], done['title']), ('title', '000500301001710a'))

    def test_root_and_unknown(self):
        self.assertEqual(self.verdict(NORMAL + '00;01;00;000: ------------------PROCESS 2 LOST FOREGROUND---\n'
                                      '00;01;00;000: ------------------PROCESS 1 GAIN FOREGROUND---\n'),
                         'root-transition')
        self.assertEqual(self.verdict(NORMAL + 'MASTER TITLE KILLED\nWAITING IN ROOT\n'), 'root')
        self.assertEqual(self.verdict('00;00;31;566: CAT Recovery Menu v0.9 starting\n'), 'unknown')

    def test_shutdown_after_fatal_error_stays_fatal(self):
        text = NORMAL + ('00:02:04:936: MCP: life time number of fatal errors - 232\n'
                         '***PPC TOLD TO SHUT DOWN (m1,f8)***\n')
        self.assertEqual(self.verdict(text), 'fatal')
        self.assertEqual(self.verdict(text + '----------- Starting System Log -----------\n'), 'booting')


class EnsurePcfsGuardTests(unittest.TestCase):
    """ensure_pcfs must refuse (or wait) before sending anything; no live commands here."""

    def run_ensure(self, logs, clock=None):
        sent, logs = [], iter(logs)
        last = [None]

        def rpc(request, timeout=None):
            if request['op'] == 'log':
                last[0] = next(logs, last[0])
                return {'text': last[0], 'cursor': 0, 'gap': False, 'error': None}
            sent.append(request)
            raise AssertionError(f'sent {request} to the console')

        patches = [patch.object(cli.serial, 'rpc', rpc),
                   patch.object(cli, 'ensure_service', lambda: {'title': {'state': 'unknown'}}),
                   patch.object(cli, 'ping', lambda ip: True),
                   patch.object(cli, 'confirm', lambda *a: None),
                   patch.object(cli.pcfs.Server, 'check_conflicts', lambda self: None),
                   patch.object(cli.time, 'sleep', lambda s: None)]
        if clock:
            patches.append(patch.object(cli.time, 'monotonic', clock))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with patch('sys.stderr'):
            try:
                result = cli.ensure_pcfs('192.0.2.1', force=True, yes=True)
            except AssertionError:
                result = None
        return result, sent

    def test_refuses_the_incident_without_sending(self):
        (ok, report), sent = self.run_ensure([upto(INCIDENT, SLOWLAUNCH, include=False)])
        self.assertFalse(ok)
        self.assertEqual(sent, [])
        self.assertEqual(report['outcome'], 'refused')
        self.assertIsNone(report['sent'])
        self.assertIn('error viewer', report['reason'])
        self.assertIn('000500301001110a', report['reason'])
        self.assertIn('Dismiss the error', report['next'])
        self.assertIn('power-cycle', report['next'])
        self.assertTrue(report['seen'])

    def test_waits_through_boot_then_refuses_the_error_viewer(self):
        before = upto(INCIDENT, SLOWLAUNCH, include=False)
        logs = [upto(before, 'Starting System Log'), upto(before, 'ROOT: OSID 0x000500101000400A'), before]
        (ok, report), sent = self.run_ensure(logs)
        self.assertFalse(ok)
        self.assertEqual(sent, [])
        self.assertIn('error viewer', report['reason'])

    def test_refuses_after_a_crash(self):
        (ok, report), sent = self.run_ensure([INCIDENT])
        self.assertFalse(ok)
        self.assertEqual(sent, [])
        self.assertIn('fatal', report['reason'])
        self.assertIn('power-cycle', report['next'])

    def test_gives_up_when_a_launch_never_settles(self):
        ticks = iter(range(0, 1000, 10))
        (ok, report), sent = self.run_ensure([upto(NORMAL, 'KiPrepareTitleAsync(0x000500101F700700)')],
                                             clock=lambda: next(ticks))
        self.assertFalse(ok)
        self.assertEqual(sent, [])
        self.assertIn('outstanding', report['reason'])
        self.assertIn('still after', report['reason'])

    def test_settled_recovery_menu_goes_on_to_send(self):
        # The guard passes; the first send is reached (and stopped by the fake rpc).
        result, sent = self.run_ensure([upto(NORMAL, 'ROOT IS STARTING MASTER TITLE'), NORMAL])
        self.assertIsNone(result)
        self.assertEqual(sent[0]['action'], 'slowlaunch')


if __name__ == '__main__':
    unittest.main()
