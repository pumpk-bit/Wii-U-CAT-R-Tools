import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import config


class ConfigFileTests(unittest.TestCase):
    """config_file(): CATR_CONFIG_FILE, else HOME's "config_file" pointer (CatR-Tools.exe), else HOME's file."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        for patcher in (patch.object(config, 'HOME', self.home), patch.dict(os.environ)):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop('CATR_CONFIG_FILE', None)

    def write(self, path, values):
        path.write_text(json.dumps(values), 'utf-8')

    def test_home_file_without_pointer(self):
        self.assertEqual(config.config_file(), self.home / 'config.json')

    def test_pointer_to_the_exes_file_is_followed(self):
        exe = Path(self.temp.name) / 'config.json'
        self.write(exe, {'ip': '192.0.2.50'})
        self.write(self.home / 'config.json', {'config_file': str(exe), 'ip': 'stale'})
        self.assertEqual(config.config_file(), exe)
        self.assertEqual(config.load()['ip'], '192.0.2.50')
        config.save({'ip': '192.0.2.51'})
        self.assertEqual(json.loads(exe.read_text('utf-8'))['ip'], '192.0.2.51')
        self.assertEqual(json.loads((self.home / 'config.json').read_text('utf-8'))['ip'], 'stale')

    def test_missing_pointer_target_falls_back(self):
        self.write(self.home / 'config.json', {'config_file': str(Path(self.temp.name) / 'gone.json')})
        self.assertEqual(config.config_file(), self.home / 'config.json')

    def test_variable_wins(self):
        exe = Path(self.temp.name) / 'config.json'
        self.write(exe, {})
        self.write(self.home / 'config.json', {'config_file': str(exe)})
        os.environ['CATR_CONFIG_FILE'] = str(Path(self.temp.name) / 'mine.json')
        self.assertEqual(config.config_file(), Path(self.temp.name) / 'mine.json')

    def test_a_file_with_a_bom_is_read(self):
        (self.home / 'config.json').write_text(json.dumps({'ip': '192.0.2.50'}), 'utf-8-sig')
        self.assertEqual(config.load(), {'ip': '192.0.2.50'})

    def test_save_leaves_no_temporary_file(self):
        config.save({'ip': '192.0.2.50'})
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), ['config.json'])


class CafeSdkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sdk = Path(self.temp.name) / 'sdk'
        (self.sdk / 'system').mkdir(parents=True)
        self.settings = {}
        for patcher in (patch.object(config, 'load', lambda: self.settings), patch.dict(os.environ),
                        patch.object(config, 'HOME', Path(self.temp.name) / 'home')):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop('CAFE_ROOT', None)

    def test_missing_names_the_settings(self):
        with self.assertRaisesRegex(FileNotFoundError, 'CAFE_ROOT'):
            config.cafe_path('system')

    def test_variable_in_windows_and_msys_form(self):
        os.environ['CAFE_ROOT'] = str(self.sdk)
        self.assertEqual(config.cafe_path('system'), (self.sdk, self.sdk / 'system'))
        self.assertEqual(config.windows_path('/c/sdk/x'), Path('C:/sdk/x'))
        self.assertEqual(config.windows_path('/cygdrive/d/sdk'), Path('D:/sdk'))

    def test_setting_wins_and_is_the_only_one(self):
        os.environ['CAFE_ROOT'] = str(self.sdk)
        self.settings['cafe_root'] = str(self.sdk.parent / 'other')
        with self.assertRaises(FileNotFoundError):
            config.cafe_path('system')
        self.settings['cafe_root'] = str(self.sdk)
        os.environ['CAFE_ROOT'] = 'C:\\nowhere'
        self.assertEqual(config.cafe_path('system')[0], self.sdk)


class WorkspaceTests(unittest.TestCase):
    def test_default_is_not_the_repo_itself(self):
        with tempfile.TemporaryDirectory() as home, patch.object(config, 'HOME', Path(home)), patch.dict(os.environ):
            os.environ.pop('CATR_WORKSPACE', None)
            os.environ.pop('CATR_CONFIG_FILE', None)
            self.assertEqual(config._workspace(), config.ROOT.parent / 'workspace')


if __name__ == '__main__':
    unittest.main()
