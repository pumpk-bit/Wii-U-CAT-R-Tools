import os
import stat
import tempfile
import unittest
from pathlib import Path

from catr import stage


class WritableSaveTest(unittest.TestCase):
    def test_clears_read_only_files_in_the_title_save_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            meta = root / 'data/mlc/usr/save/00050000/1f6ff1f0/meta'
            meta.mkdir(parents=True)
            locked = meta / 'meta.xml'
            locked.write_text('<menu/>')
            locked.chmod(stat.S_IREAD)
            (meta / 'saveinfo.xml').write_text('<info/>')
            other = root / 'data/mlc/usr/save/00050000/10c0be00/meta'
            other.mkdir(parents=True)
            untouched = other / 'meta.xml'
            untouched.write_text('<menu/>')
            untouched.chmod(stat.S_IREAD)
            try:
                self.assertEqual(stage.writable_save(root, '000500001F6FF1F0'), ['meta/meta.xml'])
                self.assertTrue(os.access(locked, os.W_OK))
                self.assertFalse(os.access(untouched, os.W_OK))
                self.assertEqual(stage.writable_save(root, '000500001f6ff1f0'), [])
            finally:
                untouched.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_missing_save_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(stage.writable_save(folder, '000500001f6ff1f0'), [])


if __name__ == '__main__':
    unittest.main()
