import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import backup as bk, cli


class FakeConsole:
    """Just enough of transport.wup.Session for backup/restore: a dict of path -> bytes."""

    def __init__(self, files):
        self.files = dict(files)
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def _dirs(self):
        dirs = set()
        for path in self.files:
            parts = path.split('/')
            dirs.update('/'.join(parts[:i]) for i in range(2, len(parts)))
        return dirs

    def stat(self, path):
        if path in self.files:
            return {'directory': False, 'size': len(self.files[path])}
        if path in self._dirs():
            return {'directory': True, 'size': 0}
        raise OSError(f'stat {path}: IOSU result {cli.FS_NOT_FOUND}')

    def listdir(self, path):
        names = {}
        for item in list(self.files) + list(self._dirs()):
            if item.startswith(path + '/') and '/' not in item[len(path) + 1:]:
                names[item[len(path) + 1:]] = item in self._dirs()
        return [{'name': n, 'directory': d} for n, d in sorted(names.items())]

    def read_file(self, path, limit=None):
        if path not in self.files:
            raise OSError(f'open {path}: IOSU result {cli.FS_NOT_FOUND}')
        return self.files[path]

    def write_file(self, path, data):
        self.writes.append(path)
        self.files[path] = bytes(data)

    def makedir(self, path):
        return True

    def title_list(self):
        return [{'title': bk.SCT, 'version': 2561, 'path': bk.SCT_DIR}]


SCT_FILES = {bk.SCT_DIR + '/code/app.xml': b'<app/>', bk.SCT_DIR + '/code/sct.rpx': b'\x7fRPX' * 100,
             bk.SCT_DIR + '/code/sub/x.bin': b'x'}


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.audited = []      # never into the real logs/audit.log
        patcher = patch.object(cli, 'audit', lambda *a, **k: self.audited.append((a, k)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, func, console, **fields):
        args = argparse.Namespace(ip='0.0.0.0', **fields)
        out = io.StringIO()
        with patch.object(cli, 'Session', lambda *a, **k: console), redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = func(args)
        return code, json.loads(out.getvalue())

    def make_backup(self, console):
        folder = self.dir / 'b'
        code, report = self.run_cli(cli.backup, console, set='sct-code', path=None, out=str(folder), quiet=True)
        self.assertEqual(code, 0)
        return folder, report

    def test_backup_manifest_hashes_and_title(self):
        folder, report = self.make_backup(FakeConsole(SCT_FILES))
        manifest = bk.load(folder)
        self.assertEqual(report['files'], 3)
        self.assertEqual(manifest['title']['version'], 2561)
        self.assertEqual([f['path'] for f in manifest['files']], sorted(SCT_FILES))
        self.assertEqual(bk.verify_local(folder, manifest), [])

    def test_damaged_backup_refuses_restore(self):
        folder, _ = self.make_backup(FakeConsole(SCT_FILES))
        bk.local_path(folder, bk.SCT_DIR + '/code/app.xml').write_bytes(b'<changed/>')
        console = FakeConsole({})
        with self.assertRaisesRegex(ValueError, 'damaged'):
            self.run_cli(cli.restore, console, folder=str(folder), to='/vol/storage_mlc01/usr/tmp/catr/r',
                         check=False, unsafe=False, recovery_menu=False, confirm=None, yes=True)
        self.assertEqual(console.writes, [])

    def test_restore_in_place_of_sct_refused(self):
        folder, _ = self.make_backup(FakeConsole(SCT_FILES))
        console = FakeConsole({})
        with self.assertRaisesRegex(ValueError, 'never writes'):
            self.run_cli(cli.restore, console, folder=str(folder), to=None, check=False, unsafe=True,
                         recovery_menu=False, confirm=None, yes=True)
        with self.assertRaisesRegex(ValueError, 'only a recovery-menu backup'):
            self.run_cli(cli.restore, console, folder=str(folder), to=None, check=False, unsafe=False,
                         recovery_menu=True, confirm='restore recovery-menu', yes=True)
        self.assertEqual(console.writes, [])

    def test_restore_to_staging_keeps_folder_names_and_verifies(self):
        folder, _ = self.make_backup(FakeConsole(SCT_FILES))
        console = FakeConsole({})
        staging = '/vol/storage_mlc01/usr/tmp/catr/restore-test'
        code, report = self.run_cli(cli.restore, console, folder=str(folder), to=staging, check=False,
                                    unsafe=False, recovery_menu=False, confirm=None, yes=True)
        self.assertEqual(code, 0)
        self.assertEqual(report['written'], 3)
        self.assertIn(staging + '/code/sub/x.bin', console.files)
        self.assertEqual(console.files[staging + '/code/sct.rpx'], SCT_FILES[bk.SCT_DIR + '/code/sct.rpx'])
        # a second run finds everything identical and writes nothing
        code, report = self.run_cli(cli.restore, console, folder=str(folder), to=staging, check=False,
                                    unsafe=False, recovery_menu=False, confirm=None, yes=True)
        self.assertEqual((code, report['same'], len(console.writes)), (0, 3, 3))

    def test_check_reports_differences_without_writing(self):
        folder, _ = self.make_backup(FakeConsole(SCT_FILES))
        changed = dict(SCT_FILES)
        changed[bk.SCT_DIR + '/code/app.xml'] = b'<other/>'
        console = FakeConsole(changed)
        code, report = self.run_cli(cli.restore, console, folder=str(folder), to=None, check=True, unsafe=False,
                                    recovery_menu=False, confirm=None, yes=False)
        self.assertEqual((code, report['differs'], report['same']), (1, 1, 2))
        self.assertEqual(console.writes, [])

    def test_targets_and_recovery_paths(self):
        manifest = {'roots': [bk.SCT_DIR + '/code', bk.SCT_DIR + '/meta'],
                    'files': [{'path': bk.SCT_DIR + '/meta/meta.xml'}]}
        self.assertEqual(bk.targets(manifest, '/vol/x/y/'), {bk.SCT_DIR + '/meta/meta.xml': '/vol/x/y/meta/meta.xml'})
        self.assertTrue(bk.recovery_restore_path(bk.RECOVERY_DIR + '/code/recovery.rpx'))
        self.assertFalse(bk.recovery_restore_path(bk.RECOVERY_DIR + '/content/x'))
        self.assertFalse(bk.recovery_restore_path(bk.SCT_DIR + '/code/x'))

    def test_manifest_with_traversal_refused(self):
        folder = self.dir / 'evil'
        folder.mkdir()
        (folder / bk.MANIFEST).write_text(json.dumps({'format': 1, 'files': [{'path': '/vol/a/../../x'}]}))
        with self.assertRaisesRegex(ValueError, 'bad console path'):
            bk.load(folder)

    def test_changes_between_crashlog_copies(self):
        old = {'files': [{'path': '/l/1.log', 'sha1': 'a'}, {'path': '/l/2.log', 'sha1': 'b'}]}
        new = {'files': [{'path': '/l/1.log', 'sha1': 'a'}, {'path': '/l/2.log', 'sha1': 'c'}, {'path': '/l/3.log', 'sha1': 'd'}]}
        self.assertEqual(bk.changes(old, new), {'added': ['/l/3.log'], 'changed': ['/l/2.log'], 'removed': []})

    def test_by_title_prefers_console_copy_over_pcfs(self):
        mlc = {'title': bk.RECOVERY, 'device': 'mlc', 'path': bk.RECOVERY_DIR, 'version': 9}
        hfio = {'title': bk.RECOVERY, 'device': 'hfiomlc', 'path': '/vol/storage_hfiomlc01/sys/title/00050010/1f700700', 'version': 9}
        only = {'title': '0005000010c0bef0', 'device': 'hfiomlc', 'path': '/vol/storage_hfiomlc01/usr/title/00050000/10c0bef0'}
        for order in ([mlc, hfio, only], [hfio, mlc, only]):
            chosen = cli.by_title(order)
            self.assertIs(chosen[bk.RECOVERY], mlc)
            self.assertTrue(cli.is_pcfs(chosen['0005000010c0bef0']))

    def test_typed_confirmation(self):
        cli.confirm_typed('q', 'repair recovery-menu', 'repair recovery-menu')
        with self.assertRaises(ValueError):
            cli.confirm_typed('q', 'repair recovery-menu', 'yes')

    def test_repair_refuses_wrong_title_and_recovery_in_front(self):
        package = self.dir / 'pkg'
        package.mkdir()
        tmd = bytearray(0x200)
        tmd[0:4] = (0x10004).to_bytes(4, 'big')
        tmd[0x18C:0x194] = bytes.fromhex(bk.SCT)
        (package / 'title.tmd').write_bytes(bytes(tmd))
        args = argparse.Namespace(package=str(package), confirm='repair recovery-menu', ip='0.0.0.0')
        with self.assertRaisesRegex(ValueError, 'not the Recovery Menu'):
            cli.repair_recovery_menu(args)
        tmd[0x18C:0x194] = bytes.fromhex(bk.RECOVERY)
        tmd[0x1DC:0x1DE] = (9).to_bytes(2, 'big')
        (package / 'title.tmd').write_bytes(bytes(tmd))

        class Console(FakeConsole):
            def title_list(self):
                return [{'title': bk.RECOVERY, 'version': 9, 'path': bk.RECOVERY_DIR}]

            def default_title(self):
                return bk.RECOVERY

            def space(self, volume):
                return {'free': 10 ** 9}
        front = {'title': {'state': 'observed-title', 'title': bk.RECOVERY}}
        with patch.object(cli, 'Session', lambda *a, **k: Console({})), patch.object(cli, 'ensure_service', lambda: front):
            with self.assertRaisesRegex(ValueError, 'another title is in front'):
                cli.repair_recovery_menu(args)


if __name__ == '__main__':
    unittest.main()
