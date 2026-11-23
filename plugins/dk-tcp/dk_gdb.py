"""Bridge coreinit's /dbgCtrl GDB stub to a local GDB port.

The console (5dktcp.ipx) listens on TCP 7332. A request is a DK01 frame whose
fifth word is (interface << 16) | client fd.

  0x20 with an 8-byte data input is the HIO worker's read, answered with
  the 8-byte open ack. Any larger read is the debugger: queued bytes from
  the GDB client, or a zero-length read when the client has not spoken.
  A character write whose bytes start with '$', '+' or '-' is a stub
  packet and is handed to the GDB client. Other writes are accepted and
  not forwarded.

GDB connects with, for example:

  target remote 127.0.0.1:3000

The stream is the stub's own packets, not a second encoding.
"""
import argparse
import socket
import struct
import sys
import threading
import time

REQ_MAGIC = 0x444B3031
RESP_MAGIC = 0x444B3032
NO_DATA = 0xFFF9FFF6


def recvall(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return bytes(buf)


DBGCTRL = 3          # /dev/dk/dbgCtrl, the stub's channel
HIO = 2              # /dev/dk/hio
IFACE_UNKNOWN = 0xFFFF


def read_request(sock):
    # The fifth word is (interface << 16) | client fd. Plugins before the
    # interface tag send the fd alone, which reads as interface 0.
    magic, cmd, nin, nout, fd = struct.unpack(">IIIII", recvall(sock, 20))
    if magic != REQ_MAGIC:
        raise ValueError(f"bad magic {magic:#x}")
    inputs = []
    for _ in range(nin):
        (length,) = struct.unpack(">I", recvall(sock, 4))
        inputs.append(recvall(sock, length))
    caps = list(struct.unpack(">" + "I" * nout, recvall(sock, 4 * nout))) if nout else []
    return cmd, fd, inputs, caps


def reply_bytes(caps, payload):
    choice = None
    for i, cap in enumerate(caps):
        if cap >= len(payload) and (choice is None or cap < caps[choice]):
            choice = i
    pieces = []
    for i, cap in enumerate(caps):
        if i == choice:
            pieces.append(struct.pack(">I", len(payload)) + payload)
        else:
            pieces.append(struct.pack(">I", 0))
    body = b"".join(pieces)
    return struct.pack(">III", RESP_MAGIC, 12 + len(body), 0) + body


def status_reply(caps, status, count, elem):
    raw = struct.pack(">III", status, count, elem)
    return reply_bytes(caps, raw if any(c >= 12 for c in caps) else b"")


def fill_each(caps, payload):
    pieces = []
    for cap in caps:
        if payload and cap >= len(payload):
            pieces.append(struct.pack(">I", len(payload)) + payload)
        elif payload and cap >= 4:
            pieces.append(struct.pack(">I", 4) + payload[:4])
        else:
            pieces.append(struct.pack(">I", 0))
    body = b"".join(pieces)
    return struct.pack(">III", RESP_MAGIC, 12 + len(body), 0) + body


def empty_reply(caps):
    body = b"".join(struct.pack(">I", 0) for _ in caps)
    return struct.pack(">III", RESP_MAGIC, 12 + len(body), 0) + body


STD_SIGNALS = {0x02, 0x05, 0x0B, 0x04, 0x08, 0x07, 0x0A, 0x06, 0x00, 0x0F, 0x09}


def fix_stop_signal(packet):
    """Rewrite a stop reply's signal the stub picks from its own numbering.

    The stub reports a halt as, for example, T9d. GDB shows that as
    signal '?' and then resumes with S8f, which the stub refuses. Any
    signal outside the usual POSIX set becomes 05 (SIGTRAP).
    """
    body = packet[1:-3]
    if len(body) >= 3 and body[:1] in (b"T", b"S"):
        try:
            sig = int(body[1:3], 16)
        except ValueError:
            return packet
        if sig not in STD_SIGNALS:
            body = body[:1] + b"05" + body[3:]
            csum = sum(body) & 0xFF
            return b"$" + body + b"#" + b"%02x" % csum
    return packet


def make_packet(body):
    return b"$" + body + b"#" + b"%02x" % (sum(body) & 0xFF)


def fix_detach(packet):
    """Turn GDB's detach into a continue.

    The stub's D handler replies OK and clears OSIsDebuggerPresent but
    leaves every core stopped in its command loop. 'c' also replies OK, so
    GDB sees the detach succeed and the title runs on. GDB removes its
    breakpoints before it detaches.
    """
    body = packet[1:-3]
    if body == b"D" or body.startswith(b"D;"):
        return make_packet(b"c")
    return packet


def parse_text_alias(spec):
    """'ORIG:SIZE:ALIAS' (hex) -> (orig, size, alias)."""
    parts = spec.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected ORIG:SIZE:ALIAS in hex")
    orig, size, alias = (int(p, 16) for p in parts)
    return orig, size, alias


def fix_text_write(packet, text_alias):
    """Point software breakpoints and memory writes in title text at its alias.

    Title text is mapped read-only and the stub's plain store of the trap
    word never completes. A title that could map the same physical pages
    read-write at another address can have them written there. The
    stub keeps the alias in its breakpoint table, so its reads, writes and
    cache flushes go through the writable mapping, and the trap still fires
    at the original address. Only a range that lies wholly in text is moved.
    """
    if not text_alias:
        return packet
    orig, size, alias = text_alias
    body = packet[1:-3]
    if body[:2] in (b"Z0", b"z0") and body[2:3] == b",":
        head, sep, rest = body[3:].partition(b",")
        if not sep:
            return packet
        try:
            addr = int(head, 16)
            length = 4
        except ValueError:
            return packet
        prefix, tail = body[:3], b"," + rest
    elif body[:1] in (b"M", b"X"):
        head, sep, rest = body[1:].partition(b",")
        lenpart, colon, _ = rest.partition(b":")
        if not sep or not colon:
            return packet
        try:
            addr = int(head, 16)
            length = int(lenpart, 16)
        except ValueError:
            return packet
        prefix, tail = body[:1], b"," + rest
    else:
        return packet
    if not (orig <= addr and addr + length <= orig + size):
        return packet
    new = prefix + b"%x" % (addr - orig + alias) + tail
    return make_packet(new)


def parse_bp(body):
    """'Z0,addr,kind' and friends -> (type char, set?, addr) or None."""
    if len(body) < 4 or body[:1] not in (b"Z", b"z") or body[2:3] != b",":
        return None
    head = body[3:].split(b",")[0]
    try:
        return body[1:2], body[:1] == b"Z", int(head, 16)
    except ValueError:
        return None


def is_resume(body):
    return (body[:1] in (b"c", b"C", b"s", b"S")
            or (body.startswith(b"vCont;") and len(body) > 6))


class Bridge:
    """Byte pump between GDB and the stub.

    While the target runs, the stub polls every 100 ms (coreinit 0x02068bac).
    That poll resets the stub's read index whenever the buffer is not used up
    (0x02039250), so a read that starts with anything but '$' (for example
    GDB's '+' ack followed by a packet) makes the stub core spin forever. So
    while the target is not known to be halted, acks from GDB are dropped,
    and each read the stub gets starts with '$' or is a lone ^C.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.to_console = bytearray()
        self.client = None
        self.printed = {}
        self.stub_buf = bytearray()
        self.gdb_buf = bytearray()
        self.halted = False          # not known: treat as running
        self.resume_sent = False
        # The stub waits for GDB's '+' on the OK that answers a resume before
        # it leaves its command loop, so that one ack must reach it, alone,
        # while it still counts as halted.
        self.ack_then_run = False
        self.text_alias = None       # (orig, size, alias) from --text-alias
        # The stub has one instruction breakpoint (IABR) and its software
        # breakpoints hang it, so the first 'break' becomes that IABR.
        # iabr is None, ("sw", addr) for a converted Z0, or ("hw", addr).
        self.sw_to_hw = True
        self.iabr = None
        self.skip_acks = 0           # GDB's acks for replies made here
        self.log_client = False      # --log-client: print every packet from GDB/MULTI

    def push_from_stub(self, data):
        # Pass acks through; hold '$...#xx' packets until complete so a stop
        # reply can be rewritten.
        out = bytearray()
        self.stub_buf += data
        while self.stub_buf:
            if self.stub_buf[0] != ord("$"):
                out.append(self.stub_buf[0])
                del self.stub_buf[0]
                continue
            end = self.stub_buf.find(b"#")
            if end < 0 or len(self.stub_buf) < end + 3:
                break
            pkt = bytes(self.stub_buf[:end + 3])
            del self.stub_buf[:end + 3]
            fixed = fix_stop_signal(pkt)
            if fixed != pkt:
                print(f"stop reply {pkt[:12]!r} -> {fixed[:12]!r}", flush=True)
            self.note_stub_packet(pkt[1:-3])
            out += fixed
        data = bytes(out)
        with self.lock:
            client = self.client
        if client is None or not data:
            return
        try:
            client.sendall(data)
        except OSError:
            self.drop_client()

    def note_stub_packet(self, body):
        with self.lock:
            if self.resume_sent and body == b"OK":
                # The stub answers a resume with OK, waits for the ack, and
                # then runs (see take_for_console).
                self.ack_then_run = True
                self.resume_sent = False
            elif body[:1] == b"O" and body != b"OK" and not self.halted:
                pass                 # console output while running
            else:
                self.halted = True
                self.resume_sent = False
                self.ack_then_run = False

    def take_for_console(self, limit):
        with self.lock:
            if not self.to_console or limit <= 0:
                return b""
            if self.halted and self.ack_then_run and self.to_console[0] == ord("+"):
                # The ack for a resume's OK: hand it over alone, then the
                # target runs.
                del self.to_console[0]
                self.ack_then_run = False
                self.halted = False
                return b"+"
            if not self.halted:
                # Running: start every read at '$', or hand over ^C alone.
                while self.to_console and self.to_console[0] not in (ord("$"), 3):
                    del self.to_console[0]
                if not self.to_console:
                    return b""
                if self.to_console[0] == 3:
                    del self.to_console[0]
                    return b"\x03"
                nxt = self.to_console.find(b"\x03")
                if 0 < nxt < limit:
                    limit = nxt
            out = bytes(self.to_console[:limit])
            del self.to_console[:limit]
            return out

    def add_from_gdb(self, data):
        # ^C passes straight through, and acks too while the target is halted.
        # '$...#xx' packets are held until complete so a detach can be
        # rewritten and a resume noticed.
        out = bytearray()
        self.gdb_buf += data
        while self.gdb_buf:
            if self.gdb_buf[0] != ord("$"):
                b = self.gdb_buf[0]
                del self.gdb_buf[0]
                if self.log_client and b not in (ord("+"), ord("-")):
                    print(f"client raw {bytes([b])!r}", flush=True)
                if b == ord("+") and self.skip_acks:
                    self.skip_acks -= 1
                    continue
                if b in (ord("+"), ord("-")) and not self.halted:
                    continue
                out.append(b)
                continue
            end = self.gdb_buf.find(b"#")
            if end < 0 or len(self.gdb_buf) < end + 3:
                break
            pkt = bytes(self.gdb_buf[:end + 3])
            del self.gdb_buf[:end + 3]
            if self.log_client:
                print(f"client {pkt[:60]!r}", flush=True)
            fixed = fix_detach(pkt)
            if fixed != pkt:
                print(f"detach {pkt!r} -> {fixed!r} (the stub's D leaves the title stopped)",
                      flush=True)
            moved = fix_text_write(fixed, self.text_alias)
            if moved != fixed:
                print(f"text write {fixed[:40]!r} -> {moved[:40]!r}", flush=True)
                fixed = moved
            else:
                fixed = self.map_breakpoint(fixed)
                if fixed is None:
                    continue
            if is_resume(fixed[1:-3]):
                with self.lock:
                    self.resume_sent = True
            out += fixed
        if out:
            with self.lock:
                self.to_console += out

    def map_breakpoint(self, packet):
        """Track the one IABR; turn Z0/z0 into Z1/z1 while it is free.

        Returns the packet to send on, or None when it was answered here.
        """
        if not self.sw_to_hw:
            return packet
        body = packet[1:-3]
        bp = parse_bp(body)
        if bp is None or bp[0] not in (b"0", b"1"):
            return packet
        kind, insert, addr = bp
        owner = "sw" if kind == b"0" else "hw"
        if insert:
            if self.iabr is not None and self.iabr != (owner, addr):
                used = self.iabr
                print(f"breakpoint {body!r} refused: the one IABR holds "
                      f"{used[0]} {used[1]:08x}", flush=True)
                self.reply_here(b"E0e")
                return None
            self.iabr = (owner, addr)
        elif self.iabr == (owner, addr):
            self.iabr = None
        elif kind == b"0":
            # Not the one we hold: nothing was inserted, so nothing to remove.
            self.reply_here(b"OK")
            return None
        if kind == b"0":
            new = make_packet((b"Z1" if insert else b"z1") + body[2:])
            print(f"breakpoint {body!r} -> {new[1:-3]!r} (one IABR; Z0 hangs the stub)",
                  flush=True)
            return new
        return packet

    def reply_here(self, body):
        with self.lock:
            client = self.client
            self.skip_acks += 1
        if client is None:
            return
        try:
            client.sendall(b"+" + make_packet(body))
        except OSError:
            self.drop_client()

    def set_client(self, sock):
        with self.lock:
            old = self.client
            self.client = sock
            self.gdb_buf = bytearray()
            self.to_console = bytearray()
            self.skip_acks = 0
        if old is not None:
            try:
                old.close()
            except OSError:
                pass

    def drop_client(self):
        with self.lock:
            old = self.client
            self.client = None
        if old is not None:
            try:
                old.close()
            except OSError:
                pass

    def note(self, cmd, fd, inputs):
        key = (cmd, fd)
        n = self.printed.get(key, 0) + 1
        self.printed[key] = n
        # SMD reads poll every 10 ms; show the first few and then rarely.
        if cmd == 0x140 and n > 4 and n % 1000:
            return
        if n <= 8 or n % 500 == 0 or cmd == 0x141:
            shown = b"".join(data_inputs(inputs))[:40]
            print(f"cmd={cmd:#x} #{n} iface={fd >> 16:#x} fd={fd & 0xFFFF:#x} "
                  f"lens={[len(b) for b in inputs]} {shown!r}", flush=True)


def data_inputs(inputs):
    return [b for b in inputs if len(b) != 12]


def is_hio_read(inputs):
    data = data_inputs(inputs)
    return bool(data) and max(len(b) for b in data) <= 8


def is_stub_packet(payload):
    return bool(payload) and payload[0] in (ord("$"), ord("+"), ord("-"))


STOCK_FAIL = struct.pack(">III", RESP_MAGIC, 12, 1)


def serve_console(conn, bridge):
    while True:
        cmd, fd, inputs, caps = read_request(conn)
        iface = fd >> 16
        bridge.note(cmd, fd, inputs)
        shown = b"".join(data_inputs(inputs) or inputs)
        if cmd == 0x140:
            # SMD read held by the plugin: queued GDB bytes, or 0 bytes to
            # keep it held for the next 10 ms tick.
            payload = bridge.take_for_console(caps[0] if caps else 0)
            msg = reply_bytes(caps, payload) if payload else empty_reply(caps)
        elif cmd == 0x141:
            # SMD write: the stub's bytes, straight to GDB.
            bridge.push_from_stub(b"".join(inputs))
            msg = struct.pack(">III", RESP_MAGIC, 12, 0)
        elif cmd == 0x10:
            msg = status_reply(caps, 0, len(shown), 1 if shown else 0)
        elif cmd == 0x11:
            msg = status_reply(caps, NO_DATA, 0, 0)
        elif cmd in (0x21, 0x41):
            # The stub may write a packet a byte at a time, so every byte on
            # /dbgCtrl goes to GDB. Other channels' writes are accepted and
            # dropped (fd 1's is the console log, already on serial).
            if iface == DBGCTRL:
                bridge.push_from_stub(shown)
            msg = fill_each(caps, struct.pack(">II", 0, len(shown)))
        elif cmd == 0x20:
            if iface == DBGCTRL:
                data_room = max((len(b) for b in data_inputs(inputs)), default=0)
                out_room = min((c for c in caps if c), default=0)
                payload = bridge.take_for_console(min(data_room, out_room))
                msg = fill_each(caps, payload) if payload else empty_reply(caps)
            elif is_hio_read(inputs):
                msg = fill_each(caps, struct.pack("<II", 11, 0))
            else:
                # No host behind this channel: stock no-card failure, so a
                # reader does not take an empty read as a command.
                msg = STOCK_FAIL
        elif cmd == 0x30:
            msg = fill_each(caps, struct.pack(">I", 0))
        else:
            msg = STOCK_FAIL
        conn.sendall(msg)


def gdb_clients(port, bridge):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(1)
    print(f"gdb listening on 127.0.0.1:{port}", flush=True)
    while True:
        client, addr = server.accept()
        print(f"gdb client {addr[0]}:{addr[1]}", flush=True)
        bridge.set_client(client)
        try:
            while True:
                data = client.recv(4096)
                if not data:
                    break
                bridge.add_from_gdb(data)
        except OSError:
            pass
        finally:
            bridge.drop_client()
            print("gdb client closed", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Bridge /dbgCtrl to a local GDB port")
    parser.add_argument("ip", help="the console's IP address")
    parser.add_argument("--port", type=int, default=7332)
    parser.add_argument("--gdb-port", type=int, default=3000)
    parser.add_argument("--halted", action="store_true",
                        help="start with the target known to be stopped in the stub, "
                             "so GDB's acks pass from the first byte")
    parser.add_argument("--log-client", action="store_true",
                        help="print every packet the debugger (GDB, MULTI's cafeserv) sends")
    parser.add_argument("--no-sw-to-hw", dest="sw_to_hw", action="store_false",
                        help="pass Z0 (software breakpoints) on as they are; by default "
                             "the first one becomes the stub's one hardware breakpoint")
    parser.add_argument("--text-alias", type=parse_text_alias, metavar="ORIG:SIZE:ALIAS",
                        help="hex; move Z0/z0/M/X in title text to a writable alias "
                             "(experimental: the console refused SparkCube's alias)")
    args = parser.parse_args()
    bridge = Bridge()
    bridge.halted = args.halted
    bridge.text_alias = args.text_alias
    bridge.log_client = args.log_client
    bridge.sw_to_hw = args.sw_to_hw and not args.text_alias
    threading.Thread(target=gdb_clients, args=(args.gdb_port, bridge), daemon=True).start()
    while True:
        try:
            sock = socket.create_connection((args.ip, args.port), timeout=5)
        except OSError as exc:
            print(f"waiting for {args.ip}:{args.port} ({exc})", flush=True)
            time.sleep(1)
            continue
        sock.settimeout(None)
        print(f"connected to {args.ip}:{args.port}", flush=True)
        try:
            serve_console(sock, bridge)
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"disconnected: {exc}", flush=True)
        finally:
            sock.close()


if __name__ == "__main__":
    sys.exit(main())
