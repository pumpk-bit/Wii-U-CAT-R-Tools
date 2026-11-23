# pcfsserver.exe

`pcfsserver.exe` is the PC side of PCFS: it serves a folder on the PC to a CAT-R over the network, so
the console can run titles from it. catr starts it for every PCFS connection, and CatR-Tools.exe has it
built in.

It is the `pcfsserver` program from **[sprig](https://codeberg.org/rem-verse/sprig)** by **Cynthia Coan**
(rem-verse), with the changes below. All of the PCFS server itself (the protocol, the host file
system, the SATA/PCFS request handlers and the TCP server) is sprig's work. The changes here only make it
connect to a CAT-R over Wi-Fi and fix the cases a CAT-R running real titles ran into.

| File | What it is |
|---|---|
| `pcfsserver.exe` | The build used by catr and CatR-Tools.exe (Windows x64). SHA-256 `4a8acd26edac36e517488d259ffdfbfad193b2386eb88e96d87292cd70d04a13` |
| `sprig-pcfsserver.patch` | The changes, as a diff against sprig commit `b8e9cadbe0350c7533a5aadafabecaaba6d902ae` (trunk, 2026-09-04, version 0.0.14) |
| `LICENSE-sprig` | sprig's license (MIT, Copyright 2023-2026 Cynthia Coan), for sprig's code in the exe and the patch. |
| `THIRD-PARTY-NOTICES.txt` | The Rust libraries linked into the exe, with their licenses |

## Changes

### Command line and connecting out (`cmd/pcfsserver/src/main.rs`)

```
pcfsserver <console-ip> [port] [pcfs-root]
```

- The console's IP address is required, the port defaults to **7500**, and the third argument is the
  folder to serve.
- The server **connects out to the console** (`server.connect()`) instead of waiting for a connection.
  A CAT-R running the PCFS-over-TCP plugin listens on port 7500 for about 10 seconds early in each boot,
  and the PC has to dial it in that window. No inbound firewall rule is needed.
- It prints `Connecting PCFS host to console at IP:PORT` and exits with code 1 if the connection
  fails, so catr can retry at the next boot.

### Large writes (`pkg/cat-dev/src/fsemul/pcfs/sata/server/write_file.rs`)

Upstream handled only writes whose data follows the request at once. A write larger than the console's
"first write size" (65184 bytes on a CAT-R) deadlocked: the console waits for a reply before it sends
the data. That froze any title writing a big file, such as a SPARK capture.

Now, for such writes, the server first sends a 0x24-byte "first response" (a 0x20-byte header plus the
total length, big-endian), then reads the data, then sends the normal result. This follows what the
Cafe SDK's own PCFS server does (`PCFSSrvWriteFile` in SDK 2.12.13's `PCFSServerDLL.dll`), found by
analysing that DLL. None of Nintendo's code is included. If the data never arrives, the server gives up after **120 seconds** and returns an error to the console instead of
hanging.

### WriteFile result is a block count (`write_file.rs`)

The final WriteFile result is now the number of **blocks** written (bytes divided by the request's
block size), as the console expects and as `PCFSSrvWriteFile` computes it. Upstream returned bytes, which only matches for 1-byte blocks.
For example, a title saving its `meta/iconTex.tga` as one 0x10000-byte block got 65536 back instead of
1, logged `FSA: Warning: unexpected non-negative status: 65536 != 1(count)` and retried forever.
ReadFile still returns bytes, as the console expects there.

### Stream helpers (`pkg/cat-dev/src/net/models.rs`)

- `Request::unsafe_write_bytes_to_stream` writes raw bytes in the middle of a request (for the first
  response above).
- `unsafe_read_more_bytes_from_stream` returns an error at end of stream instead of looping forever,
  and can't underflow its byte count.

### Tracing (`pkg/cat-dev/src/net/private.rs`, `pkg/cat-dev/src/net/server/tcp.rs`)

With the `PCFS_TRACE` environment variable set, every TCP read and write and every request is printed
to stderr in hex (the first 96 bytes), plus WriteFile details. This works in release builds.
`catr pcfs up --trace` and `catr run --trace` set it; the output goes to the server's log.

## Building it yourself

You need Rust (edition 2024) and a clone of sprig:

```powershell
git clone https://codeberg.org/rem-verse/sprig.git
cd sprig
git checkout b8e9cadbe0350c7533a5aadafabecaaba6d902ae
git apply ..\Wii-U-CAT-R-Tools\pcfsserver\sprig-pcfsserver.patch
cargo build --release -p pcfsserver
```

The result is `target\release\pcfsserver.exe`. To use your own build, pass it to `app\build.ps1` with
`-PcfsServer`, or point catr at it with the `CATR_PCFS_SERVER` variable.

The `pcfsserver.exe` here was built from a copy of sprig with these changes. Compared with that commit,
`pkg/cat-dev` and `cmd/pcfsserver` differ only by this patch and by `pkg/cat-dev/src/net/server/mod.rs`,
which was rewritten by hand and differs only in comments and in using `super::` paths for the same test
helpers (the patch leaves upstream's file as it is). The rest of the copy, such as the root `Cargo.toml`
and `Cargo.lock`, was not compared.

It was built with Rust 1.99.1 (`--offline --locked`) and with the build PC's folder names replaced in
its panic messages (rustc's `--remap-path-prefix`), so it does not match a plain `cargo build` byte for
byte.
