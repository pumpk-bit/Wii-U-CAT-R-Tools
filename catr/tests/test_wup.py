import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from catr.transport import wup  # noqa: E402


class FakeClient:
    """wupclient as far as Session file I/O uses it: memory, allocs, and the FSA ioctl/ioctlv svcs
    (0x38 open/close, 0x39 read/write by pointer) parsed from the buffers in fake IOSU memory."""

    def __init__(self):
        self.mem = {}
        self.next = 0x10000000
        self.calls = []
        self.files = {}
        self.fds = {}
        self.closed_fds = []
        self.opened = self.closed = 0

    # memory
    def alloc(self, size, align=None):
        address = self.next
        self.next += (size + 0x3f) & ~0x3f
        self.mem[address] = bytearray(size)
        return address

    def free(self, address):
        del self.mem[address]

    def _block(self, address):
        for base, data in self.mem.items():
            if base <= address < base + len(data):
                return base, data
        raise AssertionError(f'unallocated address {address:#x}')

    def write(self, address, data):
        self.calls.append(('write', address, len(data)))
        self._store(address, data)

    def _store(self, address, data):          # IOSU-side change: not a wupserver write
        base, block = self._block(address)
        assert address - base + len(data) <= len(block), 'write past the buffer'
        block[address - base:address - base + len(data)] = data

    def read(self, address, length):
        base, block = self._block(address)
        return bytes(block[address - base:address - base + length])

    # devices
    def open(self, name, mode):
        self.opened += 1
        return 0x77

    def close(self, handle):
        self.closed += 1
        return 0

    def FSA_CloseFile(self, fsa, fd):         # vendor close: only used before any request write
        self.calls.append(('vendor-close', fd))
        self.closed_fds.append(fd)
        del self.fds[fd]
        return 0

    @staticmethod
    def _string(raw):
        return raw[:raw.index(0)].decode()

    def svc(self, number, args):
        if number == 0x38:                      # ioctl
            fsa, cmd, inp, inlen, out, outlen = args
            self.calls.append(('ioctl', cmd))
            req = self.read(inp, inlen)
            if cmd == 0x0E:                     # open
                path, mode = self._string(req[4:0x284]), self._string(req[0x284:0x290])
                if mode == 'r' and path not in self.files:
                    return 0xFFFCFFE9
                fd = 0x100 + len(self.fds) + len(self.closed_fds)
                self.fds[fd] = [path, 0]
                if mode == 'w':
                    self.files[path] = bytearray()
                self._store(out + 4, struct.pack('>I', fd))
                return 0
            if cmd == 0x15:                     # close
                fd = struct.unpack('>I', req[4:8])[0]
                assert fd in self.fds, f'close of fd {fd:#x} that is not open'
                del self.fds[fd]
                self.closed_fds.append(fd)
                return 0
            raise AssertionError(f'unexpected ioctl {cmd:#x}')
        assert number == 0x39                   # ioctlv
        fsa, cmd, n_in, n_out, iovec = args
        self.calls.append(('ioctlv', cmd, n_in, n_out))
        vec = [struct.unpack('>III', self.read(iovec + 12 * i, 12)) for i in range(3)]
        (request, request_len, _), (data, count, _), (out, out_len, _) = vec
        assert (request_len, out_len) == (0x520, 0x293)
        req = self.read(request, 0x520)
        size, cnt, fd = (struct.unpack('>I', req[o:o + 4])[0] for o in (8, 12, 0x14))
        assert size == 1 and cnt == count
        path, pos = self.fds[fd]
        if cmd == 0x10:
            assert (n_in, n_out) == (2, 1)
            self.files[path] += self.read(data, count)
            return count
        assert (n_in, n_out) == (1, 2)
        chunk = bytes(self.files[path][pos:pos + count])
        self._store(data, chunk)
        self.fds[fd][1] += len(chunk)
        return len(chunk)


def make_session(client):
    session = wup.Session.__new__(wup.Session)
    session.client, session._fsa, session._arena, session.quick_ack = client, None, None, False
    return session


def writes(client):
    return [c for c in client.calls if c[0] == 'write']


class FileIOTests(unittest.TestCase):
    def test_write_then_read_round_trip(self):
        client = FakeClient()
        session = make_session(client)
        data = bytes(range(256)) * 1500        # 384,000 bytes: 2 full blocks + a short one
        session.write_file('/vol/x/a.bin', data)
        self.assertEqual(bytes(client.files['/vol/x/a.bin']), data)
        self.assertEqual(session.read_file('/vol/x/a.bin', limit=len(data) + 1), data)
        self.assertEqual(client.fds, {})         # every file closed

    def test_one_memory_write_per_block_plus_open(self):
        client = FakeClient()
        session = make_session(client)
        session.write_file('/vol/x/a.bin', b'\1' * (wup.Session.BLOCK * 3 + 5))
        self.assertEqual(len(writes(client)), 1 + 4)  # open request, then one per block
        client.calls.clear()
        session.read_file('/vol/x/a.bin', limit=10 ** 7)
        self.assertEqual(len(writes(client)), 2)      # open request, then the read requests once

    def test_small_file_costs_two_writes_each_way(self):
        client = FakeClient()
        session = make_session(client)
        session.write_file('/vol/x/t.tmd', b'tmd')
        session.read_file('/vol/x/t.tmd')
        self.assertEqual(len(writes(client)), 4)

    def test_empty_file_closed_with_its_own_handle(self):
        client = FakeClient()
        session = make_session(client)
        session.write_file('/vol/x/a.bin', b'abc')    # leaves a's close request in the buffer
        session.write_file('/vol/x/empty.bin', b'')
        self.assertEqual(bytes(client.files['/vol/x/empty.bin']), b'')
        self.assertIn(('vendor-close', 0x101), client.calls)
        self.assertEqual(client.fds, {})

    def test_failed_write_still_closes_the_file(self):
        client = FakeClient()
        session = make_session(client)
        original = client.svc

        def failing(number, args):
            if number == 0x39:
                return 0xFFFCFFE0                     # an FSA error
            return original(number, args)
        client.svc = failing
        with self.assertRaises(OSError):
            session.write_file('/vol/x/a.bin', b'abc')
        self.assertEqual(client.fds, {})

    def test_open_failure_raises(self):
        session = make_session(FakeClient())
        with self.assertRaises(OSError):
            session.read_file('/vol/x/missing.bin')

    def test_fsa_handle_and_buffer_once_per_session(self):
        client = FakeClient()
        session = make_session(client)
        session.write_file('/vol/x/a.bin', b'abc')
        session.read_file('/vol/x/a.bin')
        session.write_file('/vol/x/b.bin', b'def')
        self.assertEqual((client.opened, client.closed), (1, 0))
        self.assertEqual(len(client.mem), 1)
        session._arena.free()
        self.assertEqual(client.mem, {})


if __name__ == '__main__':
    unittest.main()
