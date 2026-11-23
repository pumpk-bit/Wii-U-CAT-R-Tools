"""IOSU syslog over wupserver memory reads.

On this IOSU (fw.img sha256 d360a505...) MCP keeps a pointer to the syslog buffers at
0x05096ED4 (Mocha's gSyslogBuffers for 5.5.x). They are two ring buffers of 0x40000 bytes:

    header  u32 magic 0xDE515109, u32 entries (2), u32 active entry, u32 checksum
    entry i u32 write offset, u32 read offset, u32 capacity 0x40000, u32 data pointer, data[0x40000]

Seen 2026-11-15: pointer 0x1FF7FFD0, i.e. the last 0x80030 bytes below 0x20000000 (fw.img
segment 36, MCP read/write). The active entry holds this boot from "Starting System Log".
Mocha says the entries alternate on every warm boot, keeping the previous boot in the other
one; on this console entry 1 stayed empty after several PM_RESTARTs (slowlaunch), so
`--previous` may well show nothing.

Every address is checked before it is read: wupserver reads are a raw memcpy in MCP, and a bad
address crashes IOSU (as `cos memdump 0xDEAD0000` crashed the PPC kernel).
"""
import struct

POINTER = 0x05096ED4
# MCP code that loads the pointer; its literal pool must read back exactly like this, otherwise
# this is another IOSU build and the address means nothing.
SIGNATURE_AT = 0x05028F94
SIGNATURE = bytes.fromhex('05096ed4050b7f0005067a1800080030')
REGION = (0x1FE40000, 0x20000000)      # fw.img segment 36 (MCP, RW): the buffers must lie inside
MAGIC = 0xDE515109
CAPACITY = 0x40000
ENTRY = 0x10 + CAPACITY
TOTAL = 0x10 + 2 * ENTRY
CHUNK = 0x8000


def inside(address, size):
    return REGION[0] <= address and address + size <= REGION[1]


def parse_header(data):
    magic, entries, active, checksum = struct.unpack('>4I', data[:16])
    if magic != MAGIC or entries != 2 or active not in (0, 1):
        raise ValueError(f'unexpected syslog header: magic {magic:#x}, entries {entries}, active {active}')
    return {'magic': magic, 'entries': entries, 'active': active, 'checksum': checksum}


def parse_entry(data, base):
    write, read, capacity, pointer = struct.unpack('>4I', data[:16])
    if capacity != CAPACITY or pointer != base + 0x10 or write >= capacity or read >= capacity:
        raise ValueError(f'unexpected syslog entry at {base:#x}: write {write:#x} read {read:#x} '
                         f'capacity {capacity:#x} data {pointer:#x}')
    return {'write': write, 'read': read, 'data': pointer}


def ordered(raw, write):
    """Text in time order. After a wrap the oldest bytes start at the write offset."""
    wrapped = any(raw[write:])
    text = (raw[write:] + raw[:write]) if wrapped else raw[:write]
    return text.replace(b'\0', b''), wrapped


def locate(read):
    """(buffer address, header) using read(address, size); raises before any unchecked read."""
    if read(SIGNATURE_AT, len(SIGNATURE)) != SIGNATURE:
        raise ValueError(f'MCP code at {SIGNATURE_AT:#x} differs from the known IOSU build; '
                         'not following the syslog pointer')
    address = struct.unpack('>I', read(POINTER, 4))[0]
    if not inside(address, TOTAL):
        raise ValueError(f'syslog pointer {address:#x} is outside {REGION[0]:#x}-{REGION[1]:#x}; not read')
    return address, parse_header(read(address, 16))


def fetch(read, previous=False):
    """{'entry', 'boot', 'write', 'wrapped', 'text'} for the active entry, or the other one."""
    address, header = locate(read)
    index = header['active'] ^ 1 if previous else header['active']
    base = address + 0x10 + index * ENTRY
    entry = parse_entry(read(base, 16), base)
    raw = b''.join(read(entry['data'] + offset, CHUNK) for offset in range(0, CAPACITY, CHUNK))
    text, wrapped = ordered(raw, entry['write'])
    return {'entry': index, 'boot': 'previous' if previous else 'current', 'address': f'{base:#x}',
            'write': entry['write'], 'wrapped': wrapped, 'text': text}
