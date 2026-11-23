import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import cli
from catr.actions import stuck_shutdown

# From the 2026-11-14 serial log after error 160-0101 (slowlaunch sent from the error screen).
STALLED = ('cos slowlaunch 0x000500101F700700\n'
           '00;04;23;526: DIAG:Issued KiPrepareTitleAsync(0x000500101F700700)\n'
           '00:04:23:675: MCP: Power Transition -> PM_RESTART, flags 0x0\n'
           '00;04;23;556: DIAG:Result PrepareTitle(0x000500101F700700) came back with cosxml(0x0000000000000000)\n'
           '00;04;23;556: PrepareTitle(Relaunch) Succeeded.  IOS should have or will be telling us to shut down.\n'
           '00;04;23;556: ***PPC TOLD TO SHUT DOWN (m8,f0)***\n'
           '00;04;23;556: SHUTINFO: RAMPID 6 is in Foreground\n'
           '00;04;23;556: SHUTINFO: RAMPID 0 is also alive.\n'
           '00:05:24:207: BT: WPAD registration took >5 minutes -- disconnecting all Wiiremotes\n')
NORMAL = STALLED.split('00:05:24')[0] + ('----------- Starting System Log -----------\n'
                                         '00;00;11;071: TitleId 0x000500101F700700 Version 0x00000009 Built On SDK 21213\n')


class UnstickTests(unittest.TestCase):
    def test_detects_only_a_stalled_shutdown(self):
        self.assertTrue(stuck_shutdown(STALLED))
        self.assertFalse(stuck_shutdown(NORMAL))
        self.assertFalse(stuck_shutdown('00;00;31;566: CAT Recovery Menu v0.9 starting\n'))

    def run_stalled(self, log):
        sent = []

        def rpc(request, timeout=None):
            sent.append(request)
            return {'text': log}
        with patch.object(cli.serial, 'rpc', rpc):
            report = cli.stalled_shutdown({'begin': 0})
        return report, [r for r in sent if r['op'] != 'log']

    def test_normal_restart_is_not_a_stall(self):
        self.assertEqual(self.run_stalled(NORMAL), (None, []))

    def test_stall_is_reported_and_nothing_is_sent(self):
        # 2026-11-15: cos kill answered "kill command only works when game is in foreground."
        report, sent = self.run_stalled(STALLED)
        self.assertEqual(sent, [])
        self.assertEqual(report['next'], 'power-cycle the console')

    def test_recovery_mode_boot_has_no_ppc_lines(self):
        from catr.actions import iosu_only_boot
        recovery = ('----------- Starting System Log -----------\n'
                    '00:00:07:295: PCFSTCP[V11]: selected native TCP transport=3 port=7500\n'
                    '00:00:13:611: CDC_SIGNAL_STA_ASSOCIATED_NOTIFY\n# # cos sdkversion\n#')
        self.assertTrue(iosu_only_boot(recovery))
        self.assertFalse(iosu_only_boot(NORMAL + '00;00;31;566: CAT Recovery Menu v0.9 starting\n'))
        # an earlier normal boot does not count, only the latest one
        self.assertTrue(iosu_only_boot('00;00;31;566: menu\n' + recovery))


if __name__ == '__main__':
    unittest.main()
