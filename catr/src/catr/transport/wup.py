"""Read-only facade; reconnects are explicit, never replay an operation."""
import socket
import struct
import sys
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from ..vendor.wupclient import buffer, copy_word, wupclient

SIO_TCP_SET_ACK_FREQUENCY = 0x98000017


def ack_every_segment(sock):
    """Windows: ACK every received segment at once instead of delaying the ACK.

    Only changes the PC's ACK timing; what the PC sends (and how it is split) is unchanged, so the
    wupserver framing rules still hold. Measured 2026-11-15: a 16-byte wupserver read 49 ms -> 7 ms.
    Returns False where it isn't available.
    """
    if sys.platform != 'win32':
        return False
    import ctypes
    value, returned = ctypes.c_ulong(1), ctypes.c_ulong(0)
    ws2 = ctypes.windll.ws2_32
    return ws2.WSAIoctl(ctypes.c_size_t(sock.fileno()), SIO_TCP_SET_ACK_FREQUENCY, ctypes.byref(value), 4,
                        None, 0, ctypes.byref(returned), None, None) == 0


def check(result, operation):
    if result is None or result >= 0x80000000:
        raise OSError(f'{operation}: IOSU result {result!r}')
    return result


class Arena:
    """One wupserver buffer per session for FSA file I/O: requests, reply and data side by side.

    Each separate wupserver memory write costs ~200 ms (Nagle holds the payload until the console
    ACKs the 12-byte header; that gap is what the framing rule needs). The vendor FSA calls make
    several small writes per call. Here the block request, its iovec and the close request sit
    right in front of the data area, so one block of a file costs one memory write (requests plus
    data, still header and payload as two sends) and one svc. Opening a file is one more write.

    History, 2026-11-15: vendor path 94 KB/s with read-back and ~3.2 s per file; reusing per-file
    buffers 279 KB/s and ~2 s per file; this layout removes the rest of
    the per-file writes.
    """

    OPEN, OUT, REQ, IOVEC, CLOSE, DATA = 0x000, 0x540, 0x800, 0xD40, 0xD80, 0x12C0
    REQ_SIZE, OUT_SIZE = 0x520, 0x293
    FSA_OPEN, FSA_CLOSE, FSA_READ, FSA_WRITE = 0x0E, 0x15, 0x0F, 0x10

    def __init__(self, client, block):
        self.c, self.block = client, block
        self.base = client.alloc(self.DATA + block, 0x40)
        if not self.base:
            raise OSError('wupserver could not allocate the FSA I/O buffer')
        self.data = self.base + self.DATA

    def free(self):
        if self.base:
            self.c.free(self.base)
            self.base = 0

    def open(self, fsa, path, mode):
        """FSA open (ioctl 0x0E), request at OPEN, reply at OUT; returns the file handle."""
        request = buffer(self.REQ_SIZE)
        encoded = path.encode('ascii') + b'\0'
        if len(encoded) > 0x280:
            raise ValueError(f'path too long: {path}')
        request[0x04:0x04 + len(encoded)] = encoded
        request[0x284:0x284 + len(mode) + 1] = mode.encode('ascii') + b'\0'
        self.c.write(self.base + self.OPEN, request)
        ret = self.c.svc(0x38, [fsa, self.FSA_OPEN, self.base + self.OPEN, self.REQ_SIZE,
                                self.base + self.OUT, self.OUT_SIZE])
        check(ret, f'open {path}' + (' for writing' if mode == 'w' else ''))
        return struct.unpack('>I', self.c.read(self.base + self.OUT + 4, 4))[0]

    def requests(self, fd, count):
        """Bytes for REQ..DATA: the block request, its iovec and the close request for fd."""
        region = bytearray(self.DATA - self.REQ)
        copy_word(region, 1, 0x08)
        copy_word(region, count, 0x0C)
        copy_word(region, fd, 0x14)
        # Same vector order as the vendor ioctlv: inbufs, inbufs_ptr, outbufs_ptr, outbufs.
        vec = b''.join(struct.pack('>III', a, n, 0) for a, n in
                       ((self.base + self.REQ, self.REQ_SIZE), (self.data, count),
                        (self.base + self.OUT, self.OUT_SIZE)))
        region[self.IOVEC - self.REQ:self.IOVEC - self.REQ + len(vec)] = vec
        copy_word(region, fd, self.CLOSE - self.REQ + 0x04)
        return bytes(region)

    def call(self, fsa, cmd):
        """FSA read (0x0F) or write (0x10) by pointer, using the requests written at REQ."""
        n_in, n_out = (2, 1) if cmd == self.FSA_WRITE else (1, 2)
        return self.c.svc(0x39, [fsa, cmd, n_in, n_out, self.base + self.IOVEC])

    def close(self, fsa):
        """FSA close (ioctl 0x15) with the close request written at CLOSE."""
        return self.c.svc(0x38, [fsa, self.FSA_CLOSE, self.base + self.CLOSE, self.REQ_SIZE,
                                 self.base + self.OUT, self.OUT_SIZE])


class Session:
    def __init__(self, ip, timeout=3):
        self.client = wupclient(ip, timeout=timeout)
        self.quick_ack = ack_every_segment(self.client.s)
        self._fsa = None
        self._arena = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            if self._arena is not None:
                self._arena.free()
            if self._fsa is not None:
                self.client.close(self._fsa)
        except (OSError, ConnectionError, socket.timeout):
            pass
        finally:
            self._fsa = self._arena = None
            self.client.s.close()

    @contextmanager
    def device(self, name):
        """A device handle. /dev/fsa is opened once per session and closed with it (each open
        costs a string write, ~0.2 s); other devices are opened and closed around the block."""
        if name == '/dev/fsa':
            if self._fsa is None:
                self._fsa = check(self.client.open(name, 0), f'open {name}')
            yield self._fsa
            return
        handle = check(self.client.open(name, 0), f'open {name}')
        try:
            yield handle
        finally:
            check(self.client.close(handle), f'close {name}')

    # Bytes per FSA read/write. 512 KB blocks were no faster (2026-11-15): the data write itself
    # runs at ~1 MB/s.
    BLOCK = 0x20000

    def arena(self):
        if self._arena is None:
            self._arena = Arena(self.client, self.BLOCK)
        return self._arena

    def read_file(self, path, limit=4 * 1024 * 1024):
        with self.device('/dev/fsa') as fsa:
            io = self.arena()
            fd = io.open(fsa, path, 'r')
            close_ready = False       # the close request at CLOSE may still name an earlier file
            try:
                self.client.write(io.base + io.REQ, io.requests(fd, self.BLOCK))
                close_ready = True
                output = bytearray()
                while True:
                    count = check(io.call(fsa, io.FSA_READ), f'read {path}')
                    if not count:
                        return bytes(output)
                    if count > self.BLOCK:
                        raise ValueError('Invalid FSA byte count')
                    output.extend(self.client.read(io.data, count))
                    if len(output) > limit:
                        raise ValueError(f'{path} exceeds read limit {limit}')
            finally:
                self._close(fsa, io, fd, path, close_ready)

    def write_file(self, path, data):
        """Replace a console file with data (open 'w' truncates). Caller reads back and compares.

        Each block is one memory write (requests + data), header then payload as two sends
        (vendor `write`); never combined.
        """
        with self.device('/dev/fsa') as fsa:
            io = self.arena()
            fd = io.open(fsa, path, 'w')
            close_ready = False       # the close request at CLOSE may still name an earlier file
            try:
                for offset in range(0, len(data), self.BLOCK):
                    chunk = bytes(data[offset:offset + self.BLOCK])
                    self.client.write(io.base + io.REQ, io.requests(fd, len(chunk)) + chunk)
                    close_ready = True
                    written = check(io.call(fsa, io.FSA_WRITE), f'write {path}')
                    if written != len(chunk):
                        raise OSError(f'write {path}: {written} of {len(chunk)} bytes')
            finally:
                self._close(fsa, io, fd, path, close_ready)

    def _close(self, fsa, io, fd, path, close_ready):
        if close_ready:
            check(io.close(fsa), f'close {path}')
        else:                         # nothing written for this fd yet: the vendor close names it
            check(self.client.FSA_CloseFile(fsa, fd), f'close {path}')

    def restart(self):
        """PM_Restart (ioctl 0xE3 on /dev/pm), as wupclient's own example does. The console may
        drop the connection before replying; that counts as sent. Returns the result or None."""
        handle = check(self.client.open('/dev/pm', 0), 'open /dev/pm')
        try:
            return self.client.PM_Restart(handle)
        except (OSError, ConnectionError, socket.timeout):
            return None      # connection went down with the console

    def install(self, path):
        """Install the image folder at console path onto MLC; blocks until MCP finishes.

        Same sequence as Recovery Menu v0.9: target device 0 (MLC), InstallGetInfo, then install
        (wupclient's MCP_Install, the synchronous ioctlv 0x81). Returns the GetInfo fields.
        """
        with self.device('/dev/mcp') as mcp:
            check(self.client.MCP_InstallSetTargetDevice(mcp, 0), 'MCP_InstallSetTargetDevice(MLC)')
            ret, fields = self.client.MCP_InstallGetInfo(mcp, path)
            check(ret, f'MCP_InstallGetInfo {path}')
            check(self.client.MCP_Install(mcp, path), f'MCP_Install {path}')
        return fields

    def uninstall(self, title_path):
        """Uninstall the title at its MCP path (e.g. /vol/storage_mlc01/usr/title/00050000/10c0be00)."""
        with self.device('/dev/mcp') as mcp:
            check(self.client.MCP_Uninstall(mcp, title_path), f'MCP_Uninstall {title_path}')

    FS_EXISTS = 0xFFFCFFEA      # FSA "already exists" (wupclient's mkdir treats it as success)

    def makedir(self, path, mode=0x600):
        """Create one directory; False if it already existed."""
        with self.device('/dev/fsa') as fsa:
            ret = self.client.FSA_MakeDir(fsa, path, mode)
        if ret == self.FS_EXISTS:
            return False
        check(ret, f'mkdir {path}')
        return True

    def remove(self, path):
        """Remove one file or empty directory."""
        with self.device('/dev/fsa') as fsa:
            check(self.client.FSA_Remove(fsa, path), f'remove {path}')

    def exists(self, path):
        try:
            return self.stat(path)
        except OSError:
            return None

    def default_title(self):
        with self.device('/dev/mcp') as mcp:
            ret, data = self.client.MCP_GetDefaultTitleId(mcp)
            check(ret, 'MCP_GetDefaultTitleId')
            if len(data) != 8:
                raise ValueError('Invalid default title response')
            return data.hex()

    def set_default_title(self, tid):
        """MCP_SetDefaultTitleId, then read back; returns the default MCP reports afterwards."""
        with self.device('/dev/mcp') as mcp:
            check(self.client.MCP_SetDefaultTitleId(mcp, int(tid, 16)), f'MCP_SetDefaultTitleId {tid}')
        return self.default_title()

    # MCP title list, from SDK coreinit.rpl (OS 1000400A) disassembly, 2026-11-14:
    #   MCP_TitleCount -> IOS_Ioctl(mcp, 0x4D, none, none); result = count.
    #   MCP_TitleList  -> IOS_Ioctl(mcp, 0x58, in = 0x61-byte search record (zero = all) + u32 0
    #                     at 0x61, out = count * 0x61); result = entries written.
    MCP_TITLE_COUNT, MCP_TITLE_LIST, MCP_TITLE_SIZE = 0x4D, 0x58, 0x61

    def title_list(self):
        """Installed titles as MCP reports them (wut MCPTitleListType layout)."""
        with self.device('/dev/mcp') as mcp:
            count = check(self.client.ioctl(mcp, self.MCP_TITLE_COUNT, [], 0)[0], 'MCP_TitleCount')
            if not count:
                return []
            if count > 4096:
                raise ValueError(f'implausible MCP title count {count}')
            ret, data = self.client.ioctl(mcp, self.MCP_TITLE_LIST, bytearray(0x65), count * self.MCP_TITLE_SIZE)
            written = check(ret, 'MCP_TitleList')
        if written > count:
            raise ValueError(f'MCP_TitleList wrote {written} entries into room for {count}')
        return [parse_title(data[i * self.MCP_TITLE_SIZE:(i + 1) * self.MCP_TITLE_SIZE]) for i in range(written)]

    # FSA GetInfo (ioctl 0x18) query types, as wut names them.
    QUERY_FREE_SPACE, QUERY_DEVICE_INFO, QUERY_STAT = 0, 4, 5

    def query(self, path, kind):
        """Raw FSA GetInfo payload for path (bytes after the 4-byte status word)."""
        from ..vendor.wupclient import buffer, copy_string, copy_word
        inbuf = buffer(0x520)
        copy_string(inbuf, path, 0x4)
        copy_word(inbuf, kind, 0x284)
        with self.device('/dev/fsa') as fsa:
            ret, data = self.client.ioctl(fsa, 0x18, inbuf, 0x293)
        check(ret, f'FSA GetInfo {kind} {path}')
        return data[4:]

    def space(self, volume):
        """Capacity and free bytes of a mounted volume, e.g. /vol/storage_mlc01.

        Device info: u64 sector count at +0x08, u32 sector size at +0x10 (wut FSADeviceInfo).
        Checked 2026-11-14 against the boot log's SLC line "Capacity 511 MB, 262016 logical
        blocks of size 2048 B". Free space: u64 at +0x00.
        """
        info = self.query(volume, self.QUERY_DEVICE_INFO)
        sectors, sector_size = struct.unpack('>QI', info[8:20])
        free = struct.unpack('>Q', self.query(volume, self.QUERY_FREE_SPACE)[:8])[0]
        capacity = sectors * sector_size
        if not sector_size or free > capacity:
            raise ValueError(f'{volume}: implausible sizes (capacity {capacity}, free {free})')
        return {'volume': volume, 'capacity': capacity, 'free': free,
                'sectors': sectors, 'sector_size': sector_size}

    def stat(self, path):
        """FSA stat (wut FSAStat): flags, mode, owner, group, size; directory = flag 0x80000000."""
        data = self.query(path, self.QUERY_STAT)
        flags, mode, owner, group, size = struct.unpack('>IIIII', data[:20])
        return {'path': path, 'directory': bool(flags & 0x80000000), 'flags': flags,
                'mode': mode, 'owner': owner, 'group': group, 'size': size}

    def listdir(self, path):
        """Names in a directory, with the FSA stat block each entry carries."""
        entries = []
        with self.device('/dev/fsa') as fsa:
            ret, handle = self.client.FSA_OpenDir(fsa, path)
            check(ret, f'open dir {path}')
            try:
                while True:
                    ret, entry = self.client.FSA_ReadDir(fsa, handle)
                    if ret != 0:      # end of directory (or error): wupclient gives no data
                        break
                    flags, mode, owner, group, size = struct.unpack('>IIIII', bytes(entry['unk'][:20]))
                    entries.append({'name': entry['name'], 'directory': bool(flags & 0x80000000),
                                    'size': size, 'mode': mode})
            finally:
                check(self.client.FSA_CloseDir(fsa, handle), f'close dir {path}')
        return sorted(entries, key=lambda e: (not e['directory'], e['name'].lower()))

    # No SD access. On 2026-11-14 a second FSA_Mount of /dev/sdcard01 (already attached by IOSU
    # at boot as "sdcard01 (fat)" and in use by MCP) returned success; the next GetInfo on it never
    # returned, NET then killed all sockets and the console stopped (serial silent, no wupserver).
    # Don't add mount helpers back without a safe, tested route.

    def probe(self):
        path = '/vol/system/config/system.xml'
        data = self.read_file(path)
        root = ET.fromstring(data)
        if root.tag != 'system':
            raise ValueError(f'Unexpected system.xml root: {root.tag}')
        return {'status': 'wupserver', 'proof': path, 'root': root.tag}


def cstr(raw):
    return bytes(raw).split(b'\0', 1)[0].decode('ascii', 'replace')


def parse_title(entry):
    """One 0x61-byte MCP title record: tid, group, path, app type, version, OS, SDK, device."""
    tid, group = struct.unpack('>QI', entry[0:12])
    app_type, version, os_version, sdk = struct.unpack('>IHQI', entry[0x44:0x56])
    return {'title': f'{tid:016x}', 'group': f'{group:08x}', 'path': cstr(entry[0x0C:0x44]),
            'app_type': f'{app_type:08x}', 'version': version, 'os_version': f'{os_version:016x}',
            'sdk': sdk, 'device': cstr(entry[0x56:0x60])}


def probe(ip, timeout=2):
    try:
        with Session(ip, timeout) as session:
            return {'ip': ip, **session.probe()}
    except ConnectionRefusedError as exc:
        return {'ip': ip, 'status': 'refused', 'error': str(exc)}
    except (TimeoutError, socket.timeout) as exc:
        return {'ip': ip, 'status': 'no-answer', 'error': str(exc)}
    except (OSError, ValueError, ET.ParseError) as exc:
        return {'ip': ip, 'status': 'unverified', 'error': str(exc)}
