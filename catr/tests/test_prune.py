import errno
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import pcfs

DAY = 86400
ONE_LINE = b'Connecting PCFS host to console at 192.0.2.1:7500\n'
REAL = ONE_LINE + b'Ping complete\nAttached volume\n'


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.now = time.time()
        self.state = self.folder / 'pcfs.json'
        patcher = patch.object(pcfs, 'STATE_FILE', self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def log(self, name, content, days_old):
        path = self.folder / name
        path.write_bytes(content)
        stamp = self.now - days_old * DAY
        os.utime(path, (stamp, stamp))
        return path

    def prune(self):
        return pcfs.prune_logs(self.folder, now=self.now)

    def test_one_line_logs_go_after_a_day(self):
        old = self.log('20261120-100000-000001.log', ONE_LINE, 2)
        fresh = self.log('20261122-100000-000001.log', ONE_LINE, 0.5)
        self.assertEqual(self.prune(), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_logs_with_content_stay_until_30_days(self):
        recent = self.log('20261115-100000-000001.log', REAL, 10)
        ancient = self.log('20261001-100000-000001.log', REAL, 40)
        self.assertEqual(self.prune(), 1)
        self.assertTrue(recent.exists())
        self.assertFalse(ancient.exists())

    def test_newest_keeper_log_is_kept_even_when_old(self):
        older = self.log('keep-20260901-100000.log', REAL, 80)
        newest = self.log('keep-20261001-100000.log', REAL, 50)
        self.prune()
        self.assertFalse(older.exists())
        self.assertTrue(newest.exists())

    def test_short_keeper_log_is_not_a_dial_log(self):
        keeper = self.log('keep-20261120-100000.log', b'', 5)
        other = self.log('keep-20261121-100000.log', b'', 3)
        self.prune()
        self.assertTrue(keeper.exists())     # empty, but only the 30-day rule applies to keeper logs
        self.assertTrue(other.exists())

    def test_running_servers_log_is_kept(self):
        running = self.log('20261120-100000-000001.log', ONE_LINE, 3)
        self.state.write_text(json.dumps({'pid': 1, 'log': str(running)}), encoding='utf-8')
        self.assertEqual(self.prune(), 0)
        self.assertTrue(running.exists())

    def test_other_files_are_left_alone(self):
        note = self.folder / 'notes.txt'
        note.write_text('x')
        os.utime(note, (self.now - 90 * DAY,) * 2)
        self.prune()
        self.assertTrue(note.exists())

    def test_missing_folder(self):
        self.assertEqual(pcfs.prune_logs(self.folder / 'nope', now=self.now), 0)


    def test_a_log_removed_meanwhile_is_ignored(self):
        self.log('keep-20261101-100000.log', REAL, 40)
        gone = self.folder / 'keep-20261102-100000.log'
        real_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path == gone:
                raise FileNotFoundError(errno.ENOENT, 'gone', str(path))
            return real_stat(path, *args, **kwargs)
        self.log(gone.name, REAL, 40)
        with patch.object(Path, 'stat', stat):
            self.prune()

    def test_prune_once_never_raises(self):
        with patch.object(pcfs, '_pruned', False), patch.object(pcfs, 'prune_logs', side_effect=PermissionError('busy')):
            pcfs.prune_logs_once()


if __name__ == '__main__':
    unittest.main()
