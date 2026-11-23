# SparkCube

A small demo title: one spinning coloured cube on the TV and the GamePad, linked with the Cafe SDK's
`gx2spark` library. It shows how to take **SPARK** frame captures on a CAT-R, both to a file over PCFS and
live from SPARK on the PC.

| | |
|---|---|
| Title ID | `0005000010C0BE00` (normal app) |
| Version | 0.18 (title version 18) |
| OS | `000500101000400A` |

## What you need

- **To build:** the **Cafe SDK 2.12.13**, **GHS v5.3.27** and **devkitPro** (MSYS2's `make` and
  devkitPPC's `ar`), in folders without
  spaces in their paths, and Windows PowerShell 5.1. None of these are in this repository.
- **To capture:** a `gx2.rpl` on the console that accepts SPARK captures. The CAT-R's stock one refuses
  them and logs `Capture not supported in production mode`; the cube still spins.
  [`tools/gx2-patch`](../../tools/gx2-patch/README.md) patches the one on your console.
- **For captures to a file:** PCFS (see [app/README.md](../../app/README.md#pcfs)) with this repository's
  [`pcfsserver.exe`](../../pcfsserver/CHANGES.md). sprig's original server hangs on large captures.

## Build

```powershell
.\scripts\Build.ps1 -CafeRoot C:\path\to\Cafe_SDK-2.12.13 -GhsRoot C:\path\to\ghs
```

This writes `SparkCube-v18.wumad` and an install image in `Install\`. `-SkipPackage` only compiles.
`-CafeRoot` and `-GhsRoot` default to the `CAFE_ROOT` and `GHS_ROOT` variables; `-DevkitPro` defaults to
`C:\devkitPro`.

What the script does:

1. Compiles `shaders/` with the SDK's `gshCompile` and embeds the result as `source/spark_cube_gsh.h`.
2. Takes `commsManager.o` out of your SDK's `gx2spark.a` and renames its five HIO function names to `HSK*`
   in the copy, so it uses `source/hio_socket.c` instead. The SDK itself is not changed.
3. Compiles and links the title with the SDK makefiles.
4. Packages it with `makecfmaster`. The DEMO font, boot images and icon are copied from your SDK.

For a new version, change `<title_version>` in `metadata\code\app.xml` and `SPARK_CUBE_VERSION` in
`source\main.c`.

## Run it

Install it (**Install** in CatR-Tools.exe, or `catr install`) or run it straight from the PC:

```powershell
.\catr\catr.cmd run .\titles\spark-cube\master
```

### Capture to a file (PCFS)

Press **A** on the GamePad. The next frame is written to `data\save\spark\spark_cube.4mp` in your PCFS
folder. The `spark` folder must exist. A frame is about 20 MB and takes a while over Wi-Fi; the cube
pauses meanwhile. Each capture replaces the one before.

```powershell
.\catr\catr.cmd spark wait
```

```powershell
.\catr\catr.cmd spark open
```

In your own title, use the same kind of path, `/cygdrive/%SAVE_EMU_DIR/...`. Windows paths (`C:\...`,
`\\server\...`) don't work over PCFS.

### Live capture from SPARK

SPARK normally reaches the console through HIO and a MION, which a CAT-R doesn't have.
`source/hio_socket.c` carries SPARK's channel over TCP port 6100 instead, and `catr hio` connects it to
SPARK on the PC. Start the title, then:

```powershell
.\catr\catr.cmd hio
```

Then use **Capture** in SPARK. **Analyze** doesn't work.

Port 6100 has no password: use it only on a network you trust.

### Debugging with GDB or MULTI

Started with `dbgtry`, SparkCube turns on coreinit's GDB stub and waits for a debugger. This needs the
[debugger plugin](../../plugins/dk-tcp/README.md) on the SD card and its `dk_gdb.py` on the PC:

```powershell
.\catr\catr.cmd launch 0005000010C0BE00 dbgtry
```

`source/dbg_try.c` does the work, using fixed addresses in coreinit of OS `000500101000400A`.
Without `dbgtry` it does nothing.

## Use it in your own title

- Link `gx2spark` and call `GX2DebugCaptureInit(NULL)` after GX2 is set up, and
  `GX2DebugCaptureFrame(path)` to capture.
- For live capture, also add `source/hio_socket.c` and steps 1b and 2 of `scripts/Build.ps1`
  (`commsManager_hio.o`, linked ahead of `gx2spark.a` in the `Makefile`).
- For debugging, add `source/dbg_try.c` and call `dbg_try_start()` early in `main`.
- `GX2DebugTagUserString` labels show up in SPARK's event list.

## Serial log

`SparkCube v0.18 starting`, the launch arguments, `GX2DebugCaptureInit returned`,
`HIO-socket: listening on TCP 6100`, `cube ready, entering main loop`, then `alive, frame N` every 600
frames. Each **A** logs `capture request N at frame F`.
