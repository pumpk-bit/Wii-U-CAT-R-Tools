"""Download gx2.rpl, NOP its SPARK console-type gate, and upload it back.

In OS title 000500101000400A
(SDK 2.13.01) gx2.rpl, _GX2DebugSetCaptureInterface at 0x02007358. That word is
`bne 0x02007450`, the branch taken when OSGetConsoleType() is neither
0x13000048 nor 0x13000040. It becomes a nop, so a dev-security console of
another type can fall through into the capture-interface checks. The security
level check at 0x02007330 is left as it is.

Only .text changes. It is recompressed and its RPL CRC is updated. If it no
longer fits in its place (Python 3.14's zlib compresses a little worse), the
sections after it move back. The script refuses any other instruction stream.
A copy of the downloaded file is kept before the upload, and the uploaded file
is read back and compared.

gx2.rpl is mapped at boot. The running OS keeps the old copy until reboot.
If a title fails to start afterwards, upload the saved backup with the same
STOR (this FTP server rejects renames).

Usage:
  py -3 -I patch_gx2_spark_gate.py 192.0.2.50 --dry-run
  py -3 -I patch_gx2_spark_gate.py 192.0.2.50
  py -3 -I patch_gx2_spark_gate.py 192.0.2.50 --path /storage_slc/sys/title/00050010/1000400a/code/gx2.rpl
"""

import argparse
import hashlib
import io
import struct
import sys
import zlib
from ftplib import FTP, error_perm
from pathlib import Path

PATCH_VA = 0x02007358
EXPECTED_BRANCH = 0x408200F8  # bne 0x02007450
NOP = 0x60000000
# Words that identify this build of _GX2DebugSetCaptureInterface. The patched
# site itself is checked separately so an already-patched file still matches.
CONTEXT = {
    0x02007330: 0x40820120,  # bne 0x02007450, OSGetSecurityLevel() != 1
    0x02007338: 0x3D201300,  # lis r9, 0x1300
    0x0200733C: 0x39290048,  # addi r9, r9, 0x48
    0x02007344: 0x41820018,  # beq 0x0200735c
    0x0200734C: 0x3D401300,  # lis r10, 0x1300
    0x02007350: 0x394A0040,  # addi r10, r10, 0x40
    0x02007354: 0x7C035040,  # cmplw r3, r10
    0x0200735C: 0x2C1F0000,  # cmpwi r31, 0
}
RPL_ZLIB = 0x08000000
SHT_RPL_CRCS = 0x80000003
SHT_NOBITS = 8
FILE_ALIGN = 0x40
# Downloaded and patched copies: Nintendo's code, so in a folder that git ignores.
DEFAULT_WORK = Path(__file__).resolve().parents[2] / "workspace" / "gx2-patch"
DEFAULT_PATH = "/storage_slc/sys/title/00050010/1000400a/code/gx2.rpl"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def section_headers(data):
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF/RPL")
    shoff = struct.unpack_from(">I", data, 32)[0]
    shentsize, shnum, _ = struct.unpack_from(">HHH", data, 46)
    if shentsize < 40 or shnum < 2 or shoff + shnum * shentsize > len(data):
        raise ValueError("truncated RPL section table")
    headers = []
    for i in range(shnum):
        off = shoff + i * shentsize
        headers.append((off, list(struct.unpack_from(">10I", data, off))))
    return shentsize, headers


def section_bytes(data, header):
    typ, off, size = header[1], header[4], header[5]
    if typ == SHT_NOBITS or size == 0:
        return b""
    if off + size > len(data):
        raise ValueError(f"section extends past end of file (off={off:#x} size={size:#x})")
    return data[off:off + size]


def decompressed(header, raw):
    flags = header[2]
    if not raw:
        return b""
    if flags & RPL_ZLIB:
        expected = struct.unpack_from(">I", raw)[0]
        body = zlib.decompress(raw[4:])
        if len(body) != expected:
            raise ValueError(f"zlib size prefix {expected:#x} != decompressed {len(body):#x}")
        return body
    return raw


def find_text_and_crc(headers):
    text_i = crc_i = None
    for i, (_, header) in enumerate(headers):
        typ, flags, addr, _, size = header[1], header[2], header[3], header[4], header[5]
        if typ == 1 and flags & 4 and addr <= PATCH_VA < addr + max(size, 0x100000):
            text_i = i
        if typ == SHT_RPL_CRCS:
            crc_i = i
    if text_i is None or crc_i is None:
        raise ValueError("could not find .text or the RPL CRC section")
    return text_i, crc_i


def word_at(text, text_addr, va):
    off = va - text_addr
    if off < 0 or off + 4 > len(text):
        raise ValueError(f"{va:#010x} is outside .text")
    return struct.unpack_from(">I", text, off)[0]


def patch_rpl(data):
    """Return (patched bytes, status). status is 'patched' or 'already'."""
    _, headers = section_headers(data)
    text_i, crc_i = find_text_and_crc(headers)
    text_off, text_h = headers[text_i]
    text_addr = text_h[3]
    text_file_off, text_file_size = text_h[4], text_h[5]
    if not text_h[2] & RPL_ZLIB:
        raise ValueError(".text is not zlib-compressed")
    text = bytearray(decompressed(text_h, section_bytes(data, text_h)))

    for va, expected in CONTEXT.items():
        found = word_at(text, text_addr, va)
        if found != expected:
            raise ValueError(
                f"{va:#010x}: expected {expected:#010x}, found {found:#010x} "
                "(not the 2.13.01 gx2.rpl this patch was written for)"
            )
    current = word_at(text, text_addr, PATCH_VA)
    if current == NOP:
        return data, "already"
    if current != EXPECTED_BRANCH:
        raise ValueError(
            f"{PATCH_VA:#010x}: expected {EXPECTED_BRANCH:#010x} (bne) or {NOP:#010x} (nop), "
            f"found {current:#010x}"
        )

    struct.pack_into(">I", text, PATCH_VA - text_addr, NOP)
    packed = struct.pack(">I", len(text)) + zlib.compress(bytes(text), 9)
    later = [(off, h) for off, h in headers if h[1] != SHT_NOBITS and h[5] and h[4] > text_file_off]
    if not later:
        raise ValueError("no section follows .text; refusing to grow the file")
    next_off = min(h[4] for _, h in later)
    if any(h[4] < text_file_off + text_file_size for _, h in later):
        raise ValueError("a section overlaps .text")
    # Move what follows back by whole FILE_ALIGN steps, so every section keeps its file alignment and order.
    over = max(0, text_file_off + len(packed) - next_off)
    shift = -(-over // FILE_ALIGN) * FILE_ALIGN
    if any(off >= text_file_off for off, _ in headers):
        raise ValueError("the section table is not in front of .text")

    room = next_off + shift - text_file_off
    out = bytearray(data[:text_file_off]) + packed + b"\0" * (room - len(packed)) + data[next_off:]
    text_h[5] = len(packed)
    struct.pack_into(">10I", out, text_off, *text_h)
    for off, h in later:
        h[4] += shift
        struct.pack_into(">10I", out, off, *h)

    crc_file_off = headers[crc_i][1][4]
    crc_pos = crc_file_off + text_i * 4
    struct.pack_into(">I", out, crc_pos, zlib.crc32(bytes(text)) & 0xFFFFFFFF)

    verify_crcs(out)
    patched_text = decompressed(text_h, section_bytes(out, text_h))
    if word_at(patched_text, text_addr, PATCH_VA) != NOP:
        raise ValueError("patch did not land in the recompressed .text")
    if word_at(patched_text, text_addr, 0x02007330) != CONTEXT[0x02007330]:
        raise ValueError("security-level branch was modified")
    return bytes(out), "patched"


def verify_crcs(data):
    _, headers = section_headers(data)
    _, crc_i = find_text_and_crc(headers)
    crc_raw = section_bytes(data, headers[crc_i][1])
    for i, (_, header) in enumerate(headers):
        if i == crc_i or header[1] == SHT_NOBITS:
            continue
        if i * 4 + 4 > len(crc_raw):
            break
        stored = struct.unpack_from(">I", crc_raw, i * 4)[0]
        body = decompressed(header, section_bytes(data, header))
        calc = zlib.crc32(body) & 0xFFFFFFFF
        if stored != calc:
            raise ValueError(f"CRC mismatch on section {i}: stored {stored:#010x}, calculated {calc:#010x}")


def ftp_connect(host, port):
    ftp = FTP()
    ftp.connect(host, port, timeout=30)
    ftp.login("anonymous", "")
    ftp.set_pasv(True)
    return ftp


def ftp_retr(ftp, remote):
    buf = io.BytesIO()
    ftp.retrbinary("RETR " + remote, buf.write)
    return buf.getvalue()


def split_remote(path):
    path = path.replace("\\", "/")
    if not path.startswith("/"):
        path = "/" + path
    directory, _, name = path.rpartition("/")
    if not directory or not name:
        raise ValueError(f"remote path needs a directory and a file name: {path}")
    return directory, name


def download(host, port, remote):
    directory, name = split_remote(remote)
    ftp = ftp_connect(host, port)
    try:
        try:
            ftp.cwd(directory)
        except error_perm as exc:
            root = []
            try:
                ftp.cwd("/")
                root = ftp.nlst()
            except error_perm:
                pass
            shown = ", ".join(root) if root else "(could not list /)"
            raise SystemExit(
                f"FTP cannot open {directory}: {exc}\n"
                f"Server root entries: {shown}\n"
                "Pass the gx2.rpl path this server actually exposes with --path."
            ) from exc
        data = ftp_retr(ftp, name)
    finally:
        ftp.close()
    if len(data) < 64:
        raise SystemExit(f"download of {remote} is only {len(data)} bytes")
    return data


def upload_and_readback(host, port, remote, payload):
    directory, name = split_remote(remote)
    ftp = ftp_connect(host, port)
    try:
        ftp.cwd(directory)
        ftp.storbinary("STOR " + name, io.BytesIO(payload))
        readback = ftp_retr(ftp, name)
    finally:
        ftp.close()
    return readback


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("host", help="the console's IP address")
    parser.add_argument("--port", type=int, default=21)
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK,
                        help="where the downloaded and patched copies are kept (%(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="download and patch locally, do not upload")
    parser.add_argument("--yes", action="store_true", help="upload without asking")
    args = parser.parse_args()

    args.work.mkdir(parents=True, exist_ok=True)
    print(f"downloading {args.path} from {args.host}:{args.port}")
    original = download(args.host, args.port, args.path)
    digest = sha256(original)
    saved = args.work / f"gx2.rpl.downloaded-{digest[:12]}"
    saved.write_bytes(original)
    print(f"saved {saved} ({len(original)} bytes, sha256 {digest})")

    try:
        verify_crcs(original)
    except ValueError as exc:
        raise SystemExit(f"downloaded file failed CRC check, left it untouched: {exc}") from exc

    try:
        patched, status = patch_rpl(original)
    except ValueError as exc:
        raise SystemExit(f"refusing to patch: {exc}") from exc

    if status == "already":
        print(f"{PATCH_VA:#010x} is already nop. Console file left unchanged.")
        return

    patched_path = args.work / f"gx2.rpl.patched-{sha256(patched)[:12]}"
    patched_path.write_bytes(patched)
    print(
        f"patched {PATCH_VA:#010x}: {EXPECTED_BRANCH:#010x} -> {NOP:#010x}; "
        f"wrote {patched_path} ({len(patched)} bytes, sha256 {sha256(patched)})"
    )
    if args.dry_run:
        print("dry-run: not uploaded")
        return

    if not args.yes:
        answer = input(f"Replace {args.path} on {args.host}? Type yes: ")
        if answer.strip().lower() != "yes":
            raise SystemExit("not uploaded")

    print(f"uploading to {args.path}")
    problem = "readback does not match the patched file"
    try:
        readback = upload_and_readback(args.host, args.port, args.path, patched)
    except Exception as exc:
        readback = None
        problem = f"upload failed: {exc}"
    if readback != patched:
        print(f"{problem}. Putting the downloaded original back.", file=sys.stderr)
        by_hand = (
            "DO NOT RESTART THE CONSOLE: its gx2.rpl may be damaged. "
            f"Upload {saved} to {args.path} by hand and check that it arrived whole."
        )
        try:
            restored = upload_and_readback(args.host, args.port, args.path, original)
        except Exception as exc:
            raise SystemExit(f"putting the original back failed: {exc}\n{by_hand}") from exc
        if restored != original:
            raise SystemExit(f"the original did not read back correctly.\n{by_hand}")
        raise SystemExit("the original file is back in place; nothing was changed")

    print(f"readback matches ({len(readback)} bytes). Reboot before the new gx2.rpl is loaded.")
    print(f"to restore: upload {saved} to {args.path}")


if __name__ == "__main__":
    main()
