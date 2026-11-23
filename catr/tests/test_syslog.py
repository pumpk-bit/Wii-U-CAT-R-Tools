import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr import syslog as sl

BASE = 0x1FF7FFD0


class FakeMemory:
    """MCP memory as the 2026-11-15 spike saw it, with a log of what was read."""

    def __init__(self, pointer=BASE, signature=sl.SIGNATURE, magic=sl.MAGIC, text=b'boot\n', wrap=False):
        self.reads = []
        self.mem = {sl.SIGNATURE_AT: signature, sl.POINTER: struct.pack('>I', pointer)}
        self.pointer = pointer
        header = struct.pack('>4I', magic, 2, 0, magic + 2)
        data = bytearray(sl.CAPACITY)
        write = len(text)
        data[:write] = text
        if wrap:
            data[write:] = b'x' * (sl.CAPACITY - write)
        entry0 = struct.pack('>4I', write, write, sl.CAPACITY, pointer + 0x20) + bytes(data)
        entry1 = struct.pack('>4I', 0, 0, sl.CAPACITY, pointer + 0x10 + sl.ENTRY + 0x10) + bytes(sl.CAPACITY)
        self.block = header + entry0 + entry1

    def read(self, address, size):
        self.reads.append((address, size))
        if address in self.mem:
            return self.mem[address][:size]
        offset = address - self.pointer
        assert 0 <= offset and offset + size <= len(self.block), f'unchecked read {address:#x}'
        return self.block[offset:offset + size]


class SyslogTests(unittest.TestCase):
    def test_reads_current_boot(self):
        result = sl.fetch(FakeMemory(text=b'----------- Starting System Log -----------\n').read)
        self.assertEqual((result['entry'], result['wrapped']), (0, False))
        self.assertTrue(result['text'].startswith(b'-----------'))

    def test_previous_boot_entry(self):
        result = sl.fetch(FakeMemory().read, previous=True)
        self.assertEqual((result['entry'], result['text']), (1, b''))

    def test_wrapped_buffer_starts_at_write_offset(self):
        result = sl.fetch(FakeMemory(text=b'NEW', wrap=True).read)
        self.assertTrue(result['wrapped'])
        self.assertTrue(result['text'].endswith(b'NEW') and result['text'].startswith(b'xxx'))

    def test_other_iosu_build_stops_before_the_pointer(self):
        memory = FakeMemory(signature=bytes(16))
        with self.assertRaisesRegex(ValueError, 'differs'):
            sl.fetch(memory.read)
        self.assertEqual(memory.reads, [(sl.SIGNATURE_AT, 16)])

    def test_pointer_outside_region_not_followed(self):
        memory = FakeMemory(pointer=0xDEAD0000)
        with self.assertRaisesRegex(ValueError, 'outside'):
            sl.fetch(memory.read)
        self.assertNotIn(0xDEAD0000, [a for a, _ in memory.reads])

    def test_bad_magic_refused(self):
        with self.assertRaisesRegex(ValueError, 'header'):
            sl.fetch(FakeMemory(magic=0x12345678).read)


if __name__ == '__main__':
    unittest.main()
