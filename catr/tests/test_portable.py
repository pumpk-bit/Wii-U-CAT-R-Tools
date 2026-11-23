"""catr must not depend on one PC: COM port, networks, Windows language."""
import argparse
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import cli, config, pcfs
from catr.transport import serial, winserial


class PortTests(unittest.TestCase):
    def pick(self, requested=None, saved=None, ports=()):
        with patch.object(config, 'load', lambda: {'serial_port': saved} if saved else {}), \
                patch.object(winserial, 'available_ports', lambda: list(ports)):
            return serial.pick_port(requested)

    def test_requested_then_saved_then_the_only_one(self):
        self.assertEqual(self.pick('COM9', 'COM3', ['COM5']), 'COM9')
        self.assertEqual(self.pick(None, 'COM3', ['COM5']), 'COM3')
        self.assertEqual(self.pick(None, None, ['COM5']), 'COM5')

    def test_never_guesses(self):
        with self.assertRaisesRegex(ValueError, 'several .COM3, COM5.'):
            self.pick(None, None, ['COM3', 'COM5'])
        with self.assertRaisesRegex(ValueError, 'no USB serial port'):
            self.pick()

    def test_available_ports_can_be_read_here(self):
        self.assertIsInstance(winserial.available_ports(), list)


class FindTests(unittest.TestCase):
    def find(self, saved, port=None):
        with tempfile.TemporaryDirectory() as home, patch.object(config, 'HOME', Path(home)), patch.dict(os.environ), \
                patch.object(cli, 'probe', lambda ip: {'ip': ip, 'status': 'wupserver'}), \
                patch.object(cli, 'output'), patch.object(winserial, 'available_ports', lambda: []):
            os.environ.pop('CATR_CONFIG_FILE', None)
            if saved:
                config.save(saved)
            self.assertEqual(cli.find(argparse.Namespace(ip='192.0.2.1', network=None, port=port)), 0)
            return config.load()

    def test_keeps_a_saved_port(self):
        self.assertEqual(self.find({'serial_port': 'COM3'})['serial_port'], 'COM3')
        self.assertEqual(self.find({'serial_port': 'COM3'}, 'COM8')['serial_port'], 'COM8')

    def test_saves_the_ip_without_a_port(self):
        saved = self.find(None)
        self.assertEqual(saved['ip'], '192.0.2.1')
        self.assertNotIn('serial_port', saved)

    def test_networks_come_from_this_pc(self):
        answers = [(socket.AF_INET, 0, 0, '', (ip, 0)) for ip in ('10.1.2.3', '169.254.9.9', '8.8.8.8', '127.0.0.1')]
        with patch.object(socket, 'getaddrinfo', lambda *_: answers):
            self.assertEqual([str(network) for network in cli.local_networks()], ['10.1.2.0/24'])


class TcpStateTests(unittest.TestCase):
    def test_states_of_a_real_connection(self):
        with socket.create_server(('127.0.0.1', 0)) as server, patch.object(pcfs, 'PORT', server.getsockname()[1]):
            self.assertIsNone(pcfs.tcp_state(os.getpid(), '127.0.0.1'))
            with socket.create_connection(server.getsockname()) as client:
                accepted, _ = server.accept()
                self.assertEqual(pcfs.tcp_state(os.getpid(), '127.0.0.1'), 'ESTABLISHED')
                accepted.close()
                for _ in range(50):
                    if pcfs.tcp_state(os.getpid(), '127.0.0.1') == 'CLOSE_WAIT':
                        break
                self.assertEqual(pcfs.tcp_state(os.getpid(), '127.0.0.1'), 'CLOSE_WAIT')


class PcfsFolderTests(unittest.TestCase):
    def test_a_missing_folder_is_refused_before_the_console_is_touched(self):
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaisesRegex(FileNotFoundError, 'catr pcfs root'):
                pcfs.Server('192.0.2.1', Path(home) / 'missing').check_conflicts()


if __name__ == '__main__':
    unittest.main()
