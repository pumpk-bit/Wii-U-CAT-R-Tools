# Debugger over TCP (`5dktcp.ipx`)

Lets a debugger on the PC (GDB, or Green Hills MULTI) debug a title on a CAT-R that has no MION.

coreinit's own GDB stub normally talks to the PC through `/dev/dk` and the MION. This
[stroopwafel](https://github.com/StroopwafelCFW/stroopwafel) plugin carries that traffic over TCP port
7332 instead, and `dk_gdb.py` on the PC turns it into a normal GDB port, `127.0.0.1:3000`. You only need
it for debugging: nothing else in this repository uses it.

> **Security:** port 7332 has no password. Use it only on a network you trust, and remove `5dktcp.ipx`
> from the SD card when you don't need it.

| File | What it is |
|---|---|
| `5dktcp.ipx` | The build to put on the SD card. SHA-256 `ca5f9aaed759bdd50d9086fcf29fcd91a53eee1ac482350b2e586ccb6902855d` |
| `dk_gdb.py` | The PC side (Python 3, no extra packages) |
| `source/` | The plugin's source |
| `Makefile`, `build.sh` | The build. The rules, linker script and `crt0.S` come from stroopwafel (see Building). |
| `LICENSE-iosuhax.md` | iosuhax's license (see Credits) |

## What you need

- The same boot setup and IOSU version as [`5pcfstcp.ipx`](../pcfs-tcp/README.md#what-you-need-on-the-console).
  The plugin checks the instructions it patches first, and leaves alone what doesn't match. It patches
  RAM only and writes nothing to the console's storage.
- The stub's waiting state has to be set up by the title. [SparkCube](../../titles/spark-cube/README.md)
  does it when started with `dbgtry`; use its `source/dbg_try.c` in your own title. It is tied to
  coreinit of OS `000500101000400A`.
- A debugger: devkitPPC's `powerpc-eabi-gdb`, or Green Hills MULTI with its Cafe debug server.

Install: copy `5dktcp.ipx` to `wiiu/ios_plugins/` on the SD card and restart. The serial log then shows lines starting with `DKTCP:` and `DKSMD:`.

## Use it with GDB

Start the bridge and leave it running:

```powershell
py -3 .\plugins\dk-tcp\dk_gdb.py 192.0.2.50
```

Start the title from the CAT Recovery Menu or with no title running:

```powershell
.\catr\catr.cmd launch 0005000010C0BE00 dbgtry
```

It stops at `WAITING FOR DEBUGGER`. Then:

```powershell
powerpc-eabi-gdb spark_cube.elf
```

```text
(gdb) target remote 127.0.0.1:3000
(gdb) set $pc = $pc + 4
(gdb) continue
```

The first stop is the trap in `OSDebugStr`; the second line steps over it. You can also attach to a title
that is already running.

| Works | Doesn't |
|---|---|
| Attaching, `stepi`, `continue`, `^C`, `detach` | More than one breakpoint at a time: a second one is refused |
| One breakpoint: `hbreak`, or plain `break` (dk_gdb turns it into the one hardware breakpoint) | Real software breakpoints: they hang the stub |
| Threads, registers, reading memory | Writing memory is not tried |

**Always `detach` or `continue` before `catr kill`.** Ending a title that is stopped in the debugger can
hang the console. catr refuses (`--debugger-halted-ok` overrides it).

## Use it with MULTI

With `dk_gdb.py` running and the title started with `dbgtry`, open MULTI already connected:

```powershell
multi.exe spark_cube.elf -cmd "connect cafeserv -iphost 127.0.0.1 -ipport 3000 -no_ip_console -no_caferun -no_cafecontrol -no_cafex -no_ip_manage -no_kill_fsemul -attach_first"
```

- Press **Go** once before the first **Halt**. Halting first fails with "target is running".
- Breakpoints work one at a time, for example `b draw_cube` and then **Go**.
- **Don't press Halt while a breakpoint may be about to hit.** That hangs the stub, and only a power cycle
  recovers the console.

## Options of `dk_gdb.py`

| Option | What it does |
|---|---|
| `--gdb-port N` | Local port for the debugger (3000) |
| `--halted` | The title is already stopped in the debugger (after restarting the bridge) |
| `--log-client` | Prints what the debugger sends |
| `--no-sw-to-hw` | Passes software breakpoints on unchanged (they hang the stub) |

## Known problems

- Restarting the console can take longer with this plugin, and has occasionally got stuck. Power the
  console off and on if it does.
- SPARK and other HIO programs on the PC are not carried: HIO stays off, as on any CAT-R. For SPARK, use
  `catr hio`.

## Building

As for [`5pcfstcp.ipx`](../pcfs-tcp/README.md#building): devkitARM and a stroopwafel checkout.

```bash
export STROOPWAFEL_ROOT=/path/to/stroopwafel
./build.sh
```

Built with devkitARM GCC 16.1.0 against stroopwafel commit `f25549d`, this gives exactly the `5dktcp.ipx`
above.

## Credits

- [stroopwafel](https://github.com/StroopwafelCFW/stroopwafel) by Jan Hofmeier, shinyquagsire23 and
  contributors: the plugin loader, the plugin API and the build template.
- **smealum** ([iosuhax](https://github.com/smealum/iosuhax)) and stroopwafel's authors: `source/dk_sock.c`
  follows wupserver's socket code. iosuhax's license asks for credit and forbids commercial use; the full
  text is in [`LICENSE-iosuhax.md`](LICENSE-iosuhax.md).
- The GDB stub itself is Nintendo's, already in the console's OS.
