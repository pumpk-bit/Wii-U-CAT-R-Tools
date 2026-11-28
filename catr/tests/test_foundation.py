import socket
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from catr.safety import readonly, classify
from catr.transport.serial import History, TitleState, command_proof
from catr.transport.wup import Session
from catr.vendor.wupclient import wupclient


class FoundationTests(unittest.TestCase):
    def test_help_has_no_standard_shell_header(self):
        self.assertTrue(command_proof('help', 'cos help - List all commands\rcos sdkversion\rcos killrestart'))
        self.assertFalse(command_proof('help', 'cos help'))

    def test_echo_and_prompt_prove_headerless_commands(self):
        # Real reply to `cos modules` inside SCT, 2026-11-14 (no standard header).
        text = 'cos modules\r\n# 00;01;32;895: \r\nProcess 7 Loaded RPLs:\r\n'
        self.assertTrue(command_proof('modules', text, 'cos modules'))
        # Garbled input seen during heavy game output: no exact echo, no prompt.
        self.assertFalse(command_proof('launch', 'cos l\xff\xffA\xffo\xffScreenManager: audio\r\n', 'cos launch 0x00050010 0x1F700700'))
        # Echo alone, prompt not yet printed.
        self.assertFalse(command_proof('modules', 'cos modules\r\n', 'cos modules'))

    def test_serial_policy_rejects_mutations_and_command_injection(self):
        for command in ('slowlaunch 123', 'setword 1 2', 'unknown', 'help\rkill', 'help;kill', 'cos help\x00'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                readonly(command)
        self.assertEqual(readonly('cos iostats 1'), ('iostats', 'cos iostats 1'))
        # Address commands: `memdump 0xDEAD0000 4` crashed the kernel on 2026-11-14.
        for command in ('memdump 0xDEAD0000 4', 'disasm 0x0101c400 4', 'getsym 0', 'ctxdump 0', 'heapcheck 0'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                readonly(command)
        self.assertEqual(classify('memdump 0 4')[1], 'address')
        self.assertEqual(classify('slowlaunch 123')[1], 'disruptive')

    def test_user_cos_allows_any_single_command(self):
        from catr.safety import user_command
        self.assertEqual(user_command('memdump 0x10000000 4'), ('memdump', 'address', 'cos memdump 0x10000000 4'))
        self.assertEqual(user_command('cos killrestart'), ('killrestart', 'disruptive', 'cos killrestart'))
        self.assertEqual(user_command('setword 1 2')[1], 'dangerous')
        for command in ('help\rkill', 'help;kill', 'help|kill', 'cos help\x00', '', 'cos'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                user_command(command)

    def test_config_set_changes_one_leaf_only(self):
        from catr import sysconfig
        data = (Path(__file__).parent / 'data/system.xml').read_bytes()
        root, values = sysconfig.leaves(data)
        self.assertEqual(root, 'system')
        self.assertEqual(values['log/enable']['value'], '0')
        self.assertEqual(values['default_device_type']['type'], 'string')
        new, old, value = sysconfig.plan_set(data, 'system', 'log/enable', '1')
        self.assertEqual((old, value), ('0', '1'))
        self.assertEqual(len(new), len(data))
        self.assertEqual([i for i, (a, b) in enumerate(zip(data, new)) if a != b], [data.index(b'>0</enable>') + 1])
        # standby/enable is a different <enable>: it must not be touched by log/enable.
        self.assertEqual(sysconfig.leaves(new)[1]['standby/enable']['value'], '0')
        new, _, _ = sysconfig.plan_set(data, 'system', 'standby/enable', '1')
        self.assertEqual(sysconfig.leaves(new)[1]['log/enable']['value'], '0')
        new, _, value = sysconfig.plan_set(data, 'system', 'log/max_size', '0x100000')
        self.assertEqual(value, '1048576')
        for key, val, flag in (('default_title_id', '000500101f700700', True), ('default_os_id', '000500101000400a', True),
                               ('version', '22', True), ('dev_mode', '0', False), ('log/enable', '4294967296', False),
                               ('log/enable', 'x', False), ('nope', '1', True)):
            with self.subTest(key=key, val=val), self.assertRaises(ValueError):
                sysconfig.plan_set(data, 'system', key, val, unsafe=flag)
        new, _, _ = sysconfig.plan_set(data, 'system', 'prev_title_id', '0005000010c0be00', unsafe=True)
        self.assertEqual(sysconfig.leaves(new)[1]['prev_title_id']['value'], '0005000010c0be00')
        with self.assertRaises(ValueError):
            sysconfig.plan_set(data, 'system', 'prev_title_id', '1234', unsafe=True)
        with self.assertRaises(ValueError):
            sysconfig.plan_set(data, 'sys_prod', 'anything', '1', unsafe=True)

    def test_spark_capture_watch(self):
        from catr import spark
        root = Path(tempfile.mkdtemp())
        nl = '\r\n'
        watch = spark.CaptureWatch(root)
        watch.feed('00;00;48;769: SparkCube: alive, frame 600' + nl +
                   '00;00;51;189: SparkCube: capture request 1 at frame 745 -> /cygdrive/%SAVE_EMU_DIR/spark/spark_cu')
        self.assertEqual(watch.captures, [])
        watch.feed('be.4mp' + nl + '00;00;51;222: GX2: Warning! Swap interval set 0.' + nl)
        self.assertEqual(len(watch.pending), 1)
        self.assertEqual(Path(watch.captures[0]['path']), (root / 'spark/spark_cube.4mp').resolve())
        watch.feed('00;01;14;812: SparkCube: alive, frame 1200' + nl)
        self.assertEqual((watch.pending, watch.captures[0]['resumed_frame']), ([], 1200))
        with self.assertRaises(ValueError):
            spark.CaptureWatch(root).feed('capture request 2 at frame 1 -> /cygdrive/%SAVE_EMU_DIR/../../x.4mp' + nl)
        rejected = spark.CaptureWatch(root)
        rejected.feed('GX2: Capture not supported in production mode' + nl)
        self.assertTrue(rejected.rejected)

    def test_watch_relaunch_line_not_adjacent(self):
        from catr.actions import Watch
        nl = '\r\n'
        # Raw order from the 2026-11-14 boot series (run 6 of the second series): one line in between.
        text = nl.join(['00;00;32;008: SHUTINFO: RAMPID 6 is also alive.',
                        '00;00;32;008: DIAG:Result PrepareTitle(0x000500101F700700) came back with cosxml(0x0000000000000000)',
                        '00;00;32;008: p2 received EXIT while in foreground.',
                        '00;00;32;008: PrepareTitle(Relaunch) Succeeded.  IOS should have or will be telling us to shut down.',
                        '00:00:32:127: MCP: Power Transition -> PM_RESTART, flags 0x0', '']) 
        watch = Watch('launch', '000500101f700700')
        watch.feed(text)
        self.assertIsNone(watch.failure)
        self.assertIsNone(watch.outcome())
        missing = Watch('launch', '000500001f6ff101')
        missing.feed(nl.join(['DIAG:Result PrepareTitle(0x000500001F6FF101) came back with cosxml(0x0000000000000000)']
                             + [f'line {i}' for i in range(9)] + ['']))
        self.assertEqual(missing.outcome(), 'not-found')

    def test_phase1_title_and_path_rules(self):
        from catr import safety
        from catr.cli import tmd_identity
        for tid in ('000500101f700700', '0005001010000100', '000500101000400a', '0005001010045100', '000500101f700400'):
            self.assertTrue(safety.boot_critical(tid), tid)
        self.assertFalse(safety.boot_critical('0005000010c0be00'))
        self.assertIn('repair', safety.install_block('000500101f700700', 99))
        self.assertIn('older', safety.install_block('0005000010c0be00', 2, 4))
        self.assertIsNone(safety.install_block('0005000010c0be00', 4, 2))
        self.assertIsNotNone(safety.uninstall_block('000500101f700500', 0x80000000))
        self.assertIsNotNone(safety.uninstall_block('0005001010040100', 0x90000001))
        self.assertIsNone(safety.uninstall_block('0005000010c0be00', 0x80000000))
        self.assertIsNone(safety.uninstall_block('0005000010c0be00', 0x80000000, '/vol/storage_mlc01/usr/title/00050000/10c0be00'))
        self.assertIsNotNone(safety.uninstall_block('000500001f701500', 0x80000000, '/vol/system_slc/title/00050000/1f701500'))
        self.assertIsNotNone(safety.uninstall_block('0005000010c0be00', 0x80000000, ''))
        self.assertEqual(safety.write_check(safety.STAGING + '/a/b'), safety.STAGING + '/a/b')
        for path in ('/vol/system/config/system.xml', '/vol/system_slc/x/y', '/vol/storage_mlc01/sys/title/a',
                     '/vol/storage_mlc01/usr/title/00050000/10c0be00', '/vol/storage_mlc01',
                     safety.STAGING + '/../title', safety.STAGING + '//x', r'C:\x', r'/vol/storage_mlc01/usr/tmp/catr\x'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safety.write_check(path, unsafe=True)
        with self.assertRaises(ValueError):
            safety.write_check('/vol/storage_mlc01/usr/save/x')
        self.assertEqual(safety.write_check('/vol/storage_mlc01/usr/save/x', unsafe=True), '/vol/storage_mlc01/usr/save/x')
        tmd = bytearray(0x1E0)
        tmd[0:4] = bytes.fromhex('00010004')
        tmd[0x184:0x18C] = bytes.fromhex('000500101000400a')
        tmd[0x18C:0x194] = bytes.fromhex('0005000010c0be00')
        tmd[0x1DC:0x1DE] = (4).to_bytes(2, 'big')
        self.assertEqual(tmd_identity(bytes(tmd)), ('0005000010c0be00', 4, '000500101000400a'))
        with self.assertRaises(ValueError):
            tmd_identity(b'\0' * 0x1E0)

    def test_fragmented_wup_svc_response_and_eof(self):
        left, right = socket.socketpair()
        client = wupclient.__new__(wupclient)
        client.s, client.fsa_handle = left, None
        def server():
            right.recv(1024)
            for byte in struct.pack('>II', 0, 42):
                right.sendall(bytes([byte]))
            right.close()
        thread = threading.Thread(target=server)
        thread.start()
        self.assertEqual(client.svc(0x33, []), 42)
        with self.assertRaises(ConnectionError):
            client._recv_exact(4)
        thread.join()
        left.close()

    def test_read_receives_data_before_status(self):
        left, right = socket.socketpair()
        client = wupclient.__new__(wupclient)
        client.s, client.fsa_handle = left, None
        def server():
            self.assertEqual(right.recv(12), struct.pack('>III', 1, 0x1234, 3))
            right.sendall(b'abc\x00\x00\x00\x00')
            right.close()
        thread = threading.Thread(target=server)
        thread.start()
        self.assertEqual(client.read(0x1234, 3), b'abc')
        thread.join()
        left.close()

    def test_title_state_split_lines_and_restart(self):
        state = TitleState()
        self.assertEqual(state.value['state'], 'unknown')
        state.feed('ROOT IS STARTING MASTER TITLE\r00;00;21;756: DIAG:Issued KiPrepareTitleAsync(0x000500101F700700)\r')
        state.feed('00;00;21;817: TitleId 0x000500101F70')
        state.feed('0700 Version 0x00000009 Built On SDK 21213\r')
        self.assertEqual(state.value['title'], '000500101f700700')
        self.assertIsNotNone(state.started_at, 'start time recorded for the action settle delay')
        state.feed('---PPC USER-LEVEL FAST RESTART---\r')
        self.assertEqual(state.value['state'], 'unknown')
        state.feed('***MASTER TITLE KILLED WHILE IN DEV MODE***\rWAITING IN ROOT\n')
        self.assertEqual(state.value['state'], 'root')

    def test_title_state_ignores_boot_system_titles(self):
        # Order seen on every Cafe restart on 2026-11-14.
        state = TitleState()
        state.feed('00;00;19;700: DIAG:Issued KiPrepareTitleAsync(0x000500301001110A)\r'
                   '00;00;19;814: TitleId 0x000500301001110A Version 0x00000064 Built On SDK 21204\r'
                   '00;00;20;029: DIAG:Issued KiPrepareTitleAsync(0x0005003010010100)\r'
                   '00;00;20;127: TitleId 0x0005003010010100 Version 0x000000C5 Built On SDK 21211\r')
        self.assertEqual(state.value['state'], 'unknown')
        state.feed('ROOT IS STARTING MASTER TITLE\r'
                   '00;00;20;200: TitleId 0x0005003010010100 Version 0x000000C5 Built On SDK 21211\r')
        self.assertEqual(state.value['state'], 'unknown', 'TitleId without a matching prepare')
        state.feed('00;00;21;756: DIAG:Issued KiPrepareTitleAsync(0x000500001F6FF000)\r'
                   '00;00;21;817: TitleId 0x000500001F6FF000 Version 0x00000000 Built On SDK 21213\r')
        self.assertEqual(state.value, {'state': 'observed-title', 'title': '000500001f6ff000',
                                       'reason': 'master title start seen on serial; not a foreground query'})
        state.feed('00;01;00;000: TitleId 0x0005003010010100 Version 0x000000C5 Built On SDK 21211\r')
        self.assertEqual(state.value['title'], '000500001f6ff000', 'later TitleId lines do not replace it')

    def test_title_state_restart_resets_partial_sequence(self):
        state = TitleState()
        state.feed('ROOT IS STARTING MASTER TITLE\rDIAG:Issued KiPrepareTitleAsync(0x000500001F6FF000)\r'
                   'MCP: Power Transition -> PM_RESTART, flags 0x0\r'
                   'TitleId 0x000500001F6FF000 Version 0x0\r')
        self.assertEqual(state.value['state'], 'unknown')

    def test_bounded_history_flags_gap(self):
        history = History()
        with patch('catr.transport.serial.MAX_HISTORY', 8):
            history.append(b'123456789012')
        result = history.snapshot(0)
        self.assertEqual(result['text'], '56789012')
        self.assertTrue(result['gap'])
        self.assertEqual(result['cursor'], 12)

    def test_fsa_getinfo_parsing(self):
        session = Session.__new__(Session)
        # Live /vol/system values, 2026-11-14 (matches boot log: 262016 blocks of 2048 B).
        replies = {Session.QUERY_DEVICE_INFO: bytes(8) + struct.pack('>QI', 262016, 2048),
                   Session.QUERY_FREE_SPACE: struct.pack('>Q', 272089088),
                   Session.QUERY_STAT: struct.pack('>IIIII', 0x80000000, 0x666, 0, 0, 0)}
        with patch.object(Session, 'query', lambda self, path, kind: replies[kind]):
            self.assertEqual(session.space('/vol/system')['capacity'], 536608768)
            self.assertTrue(session.stat('/vol/system/config')['directory'])
            replies[Session.QUERY_FREE_SPACE] = struct.pack('>Q', 1 << 40)
            with self.assertRaises(ValueError):
                session.space('/vol/system')

    def test_mcp_title_record(self):
        from catr.transport.wup import parse_title
        entry = bytearray(0x61)
        struct.pack_into('>QI', entry, 0, 0x0005000010C0BE00, 0xC0BE)
        path = b'/vol/storage_mlc01/usr/title/00050000/10c0be00'
        entry[0x0C:0x0C + len(path)] = path
        struct.pack_into('>IHQI', entry, 0x44, 0x80000000, 2, 0x000500101000400A, 21213)
        entry[0x56:0x59] = b'mlc'
        self.assertEqual(parse_title(entry), {
            'title': '0005000010c0be00', 'group': '0000c0be', 'path': path.decode(), 'app_type': '80000000',
            'version': 2, 'os_version': '000500101000400a', 'sdk': 21213, 'device': 'mlc'})

    def test_cli_requires_console_paths(self):
        from catr.cli import remote_path
        self.assertEqual(remote_path('/vol/system/config/'), '/vol/system/config')
        # Git Bash rewrites /vol/... into C:/Program Files/Git/vol/...; refuse it.
        with self.assertRaises(ValueError):
            remote_path('C:/Program Files/Git/vol/system')

    def test_action_wire_forms(self):
        from catr.safety import action, title_id
        self.assertEqual(title_id('0x00050000 0x1F6FF000'), '000500001f6ff000')
        self.assertEqual(title_id('recovery'), '000500101f700700')
        self.assertEqual(action('launch', '000500001f6ff000'), ('launch', 'cos launch 0x00050000 0x1F6FF000'))
        self.assertEqual(action('slowlaunch', 'recovery'), ('slowlaunch', 'cos slowlaunch 0x000500101F700700'))
        self.assertEqual(action('kill'), ('kill', 'cos kill'))
        for bad in ('1234', '000500001f6ff00g', '0005000; kill', ''):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                action('launch', bad)
        with self.assertRaises(ValueError):
            action('setword', '000500001f6ff000')
        with self.assertRaises(ValueError):    # actions are never accepted as raw `catr cos` text
            readonly('launch 0x00050000 0x1F6FF000')

    def test_action_echo_proof(self):
        from catr.transport.serial import echo_proof
        # Raw bytes from the 2026-11-14 live test: no '#' prompt follows a launch.
        self.assertTrue(echo_proof('ay    : 192.0.2.1\rcos launch 0x00050000 0x1F6FF000\r00:00:12:632: MCP: PID 14',
                                   'cos launch 0x00050000 0x1F6FF000'))
        self.assertTrue(echo_proof(' 1 done (5322 ms)\r# cos slowlaunch 0x000500101F700700\r00:00:23:998: MCP',
                                   'cos slowlaunch 0x000500101F700700'))
        self.assertFalse(echo_proof('cos l\xff\xffA\xffo\xffScreenManager: audio\r\n', 'cos launch 0x00050000 0x1F6FF000'))
        self.assertFalse(echo_proof('cos launch 0x00050000 0x1F6FF000', 'cos launch 0x00050000 0x1F6FF000'),
                         'line not finished yet')
        self.assertFalse(echo_proof('cos launch 0x00050000 0x1F6FF0000\r', 'cos launch 0x00050000 0x1F6FF000'))

    def test_launch_method_choice(self):
        from catr.actions import choose_method
        self.assertEqual(choose_method({'state': 'root'}), 'launch')
        self.assertEqual(choose_method({'state': 'observed-title', 'title': '000500101f700700'}), 'launch')
        # A running game silently ignores `cos launch`; unknown state must not guess.
        self.assertEqual(choose_method({'state': 'observed-title', 'title': '000500001f6ff000'}), 'slowlaunch')
        self.assertEqual(choose_method({'state': 'unknown'}), 'slowlaunch')
        self.assertEqual(choose_method({'state': 'root'}, slow=True), 'slowlaunch')

    def test_watch_outcomes(self):
        from catr.actions import Watch
        watch = Watch('launch', '000500001f6ff000')
        watch.feed('cos launch 0x00050000 0x1F6FF000\r\n# MCP: Power Transition -> PM_RESTART, flags 0x0\r'
                   'ROOT IS STARTING MASTER TITLE\rDIAG:Issued KiPrepareTitleAsync(0x000500001F6FF000)\r')
        self.assertIsNone(watch.outcome())
        watch.feed('TitleId 0x000500001F6FF000 Version 0x0 Built On SDK 21213\r')
        self.assertEqual(watch.outcome(), 'launched')
        self.assertTrue(watch.restarted)
        other = Watch('launch', '000500001f6ff000')
        other.feed('ROOT IS STARTING MASTER TITLE\rKiPrepareTitleAsync(0x000500101F700700)\r'
                   'TitleId 0x000500101F700700 Version 0x9\r')
        self.assertEqual(other.outcome(), 'other-title')
        kill = Watch('kill')
        kill.feed('***MASTER TITLE KILLED WHILE IN DEV MODE***\r')
        self.assertIsNone(kill.outcome())
        kill.feed('WAITING IN ROOT\r')
        self.assertEqual(kill.outcome(), 'root')

    def test_pcfs_boot_states(self):
        from catr.pcfs import PcfsBoot, current_boot
        boot = PcfsBoot()
        boot.feed('MCP: Boot PM flags - PON_RESTART \r00:00:02:762: PCFSTCP[V10]: selected native TCP transport=3 port=7500\r')
        self.assertTrue(boot.dialing)
        boot.feed('PCFS: Waiting for PC connection on port 7500...\rPCFS: Ping complete. PCFS file system is now available.\r')
        self.assertFalse(boot.done)
        boot.feed('FSA: [uptime 9.943 s]: Attached volume to hfio01 (pcfs)\r')
        self.assertEqual(boot.state, 'mounted')
        boot.feed('MCP: Boot PM flags - PON_RESTART \rPCFSTCP[V10]: selected native TCP transport=3 port=7500\r'
                  '__DKT_OpenTCP: TCP_SM_Init() failed, err=-1/0xffffffff\r'
                  'PCFSTCP[V10]: selected native TCP transport=3 port=7500\r')     # duplicated line seen in logs
        self.assertEqual(boot.state, 'failed')
        self.assertIn('TCP_SM_Init', boot.detail)
        self.assertFalse(boot.dialing)
        self.assertEqual(current_boot('old\rAttached volume to hfio01 (pcfs)\rMCP: Boot PM flags - PON_RESTART\r'
                                      'PCFS: Waiting for PC connection on port 7500...\r').state, 'waiting')
        self.assertEqual(current_boot('no boot here\r').state, 'unknown')

    def test_pcfs_boot_v11_lines(self):
        from catr.pcfs import Keeper, PcfsBoot
        # Formats from .mion-live/verified-builds/v11-main.c (not yet seen on hardware).
        boot = PcfsBoot()
        boot.feed('MCP: Boot PM flags - PON_RESTART\r00:00:02:700: PCFSTCP[V11]: selected native TCP transport=3 port=7500\r')
        self.assertTrue(boot.dialing)
        boot.feed('00:00:03:900: PCFSTCP[V11]: TCP_SM_Init result=0 attempts=5 retries=4 ncl_fd=3\r'
                  'PCFS: Waiting for PC connection on port 7500...\rAttached volume to hfio01 (pcfs)\r')
        self.assertEqual(boot.state, 'mounted')
        self.assertEqual(boot.init, {'plugin': 'V11', 'result': 0, 'attempts': 5, 'retries': 4})
        boot.feed('MCP: Boot PM flags - PON_RESTART\r')
        self.assertIsNone(boot.init, 'reset per boot')
        boot.feed('PCFSTCP[V11]: signature mismatch; no patch applied\r')
        self.assertEqual(boot.state, 'failed')
        self.assertIn('signature mismatch', boot.detail)

        class Quiet:
            starts = 0
            def stop(self): pass
            def tick(self, boot): pass
        keeper = Keeper(Quiet())
        keeper.feed('PCFSTCP[V11]: selected native TCP transport=3 port=7500\r'
                    'PCFSTCP[V11]: TCP_SM_Init result=0 attempts=1 retries=0 ncl_fd=3\r')
        self.assertEqual(len(keeper.events), 2)

    def test_pcfs_boot_replays_recorded_log(self):
        from catr.pcfs import PcfsBoot, current_boot
        from serial_lines import INCIDENT as log
        boot = PcfsBoot()
        boot.feed(log)
        self.assertEqual(boot.init, {'plugin': 'V11', 'result': 0, 'attempts': 2, 'retries': 1})
        self.assertEqual(current_boot(log).state, boot.state)

    def test_pcfs_keeper_stops_old_server_and_dials_in_window(self):
        from catr.pcfs import Keeper

        class FakeServer:
            def __init__(self):
                self.calls, self.starts = [], 0
            def stop(self):
                self.calls.append('stop')
            def tick(self, boot):
                if boot.dialing:
                    self.calls.append('dial')

        server = FakeServer()
        keeper = Keeper(server)
        keeper.feed('MCP: Power Transition -> PM_RESTART, flags 0x0\rMCP: Boot PM flags - PON_RESTART\r')
        keeper.tick()
        self.assertEqual(server.calls, ['stop'])
        keeper.feed('PCFSTCP[V10]: selected native TCP transport=3 port=7500\r')
        keeper.tick()
        keeper.feed('Attached volume to hfio01 (pcfs)\r')
        keeper.tick()
        self.assertEqual(server.calls, ['stop', 'dial'])
        self.assertEqual(len(keeper.events), 2)

    def test_pcfs_server_stopped_when_boot_fails(self):
        from catr.pcfs import PcfsBoot, Server
        server = Server('192.0.2.1')
        server.process = object()          # a server started a moment before the failure line
        stopped = []
        with patch.object(Server, 'stop', lambda self: (stopped.append(True), setattr(self, 'process', None))), \
             patch.object(Server, 'start', lambda self: self.fail('must not redial')):
            boot = PcfsBoot()
            boot.feed('MCP: Boot PM flags - PON_RESTART\rPCFSTCP[V10]: selected native TCP transport=3 port=7500\r'
                      '__DKT_OpenTCP: TCP_SM_Init() failed, err=-1/0xffffffff\r')
            server.tick(boot)
        self.assertEqual(stopped, [True])
        self.assertIsNone(server.process)

    def test_staged_id_rules(self):
        from catr.stage import staged_id
        self.assertEqual(staged_id('0005000010C0BE00'), '0005000010C0BEF0')
        self.assertEqual(staged_id('000500001F6FF1F0'), '000500001F6FF1F1')
        self.assertEqual(staged_id('0005000010C0BE00', keep=True), '0005000010C0BE00')
        self.assertEqual(staged_id('0005000010C0BE00', explicit='0x000500001f6ff1f0'), '000500001F6FF1F0')

    def test_stage_build_into_root(self):
        import tempfile
        from catr.stage import stage
        with tempfile.TemporaryDirectory() as tmp:
            build, root = Path(tmp) / 'build', Path(tmp) / 'root'
            for part in ('code', 'content', 'meta'):
                (build / part).mkdir(parents=True)
            app = ('<?xml version="1.0"?><app><os_version type="hexBinary" length="8">000500101000800A</os_version>'
                   '<title_id type="hexBinary" length="8">0005000010C0BE00</title_id></app>')
            (build / 'code/app.xml').write_bytes(b'\xef\xbb\xbf' + app.encode())
            (build / 'meta/meta.xml').write_text(app.replace('app>', 'menu>'), encoding='utf-8')
            (build / 'code/cos.xml').write_text('<app><argstr type="string">spark_cube.rpx</argstr></app>', encoding='utf-8')
            (build / 'code/spark_cube.rpx').write_bytes(b'RPX')
            (build / 'code/spark_cube.elf').write_bytes(b'ELF' * 100)
            (build / 'content/data.bin').write_bytes(b'x')
            (root / 'data/mlc/sys/title/0005001b').mkdir(parents=True)     # skip the SDK copy
            result = stage(build, root)
            target = root / 'data/mlc/usr/title/00050000/10c0bef0'
            self.assertEqual((result['title'], result['boot_file'], result['path']),
                             ('0005000010c0bef0', 'spark_cube.rpx', str(target)))
            staged_app = (target / 'code/app.xml').read_bytes()
            self.assertTrue(staged_app.startswith(b'\xef\xbb\xbf'), 'BOM kept')
            self.assertIn(b'>0005000010C0BEF0<', staged_app)
            self.assertIn(b'>000500101000400A<', staged_app, 'debug-OS os_version replaced')
            self.assertIn('>0005000010C0BEF0<', (target / 'meta/meta.xml').read_text('utf-8'))
            self.assertFalse((target / 'code/spark_cube.elf').exists())
            self.assertTrue((target / 'content/data.bin').exists())
            self.assertIn(b'>000500101000800A<', (build / 'code/app.xml').read_bytes(), 'build folder untouched')
            (build / 'content/data.bin').unlink()
            stage(build, root)
            self.assertFalse((target / 'content/data.bin').exists(), 'mirroring removes deleted files')

    def test_watch_detects_not_found_and_source(self):
        from catr.actions import Watch
        watch = Watch('launch', '0005000010c0bef0')
        watch.feed('DIAG:Result PrepareTitle(0x0005000010C0BEF0) came back with cosxml(0x00000000)\r')
        watch.feed('MCP: something else\r')
        self.assertIsNone(watch.outcome(), 'the Relaunch line may still come a few lines later')
        watch.feed('MCP: more\r' * (Watch.RELAUNCH_WINDOW - 1))
        self.assertEqual(watch.outcome(), 'not-found')
        restart = Watch('launch', '0005000010c0bef0')
        restart.feed('DIAG:Result PrepareTitle(0x0005000010C0BEF0) came back with cosxml(0x0000000000000000)\r'
                     '00;04;47;863: PrepareTitle(Relaunch) Succeeded.  IOS should have or will be telling us to shut down.\r')
        self.assertIsNone(restart.outcome(), 'normal Cafe-restart path')
        watch = Watch('launch', '0005000010c0bef0')
        watch.feed('MCP: Mapping /vol/content to /vol/storage_hfiomlc01/usr/title/00050000/10c0bef0/content\r')
        self.assertEqual(watch.source, 'pcfs')

    def test_pm_restart_no_global(self):
        client = wupclient.__new__(wupclient)
        with patch.object(client, 'ioctl', return_value=(0, None)) as ioctl:
            self.assertEqual(client.PM_Restart(7), 0)
            ioctl.assert_called_once_with(7, 0xE3, [], 0)
        with self.assertRaises(ValueError):
            client.dump_syslog()


if __name__ == '__main__':
    unittest.main()
