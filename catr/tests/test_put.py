import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from catr import cli  # noqa: E402
from test_backup import FakeConsole  # noqa: E402

STAGING = '/vol/storage_mlc01/usr/tmp/catr/install/img'


class PutConsole(FakeConsole):
    def __init__(self, files=(), truncate=False):
        super().__init__(dict(files))
        self.reads = []
        self.truncate = truncate

    def exists(self, path):
        return path in self.files

    def write_file(self, path, data):
        super().write_file(path, data[:-1] if self.truncate else data)

    def read_file(self, path, limit=None):
        self.reads.append(path)
        return super().read_file(path, limit)


class PutReadback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / 'img'
        self.src.mkdir()
        (self.src / 'title.tmd').write_bytes(b'tmd' * 10)
        (self.src / '00000000.app').write_bytes(b'app' * 1000)
        patcher = patch.object(cli, 'audit', lambda *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_put(self, console, **extra):
        args = argparse.Namespace(source=str(self.src), dest=STAGING, recursive=True, force=True,
                                  unsafe=False, yes=True, ip='192.0.2.1', **extra)
        out = io.StringIO()
        with patch.object(cli, 'Session', lambda ip, timeout=3: console), redirect_stdout(out), \
                redirect_stderr(io.StringIO()):
            cli.put(args)
        return json.loads(out.getvalue())

    def test_default_reads_every_file_back(self):
        console = PutConsole()
        report = self.run_put(console)
        self.assertEqual(len(console.reads), 2)
        self.assertEqual({c['check'] for c in report['copied']}, {'read-back'})

    def test_install_upload_checks_sizes_but_reads_back_tmd(self):
        console = PutConsole()
        report = self.run_put(console, readback=False)
        self.assertEqual(console.reads, [STAGING + '/title.tmd'])
        checks = {Path(c['dest']).name: c['check'] for c in report['copied']}
        self.assertEqual(checks, {'title.tmd': 'read-back', '00000000.app': 'size'})
        self.assertEqual(console.files[STAGING + '/00000000.app'], b'app' * 1000)

    def test_size_mismatch_stops(self):
        console = PutConsole(truncate=True)
        with self.assertRaises(OSError):
            self.run_put(console, readback=False)


if __name__ == '__main__':
    unittest.main()
