"""--trace must not silently reuse a server that was started without PCFS_TRACE."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import cli


class Restarted(Exception):
    pass


class TraceReuseTests(unittest.TestCase):
    def ensure(self, registered, trace):
        title = {'state': 'observed-title', 'title': cli.RECOVERY}

        def past_reuse():
            raise Restarted()       # ensure_pcfs went on to restart the console

        with patch.object(cli.pcfs.Server, 'check_conflicts', lambda self: None), \
                patch.object(cli, 'pcfs_ready', lambda need: (registered, title)), \
                patch.object(cli, 'ensure_service', past_reuse), patch('sys.stderr'):
            return cli.ensure_pcfs('192.0.2.1', trace=trace, yes=True, need_recovery=True)

    def test_untraced_server_reused_without_trace(self):
        ok, report = self.ensure({'pid': 1, 'root': '.', 'trace': False}, trace=False)
        self.assertTrue(ok)
        self.assertEqual(report['outcome'], 'already-up')

    def test_untraced_server_not_reused_for_trace(self):
        with self.assertRaises(Restarted):
            self.ensure({'pid': 1, 'root': '.'}, trace=True)     # pcfs.json from before 'trace' was recorded

    def test_traced_server_reused_for_trace(self):
        ok, report = self.ensure({'pid': 1, 'root': '.', 'trace': True}, trace=True)
        self.assertEqual(report['outcome'], 'already-up')

    def test_restart_wait_outlasts_a_slow_restart(self):
        self.assertGreater(cli.RESTART_WAIT, 105)


if __name__ == '__main__':
    unittest.main()
