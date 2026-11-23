import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import cli

TID = '000500001f6ff0f0'


class Stop(Exception):
    pass


class RunRemountTests(unittest.TestCase):
    """catr run remounts PCFS when the console's title list lacks a title staged after the mount."""

    def force_for(self, ready, listed):
        seen = {}

        def ensure(*args, force=False, **kwargs):
            seen['force'] = force
            raise Stop

        with tempfile.TemporaryDirectory() as root:
            code = Path(root, 'data/mlc/usr/title', TID[:8], TID[8:], 'code')
            code.mkdir(parents=True)
            (code / 'app.xml').write_text('<app/>', encoding='utf-8')
            args = SimpleNamespace(target=TID, root=root, installed=False, trace=False, tries=1, yes=True,
                                   ip='192.0.2.1', title_id=None, keep_title_id=False, include_debug=False)
            with patch.object(cli, 'console_ip', return_value='192.0.2.1'), \
                    patch.object(cli.stage, 'writable_save', return_value=[]), \
                    patch.object(cli, 'pcfs_ready', return_value=ready), \
                    patch.object(cli, 'pcfs_listed', return_value=listed), \
                    patch.object(cli, 'ensure_pcfs', side_effect=ensure), \
                    self.assertRaises(Stop):
                cli.run(args)
        return seen['force']

    def test_new_title_remounts(self):
        self.assertTrue(self.force_for(ready=({'pid': 1}, {}), listed=False))

    def test_known_title_does_not_remount(self):
        self.assertFalse(self.force_for(ready=({'pid': 1}, {}), listed=True))

    def test_pcfs_down_mounts_normally(self):
        self.assertFalse(self.force_for(ready=None, listed=False))


if __name__ == '__main__':
    unittest.main()
