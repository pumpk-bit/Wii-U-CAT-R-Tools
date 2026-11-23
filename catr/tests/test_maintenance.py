import argparse
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import cli
from catr.safety import RECOVERY, SCT


class Console:
    def __init__(self, titles):
        self.titles, self.default = titles, SCT

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def default_title(self):
        return self.default

    def set_default_title(self, tid):
        self.default = tid
        return tid

    def title_list(self):
        return []


class MaintenanceEndTests(unittest.TestCase):
    def end(self, titles):
        console = Console(titles)
        args = argparse.Namespace(target='end', ip='127.0.0.1', yes=True, no_reboot=True)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(cli, 'MAINTENANCE_FILE', Path(folder) / 'maintenance.json'), \
                patch.object(cli, 'Session', lambda *a, **k: console), \
                patch.object(cli, 'console_ip', lambda args: args.ip), \
                patch.object(cli, 'by_title', lambda listed: {tid: {} for tid in titles}), \
                patch.object(cli, 'is_pcfs', lambda title: False), \
                patch.object(cli, 'audit', lambda *a, **k: None), redirect_stdout(io.StringIO()):
            try:
                cli.maintenance(args)
            except ValueError as error:
                return console.default, str(error)
        return console.default, None

    def test_refuses_a_menu_that_is_not_installed(self):
        default, error = self.end([SCT])
        self.assertEqual(default, SCT)
        self.assertIn('not installed', error)

    def test_sets_an_installed_menu(self):
        self.assertEqual(self.end([SCT, RECOVERY]), (RECOVERY, None))


if __name__ == '__main__':
    unittest.main()
