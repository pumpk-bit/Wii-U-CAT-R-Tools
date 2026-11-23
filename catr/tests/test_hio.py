import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import hiobridge as hb  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class FakeConsole:
    """hio_socket.c's side: name line -> '+\\n' or '-\\n', then a raw channel stream."""

    def __init__(self, channels):
        self.channels = set(channels)
        self.server = socket.socket()
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.links = {}
        self.connected = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            name = b''
            while not name.endswith(b'\n'):
                name += client.recv(1)
            name = name[:-1].decode()
            if name not in self.channels:
                client.sendall(b'-\n')
                client.close()
                continue
            client.sendall(b'+\n')
            self.links[name] = client
            self.connected.set()

    def close(self):
        self.server.close()
        for s in self.links.values():
            s.close()


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class RegistryFormat(unittest.TestCase):
    def test_records_are_packed_without_separators(self):
        records = hb.registry_records([('SparkCapture', 6201, True), ('SparkPlay005', 6202, False)])
        self.assertEqual(records, b'+SparkCapture:6201-SparkPlay005:6202')

    def test_windows_paths(self):
        self.assertEqual(hb.windows_path('/cygdrive/c/Temp/a.4mp'), Path('C:\\Temp\\a.4mp'))
        self.assertEqual(hb.windows_path('C:\\Temp\\a.4mp'), Path('C:\\Temp\\a.4mp'))
        self.assertEqual(hb.windows_path('D:/x/y.4mp'), Path('D:\\x\\y.4mp'))
        self.assertIsNone(hb.windows_path('/vol/spark/a.4mp'))


class BridgeEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.save = Path(self.tmp.name) / 'save'
        self.console = FakeConsole(['SparkCapture'])
        self.registry_port = free_port()
        self.capture_port = free_port()
        self.play_port = free_port()
        self.lines = []
        self.bridge = hb.Bridge('127.0.0.1', console_port=self.console.port,
                                registry_port=self.registry_port,
                                channels={'SparkCapture': self.capture_port, 'SparkPlay005': self.play_port},
                                save_dir=self.save, log=self.lines.append)
        self.bridge.start()
        self.assertTrue(self.console.connected.wait(5))

    def tearDown(self):
        self.bridge.stop()
        self.console.close()
        self.tmp.cleanup()

    def registry(self):
        with socket.create_connection(('127.0.0.1', self.registry_port)) as s:
            data = b''
            while True:
                chunk = s.recv(100)
                if not chunk:
                    return data
                data += chunk

    def test_registry_reports_open_channel_only(self):
        self.assertTrue(wait_for(lambda: self.bridge.channels[0].up))
        self.assertEqual(self.registry(),
                         f'+SparkCapture:{self.capture_port}-SparkPlay005:{self.play_port}'.encode())

    def test_capture_path_rewritten_and_file_copied(self):
        spark = socket.create_connection(('127.0.0.1', self.capture_port))
        self.assertTrue(wait_for(lambda: self.bridge.channels[0].spark is not None))
        wanted = Path(self.tmp.name) / 'host' / 'cap.4mp'
        spark.sendall(hb.frame(hb.OPTION, struct.pack('>II', 0, 1)))
        spark.sendall(hb.frame(hb.CAPTURE, str(wanted).encode() + b'\0'))

        link = self.console.links['SparkCapture']
        self.assertEqual(hb.read_message(link), (hb.OPTION, struct.pack('>II', 0, 1)))
        kind, payload = hb.read_message(link)
        self.assertEqual(kind, hb.CAPTURE)
        self.assertEqual(payload, b'/cygdrive/%SAVE_EMU_DIR/spark/hio_capture.4mp\0')

        # The console writes the file over PCFS, reports progress, then completion.
        (self.save / 'spark' / 'hio_capture.4mp').write_bytes(b'PM4\nframe')
        link.sendall(hb.frame(hb.PROGRESS, struct.pack('>Q', 9)))
        link.sendall(hb.frame(hb.COMPLETE))
        spark.settimeout(5)
        self.assertEqual(hb.read_message(spark), (hb.PROGRESS, struct.pack('>Q', 9)))
        self.assertEqual(hb.read_message(spark), (hb.COMPLETE, b''))
        self.assertEqual(wanted.read_bytes(), b'PM4\nframe')   # in place before SPARK saw COMPLETE
        spark.close()

    def test_save_dir_path_passes_unchanged(self):
        spark = socket.create_connection(('127.0.0.1', self.capture_port))
        self.assertTrue(wait_for(lambda: self.bridge.channels[0].spark is not None))
        path = b'/cygdrive/%SAVE_EMU_DIR/spark/mine.4mp\0'
        spark.sendall(hb.frame(hb.CAPTURE, path))
        self.assertEqual(hb.read_message(self.console.links['SparkCapture']), (hb.CAPTURE, path))
        spark.close()

    def test_spark_refused_while_channel_down(self):
        with socket.create_connection(('127.0.0.1', self.play_port)) as s:
            s.settimeout(5)
            self.assertEqual(s.recv(10), b'')

    def test_console_drop_marks_channel_down_and_closes_spark(self):
        spark = socket.create_connection(('127.0.0.1', self.capture_port))
        self.assertTrue(wait_for(lambda: self.bridge.channels[0].spark is not None))
        self.console.connected.clear()
        self.console.links['SparkCapture'].close()
        spark.settimeout(5)
        try:
            self.assertEqual(spark.recv(10), b'')
        except ConnectionResetError:
            pass   # Windows may report the close as a reset
        self.assertTrue(wait_for(lambda: self.registry().startswith(b'-SparkCapture')))
        spark.close()


if __name__ == '__main__':
    unittest.main()
