import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import pcfs

BOOT_TO_WAIT = ('MCP: Boot PM flags - PON_RESTART\r'
                'PCFSTCP[V11]: selected native TCP transport=3 port=7500\r'
                'PCFS: Waiting for PC connection on port 7500...\r')


class KeeperTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        for name, value in (('LEASE_FILE', home / 'lease.json'), ('KEEP_FILE', home / 'keep.json'),
                            ('STATE_FILE', home / 'pcfs.json')):
            patcher = patch.object(pcfs, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_pid_alive(self):
        self.assertTrue(pcfs.pid_alive(os.getpid()))
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait()
        self.assertFalse(pcfs.pid_alive(child.pid))
        self.assertFalse(pcfs.pid_alive(None))

    def test_one_pcfs_folder(self):
        from catr import config
        with tempfile.TemporaryDirectory() as home, patch.object(config, 'HOME', Path(home)), \
                patch.dict(os.environ):
            os.environ.pop('CATR_CONFIG_FILE', None)    # never write to the user's own settings
            self.assertEqual(pcfs.host_root(), pcfs.FALLBACK_ROOT)
            self.assertEqual(pcfs.checked_root(None), pcfs.FALLBACK_ROOT)
            self.assertEqual(pcfs.checked_root(str(pcfs.FALLBACK_ROOT) + '\\'), pcfs.FALLBACK_ROOT)
            with self.assertRaisesRegex(ValueError, 'one PCFS folder'):
                pcfs.checked_root(home)
            pcfs.set_host_root(home)
            self.assertTrue(pcfs.same_folder(pcfs.host_root(), home))
            self.assertTrue(pcfs.same_folder(pcfs.Server('192.0.2.1').root, home))
            with self.assertRaises(FileNotFoundError):
                pcfs.set_host_root(Path(home) / 'missing')
            pcfs.KEEP_FILE.write_text('{"pid": %d, "created": %d, "root": "C:\\\\elsewhere"}'
                                      % (os.getpid(), pcfs.own_record()['created']), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'keeper'):
                pcfs.set_host_root(Path(home).parent)

    def test_reused_pid_is_not_a_holder(self):
        # A live process that is not the recorded one: our parent shell, with a wrong creation time.
        other = os.getppid()
        pcfs.LEASE_FILE.write_text('{"pid": %d, "created": 1}' % other, encoding='utf-8')
        self.assertIsNone(pcfs.lease_holder())
        record = pcfs.own_record()
        self.assertIsNotNone(record['created'])
        self.assertTrue(pcfs.recorded_alive(record))
        self.assertFalse(pcfs.recorded_alive({'pid': record['pid'], 'created': record['created'] + 1}))
        system = next((p for p in (4,) if pcfs.pid_alive(p)), None)
        if system:                                       # older lease file, PID now a non-Python process
            self.assertFalse(pcfs.recorded_alive({'pid': system}))

    def test_lease_is_ours_or_anothers(self):
        self.assertIsNone(pcfs.lease_holder())
        pcfs.take_lease()
        self.assertIsNone(pcfs.lease_holder())          # our own lease does not pause us
        pcfs.release_lease()
        self.assertFalse(pcfs.LEASE_FILE.exists())
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(lambda: (child.kill(), child.wait()))
        created = pcfs.process_identity(child.pid)[0]
        pcfs.LEASE_FILE.write_text('{"pid": %d, "created": %d}' % (child.pid, created), encoding='utf-8')
        self.assertEqual(pcfs.lease_holder(), child.pid)
        with self.assertRaisesRegex(RuntimeError, 'driving PCFS'):
            pcfs.take_lease()
        pcfs.release_lease()                             # not ours: left alone
        self.assertTrue(pcfs.LEASE_FILE.exists())
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait()
        pcfs.LEASE_FILE.write_text(f'{{"pid": {child.pid}}}', encoding='utf-8')
        self.assertIsNone(pcfs.lease_holder())           # a crashed command's lease is stale

    def test_tick_adopts_registered_server(self):
        """A keeper that did not start the running server checks it instead of starting a second."""
        server = pcfs.Server('192.0.2.1')
        boot = pcfs.PcfsBoot()
        boot.feed(BOOT_TO_WAIT)
        with patch.object(pcfs.Server, 'registered', staticmethod(lambda: {'pid': 1234, 'ip': '192.0.2.1'})), \
             patch.object(pcfs, 'tcp_state', lambda pid, ip: 'ESTABLISHED' if pid == 1234 else None), \
             patch.object(pcfs.Server, 'start', lambda self: self.fail('started a second server')):
            server.tick(boot)
        with patch.object(pcfs.Server, 'registered', staticmethod(lambda: None)), \
             patch.object(pcfs.Server, 'start', lambda self: setattr(self, 'started', True)):
            server.tick(boot)
        self.assertTrue(server.started)

    def dialing_server(self, started_at=100.0, syn_since=100.2):
        class Process:
            pid = 4321
            def poll(self):
                return None
        server = pcfs.Server('192.0.2.1')
        server.process, server.started_at, server.syn_since = Process(), started_at, syn_since
        return server

    def boot_in(self, text):
        boot = pcfs.PcfsBoot()
        boot.feed(text)
        return boot

    def test_dialing_server_replaced_only_between_syns(self):
        """Hang 2: never at the 3 s (or any other) SYN retransmit, only in the gaps."""
        server = self.dialing_server()
        transport = self.boot_in('MCP: Boot PM flags - PON_RESTART\r'
                                 'PCFSTCP[V11]: selected native TCP transport=3 port=7500\r')
        self.assertEqual(transport.state, 'transport')
        for age, expected in ((1.0, False), (3.0, False), (3.2, False), (3.7, True), (6.0, True),
                              (6.6, False), (7.0, False), (7.9, True), (9.0, False), (15.0, False)):
            with self.subTest(age=age):
                self.assertEqual(server.may_replace_dialing(transport, now=100.0 + age), expected)
        listening = self.boot_in(BOOT_TO_WAIT)
        pinged = self.boot_in(BOOT_TO_WAIT + 'PCFS: Ping sent. Getting reply from PCFSServer...\r'
                                             'PCFS: Ping complete. PCFS file system is now available.\r')
        self.assertEqual(pinged.state, 'pinged')
        for boot in (listening, pinged):
            with self.subTest(state=boot.state):
                self.assertFalse(server.may_replace_dialing(boot, now=104.0))
        server.started_at = None                  # adopted or unknown start: never
        self.assertFalse(server.may_replace_dialing(transport, now=104.0))

    def test_tick_rechecks_before_replacing(self):
        transport = self.boot_in('MCP: Boot PM flags - PON_RESTART\r'
                                 'PCFSTCP[V11]: selected native TCP transport=3 port=7500\r')
        for states, replaced in ((['SYN_SENT', 'SYN_SENT'], True), (['SYN_SENT', 'ESTABLISHED'], False)):
            with self.subTest(states=states):
                server = self.dialing_server()
                answers, calls = iter(states), []
                with patch.object(pcfs, 'tcp_state', lambda pid, ip: next(answers)), \
                     patch.object(pcfs.time, 'monotonic', lambda: 104.0), \
                     patch.object(pcfs.Server, 'stop', lambda self: calls.append('stop')), \
                     patch.object(pcfs.Server, 'start', lambda self: calls.append('start')):
                    server.tick(transport)
                self.assertEqual(calls, ['stop', 'start'] if replaced else [])

    def test_tick_never_kills_once_console_listens(self):
        """The hang 2 order: console listening, server still SYN_SENT at 3 s, ping about to complete."""
        server = self.dialing_server()
        calls = []
        with patch.object(pcfs, 'tcp_state', lambda pid, ip: 'SYN_SENT'), \
             patch.object(pcfs.time, 'monotonic', lambda: 104.0), \
             patch.object(pcfs.Server, 'stop', lambda self: calls.append('stop')), \
             patch.object(pcfs.Server, 'start', lambda self: calls.append('start')):
            server.tick(self.boot_in(BOOT_TO_WAIT))
        self.assertEqual(calls, [])

    def test_stale_route_server_replaced_while_console_listens(self):
        """2026-11-26: dialed while the Ethernet link was down, so over Wi-Fi; never connected."""
        server = self.dialing_server()
        listening = self.boot_in(BOOT_TO_WAIT)
        for age, expected in ((3.0, False), (3.7, True), (6.0, True), (7.0, False), (7.9, True), (9.0, False)):
            with self.subTest(age=age):
                self.assertEqual(server.may_replace_dialing(listening, now=100.0 + age, stale=True), expected)
        pinged = self.boot_in(BOOT_TO_WAIT + 'PCFS: Ping complete. PCFS file system is now available.\r')
        self.assertFalse(server.may_replace_dialing(pinged, now=104.0, stale=True))
        for stale, replaced in ((True, True), (False, False)):
            with self.subTest(stale=stale):
                server, calls = self.dialing_server(), []
                with patch.object(pcfs, 'tcp_state', lambda pid, ip: 'SYN_SENT'), \
                     patch.object(pcfs, 'stale_route', lambda pid, ip: stale), \
                     patch.object(pcfs.time, 'monotonic', lambda: 104.0), \
                     patch.object(pcfs.Server, 'stop', lambda self: calls.append('stop')), \
                     patch.object(pcfs.Server, 'start', lambda self: calls.append('start')):
                    server.tick(listening)
                self.assertEqual(calls, ['stop', 'start'] if replaced else [])

    def test_stale_route_compares_dialing_and_current_source(self):
        for dialing, now, stale in (('192.0.2.10', '198.51.100.1', True), ('198.51.100.1', '198.51.100.1', False),
                                    (None, '198.51.100.1', False), ('192.0.2.10', None, False)):
            with self.subTest(dialing=dialing, now=now), \
                 patch.object(pcfs, 'dialing_from', lambda pid, ip: dialing), \
                 patch.object(pcfs, 'route_source', lambda ip: now):
                self.assertEqual(pcfs.stale_route(4321, '198.51.100.50'), stale)
        self.assertEqual(pcfs.route_source('127.0.0.1'), '127.0.0.1')

    def test_keeper_stopped_and_file_removed(self):
        self.assertIsNone(pcfs.stop_keeper())
        pcfs.KEEP_FILE.write_text('{"pid": 999999999}', encoding='utf-8')
        self.assertIsNone(pcfs.keeper_running())
        pcfs.stop_keeper()
        self.assertFalse(pcfs.KEEP_FILE.exists())


class StreamLogTests(unittest.TestCase):
    def rpc_from(self, chunks):
        chunks = list(chunks)

        def rpc(request):
            item = chunks.pop(0) if chunks else ''
            if isinstance(item, BaseException):
                raise item
            return {'gap': False, 'error': None, 'text': item, 'cursor': request['cursor'] + len(item)}
        return rpc

    def run_stream(self, chunks, **kw):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from catr import cli
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli.serial, 'rpc', self.rpc_from(chunks)), patch.object(cli.time, 'sleep', lambda s: None), \
             redirect_stdout(out), redirect_stderr(err):
            result = cli.stream_log(0, **kw)
        return result, out.getvalue(), err.getvalue()

    def test_until_across_chunks(self):
        ok, out, err = self.run_stream(['PCFS_ONE_JOB: waiting\rPCFS_ONE', '_JOB_OK: read 4 bytes\r', 'never read'],
                                       until='PCFS_ONE_JOB_OK')
        self.assertTrue(ok)
        self.assertNotIn('never read', out)
        self.assertIn('keeps running', err)

    def test_until_times_out(self):
        from catr import cli
        clock = iter(range(0, 1000, 10))
        with patch.object(cli.time, 'monotonic', lambda: next(clock)):
            ok, _, err = self.run_stream(['a\r'] * 50, until='PCFS_ONE_JOB_OK', wait=30)
        self.assertFalse(ok)
        self.assertIn('not seen in 30 s', err)

    def test_ctrl_c_stops_following_only(self):
        ok, out, err = self.run_stream(['line one\r', KeyboardInterrupt()])
        self.assertFalse(ok)
        self.assertIn('line one', out)
        self.assertIn('stopped following the log; the title keeps running', err)


if __name__ == '__main__':
    unittest.main()
