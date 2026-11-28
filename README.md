# Wii-U-CAT-R-Tools

Tools for working with a Wii U **CAT-R** from a Windows PC: see and install titles, start and stop them, run
your own builds straight from the PC, read the console's logs, and back up its settings.

**Contents:** [How it works](#how-it-works) · [What is in here](#what-is-in-here) ·
[What you need](#what-you-need) · [Setup](#setup) · [Using it](#using-it) · [Settings](#settings) ·
[Building the parts](#building-the-parts) · [Troubleshooting](#troubleshooting) · [Safety](#safety) ·
[Not included](#not-included) · [Credits](#credits) · [AI](#ai)

## How it works

```text
            PC                                                   CAT-R
 +---------------------+      network, port 1337      +--------------------------+
 |  CatR-Tools.exe     | ---------------------------> |  6wupserver.ipx          |  files, titles,
 |  (the window)       |                              |                          |  installs, settings
 |        or           |      USB serial cable        |                          |
 |  catr               | ---------------------------> |  COS shell, console log  |  start and stop titles,
 |  (command line)     |                              |                          |  logs
 |        |            |      network, port 7500      |                          |
 |  pcfsserver.exe     | ---------------------------> |  5pcfstcp.ipx            |  PCFS: titles that run
 +---------------------+                              +--------------------------+  from a folder on the PC
```

- **On the PC** you use either **CatR-Tools.exe** (a window) or **catr** (the command line). They do the same
  things: CatR-Tools.exe has catr built in.
- **On the console**, two small plugins are loaded from the SD card at boot. They run below the title, so they
  keep working whichever title is in front.
- The PC always connects **to** the console, so Windows Firewall needs no new rule.

## What is in here

| Folder | What it is | Do you need it? |
|---|---|---|
| [`app/`](app/README.md) | **CatR-Tools.exe**, the Windows program. Start here. | Yes, or catr |
| [`catr/`](catr/README.md) | **catr**, the command-line toolkit that does the work. | Yes, or CatR-Tools.exe |
| [`plugins/wupserver/`](plugins/wupserver/README.md) | **6wupserver.ipx** and its source: the server on the console that catr talks to. By smealum (iosuhax) and stroopwafel's authors. | Yes, on the SD card |
| [`titles/recovery-menu/`](titles/recovery-menu/README.md) | **CAT Recovery Menu**, the title the console boots into. Not a real recovery menu: a safe place to return to, where the tools keep working. | For PCFS, `stop` and `maintenance` |
| [`plugins/pcfs-tcp/`](plugins/pcfs-tcp/README.md) | **5pcfstcp.ipx**, the console side of PCFS. | For PCFS |
| [`pcfsserver/`](pcfsserver/CHANGES.md) | **pcfsserver.exe**, the PC side of PCFS: sprig's server with fixes for the CAT-R. | For PCFS; catr starts it |
| [`titles/spark-cube/`](titles/spark-cube/README.md) | **SparkCube**, a demo title that shows SPARK captures. | Optional |
| [`tools/gx2-patch/`](tools/gx2-patch/README.md) | A script that patches your console's own `gx2.rpl`, so SPARK can capture. | Optional, for SPARK |
| [`plugins/dk-tcp/`](plugins/dk-tcp/README.md) | **5dktcp.ipx** and `dk_gdb.py`: debug a title with GDB or MULTI over the network. | Optional |

## What you need

**The console**

- A **CAT-R** on the same network as the PC, on IOSU **OSv10, title version 15848**. The plugins are made for that
  version; `5pcfstcp.ipx` checks, and does nothing on any other.
- It boots with [minute](https://github.com/StroopwafelCFW/minute_minute) and
  [stroopwafel](https://github.com/StroopwafelCFW/stroopwafel) from the SD card, using one of minute's
  **Patch (sd)** options.
- Its **USB serial cable**, connected to the PC. Without it the network commands still work (titles, installs,
  files, backups), but not the console log, starting titles, restarting
  the console, or PCFS.
- **A NAND backup.** Make one before you change what the console boots into.

**The PC** runs Windows 10 or 11 (64-bit). What else depends on what you want to do:

| To... | Install |
|---|---|
| Run CatR-Tools.exe | [.NET 10 Desktop Runtime](https://dotnet.microsoft.com/download/dotnet/10.0) |
| Build CatR-Tools.exe | [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0) (it includes the runtime) and [Python](https://www.python.org/downloads/windows/) 3.12 or newer, with its `py` launcher |
| Use catr without CatR-Tools.exe | Python 3.12 or newer, with its `py` launcher. No packages. |
| Run builds over PCFS | The **Cafe SDK** 2.12.13 (its system files go into the PCFS folder) |
| Install `.wumad` files, or use SPARK | The **Cafe SDK** 2.12.13 |
| Build the plugins | [devkitPro](https://devkitpro.org/wiki/Getting_Started) with devkitARM, and a stroopwafel checkout |
| Build the titles | The **Cafe SDK** 2.12.13, **GHS** v5.3.27, and devkitPro (SparkCube also needs devkitPPC) |

## Setup

Run the commands in **PowerShell, in this repository's folder**. The scripts are started through
`powershell -ExecutionPolicy Bypass -File`, because Windows does not run downloaded scripts by default.
`192.0.2.50` and `COM3` are examples: use your console's.

### 1. Get the files

Download the repository as a ZIP (**Code > Download ZIP**) and unpack it, or clone it:

```powershell
git clone https://github.com/pumpk-bit/Wii-U-CAT-R-Tools.git
```

```powershell
cd Wii-U-CAT-R-Tools
```

### 2. Put the plugins on the console's SD card

Copy these to the folder `wiiu/ios_plugins/` on the SD card:

| File | Where it comes from | Needed for |
|---|---|---|
| `00core.ipx` | [stroopwafel](https://github.com/StroopwafelCFW/stroopwafel) | All the plugins |
| `6wupserver.ipx` | [`plugins/wupserver/6wupserver.ipx`](plugins/wupserver/6wupserver.ipx) in this repository (a build of stroopwafel's `wafel_plugin_wupserver`) | Everything here |
| `5pcfstcp.ipx` | [`plugins/pcfs-tcp/5pcfstcp.ipx`](plugins/pcfs-tcp/5pcfstcp.ipx) in this repository | PCFS |

Put the card back, start the console, and boot with one of minute's **Patch (sd)** options.
[More about the plugins](plugins/pcfs-tcp/README.md#what-you-need-on-the-console).

> **wupserver has no password.** Anyone on the same network can control the console through it. Use it only on a
> network you trust, and never open its port (1337) to the internet.

### 3. Connect the cable

Connect the CAT-R's USB serial cable to the PC. Windows lists it in **Device Manager > Ports (COM & LPT)**; note its
number, for example `COM3`. If it isn't listed, its USB driver is missing.

### 4. Build CatR-Tools.exe

If you have a built CatR-Tools.exe already, or only want the command line, skip this step.

```powershell
powershell -ExecutionPolicy Bypass -File .\app\build.ps1
```

This takes a minute, and needs the internet the first time. The result is `ReleaseBuilds\CatR-Tools.exe`. Move it to a
folder of its own (with the README and `licenses` next to it): it keeps its settings, backups and PCFS folder next to
itself.

### 5. Connect to the console

**In CatR-Tools.exe:** open **Settings**, type the console's IP address and the COM port, and press **Find and save**.
**Overview** then shows the console as reachable.

**With catr:**

```powershell
.\catr\catr.cmd find --ip 192.0.2.50 --port COM3
```

```powershell
.\catr\catr.cmd info
```

Don't know the IP address? Leave it out and the PC's own network is searched. Your router lists it too.

At this point titles, installs, files, backups and the console log work.

### 6. Install the CAT Recovery Menu

The CAT-R needs a title to boot into that leaves the network on. System Config Tool switches it off, and the Wii U
Menu gets in the way. The Recovery Menu does neither, and the tools return to it after each run.

1. Build it. The result is the folder `titles\recovery-menu\Install\CAT-Recovery-Menu-v11_p01`:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\titles\recovery-menu\scripts\Build-Install.ps1 -CafeRoot C:\path\to\Cafe_SDK-2.12.13 -GhsRoot C:\path\to\ghs
   ```

2. Copy that folder to the SD card and install it with **System Config Tool**. Keep System Config Tool installed: it
   is your way back.
3. Start the menu and check that it works. Then make it the title the console boots into (this restarts the console):

   ```powershell
   .\catr\catr.cmd maintenance end
   ```

[More about the Recovery Menu](titles/recovery-menu/README.md), including how to update it and what to do if it
misbehaves.

### 7. Set up PCFS

PCFS lets the console run a build straight from a folder on the PC, without installing it. It needs steps 2, 3 and 6.

1. Tell the tools where the Cafe SDK is. If the SDK set up `CAFE_ROOT` for you, skip this:

   ```powershell
   setx CAFE_ROOT "C:\path\to\Cafe_SDK-2.12.13"
   ```

   Close and reopen PowerShell and CatR-Tools afterwards.
2. Make the PCFS folder:
   - **CatR-Tools.exe** makes it for you: a `PCFS` folder next to itself, filled from the SDK the first time it starts
     (about 1 GB).
   - **With catr only**, copy `disc`, `mlc` and `slc` from the SDK's `data` folder into a `data` folder of your own (for
     example `D:\pcfs\data`), then choose the folder above it:

     ```powershell
     .\catr\catr.cmd pcfs root D:\pcfs
     ```

3. Connect. This restarts the console, and about every second try works, so it tries up to 3 times:

   ```powershell
   .\catr\catr.cmd pcfs up
   ```

### 8. Optional extras

- **SPARK captures:** [patch `gx2.rpl`](tools/gx2-patch/README.md), then [build and run SparkCube](titles/spark-cube/README.md).
- **Debugging with GDB or MULTI:** [the debugger plugin](plugins/dk-tcp/README.md).

## Using it

In CatR-Tools.exe, **F1** opens the full guide ([app/README.md](app/README.md) and all catr commands). The catr
commands start with `.\catr\catr.cmd`, or `CatR-Tools` if you have the exe; `--help` after any of them lists its options.

| I want to... | In CatR-Tools.exe | With catr |
|---|---|---|
| See whether the console answers | **Overview** | `info` |
| See what is installed | **Titles** | `titles` |
| Install a title | **Install** | `install FOLDER` or `install FILE.wumad` |
| Uninstall a game | **Titles > Uninstall** | `uninstall TITLE-ID` |
| Start a title | **Titles > Launch** | `launch TITLE-ID` |
| Run my own build from the PC | **PCFS > Stage and launch** | `run FOLDER` |
| Go back to the Recovery Menu | **PCFS > Back to Recovery Menu** | `stop` |
| Keep PCFS connected at every boot | **PCFS > Start keeper** | `pcfs keep --background` |
| Read the console's log | **USB Log** | `log --follow` |
| Copy files from the console | **Commands** | `get /vol/... FOLDER` |
| Back up the settings | **Commands** | `backup config` |
| Copy the crash logs | **Diagnostics** | `crashlogs` |
| Boot into System Config Tool for a while | **Commands** | `maintenance sct`, then `maintenance end` |

A build folder for `run` is an unpacked title: a folder with `code`, `content` and `meta` in it. Every command is
described in [catr/README.md](catr/README.md).

## Settings

CatR-Tools.exe keeps its settings in `config.json` next to itself; catr alone uses `%APPDATA%\catr\config.json`. When
you have both, catr follows the CatR-Tools.exe that was started last, so they share one set of settings. `find`, **Settings**
and `pcfs root` write these for you:

| In config.json | What it is | Without it |
|---|---|---|
| `"ip"` | The console's IP address | Run `find` |
| `"serial_port"` | The cable's COM port | The only USB serial port on the PC |
| `"pcfs_root"` | The PCFS folder | `PCFS` next to the exe; `workspace\PCFS` for catr |
| `"workspace"` | Where backups, captures and logs are saved | `Workspace` next to the exe; `workspace` in this folder for catr (git ignores it) |
| `"cafe_root"` | The Cafe SDK | The `CAFE_ROOT` variable |

To set one by hand, edit the file. Write backslashes twice:

```json
{ "workspace": "D:\\catr-workspace", "cafe_root": "C:\\path\\to\\Cafe_SDK-2.12.13" }
```

The environment variables that override these are in [catr/README.md](catr/README.md#development).

## Building the parts

| Part | Command | Result |
|---|---|---|
| CatR-Tools.exe | `powershell -ExecutionPolicy Bypass -File .\app\build.ps1` | `ReleaseBuilds\CatR-Tools.exe` |
| catr | Nothing to build | |
| A plugin | In devkitPro's MSYS2 shell: `STROOPWAFEL_ROOT=/c/path/to/stroopwafel plugins/pcfs-tcp/build.sh` (or `wupserver`, `dk-tcp`) | `5pcfstcp.ipx` (or the other plugin) in its folder |
| CAT Recovery Menu | `powershell -ExecutionPolicy Bypass -File .\titles\recovery-menu\scripts\Build-Install.ps1` | A `.wumad` and the `Install` folder |
| SparkCube | `powershell -ExecutionPolicy Bypass -File .\titles\spark-cube\scripts\Build.ps1` | A `.wumad` and the `Install` folder |
| pcfsserver.exe | With Rust, from sprig's source: see [CHANGES.md](pcfsserver/CHANGES.md#building-it-yourself) | `target\release\pcfsserver.exe` |

The title scripts take `-CafeRoot` and `-GhsRoot`, or use the `CAFE_ROOT` and `GHS_ROOT` variables, and expect devkitPro
in `C:\devkitPro` (`-DevkitPro` changes it). The SDK can't build in folders with a space in their path. With the compiler
and stroopwafel versions named in their READMEs, the plugins come out byte for byte the same as the ones the project uses.

catr's tests need no console:

```powershell
py -3 -m unittest discover -s .\catr\tests
```

## Troubleshooting

| What you see | What to do |
|---|---|
| `find` gets no answer | Is the console on the same network, booted with **Patch (sd)**, with `00core.ipx` and `6wupserver.ipx` on the SD card? System Config Tool and a title that crashed can switch the network off: restart the console. |
| `Python launcher (py) not found` | Install Python from python.org with the `py` launcher, or set `CATR_PYTHON` to a python.exe. |
| `No serial port saved` | Connect the cable, find its number in Device Manager > Ports, and run `find --ip ... --port COMx`. |
| `Serial service exited (is COMx in use?)` | Close other serial programs (PuTTY, Tera Term). `serial --stop` frees the port again. |
| PCFS doesn't connect | Try again: about every second boot the console's network is not ready in time. No `PCFSTCP` lines in the log means the plugin isn't loaded; `signature mismatch` means another IOSU version. |
| A staged build doesn't start | The console reads PCFS titles only when PCFS connects. `run` notices and reconnects. |
| `... is needed from the Cafe SDK` | Set `CAFE_ROOT`, or `"cafe_root"` in config.json. |
| `The .NET 10 SDK is needed` | Building needs the SDK, not only the runtime. |
| NuGet errors while building (`NU1100`, `NU1301`) | The first build downloads the .NET SDK's packs from nuget.org: check the connection. |
| `running scripts is disabled on this system` | Start the script with `powershell -ExecutionPolicy Bypass -File`, as above. |
| The console is stuck | Power it off and on. catr says so when nothing else helps. |

## Safety

- catr asks before it changes anything on the console, and refuses what could stop it from booting
  ([the rules](catr/README.md#safety-rules)).
- The plugins change IOSU in RAM only. Remove them from the SD card and they are gone.
- **wupserver** (port 1337), **SparkCube** (6100) and the **debugger plugin** (7332) have no password: use them only on a
  network you trust.
- **gx2-patch** changes a system file on the SLC. Read [its README](tools/gx2-patch/README.md) first.
- The console's own recovery mode (POWER + EJECT at power-on) has nothing to do with the CAT Recovery Menu. **An update
  started there erases everything on the console.**

## Not included

- [stroopwafel](https://github.com/StroopwafelCFW/stroopwafel) itself, including `00core.ipx`.
- `5isfshax.ipx` and `5payldr.ipx`, from
  [wafel_isfshax_patch](https://github.com/jan-hofmeier/wafel_isfshax_patch) and
  [wafel_payloader](https://github.com/StroopwafelCFW/wafel_payloader). They boot
  [ISFShax](https://github.com/isfshax/isfshax) and Tiramisu; the tools don't use them.
- The console's IOSU image (`ios_orig.img`, which is optional), `gx2.rpl` (patched by
  [`tools/gx2-patch/`](tools/gx2-patch/README.md) from your own console), and anything else from Nintendo,
  including the Cafe SDK.

## Credits

- **wupclient** (`catr/src/catr/vendor/wupclient.py`) is a modified copy of `wupserver/wupclient.py`
  from [iosuhax](https://github.com/smealum/iosuhax) by **smealum**. Its license allows use with credit
  to the original creator and **not for commercial purposes**, so this project is non-commercial too.
  The full license text is in
  [`LICENSE-iosuhax.md`](catr/src/catr/vendor/LICENSE-iosuhax.md); the local changes are listed in
  `PROVENANCE.json` next to it.
- **pcfsserver** (`pcfsserver/pcfsserver.exe`) is the `pcfsserver` program from
  [sprig](https://codeberg.org/rem-verse/sprig) by **Cynthia Coan** (rem-verse), MIT license
  ([`LICENSE-sprig`](pcfsserver/LICENSE-sprig)). The PCFS server itself is sprig's work; the changes made
  here for the CAT-R are in [`CHANGES.md`](pcfsserver/CHANGES.md) and
  [`sprig-pcfsserver.patch`](pcfsserver/sprig-pcfsserver.patch). The Rust libraries linked into it are
  listed in [`THIRD-PARTY-NOTICES.txt`](pcfsserver/THIRD-PARTY-NOTICES.txt).
- **wupserver** (`plugins/wupserver/`) comes from [iosuhax](https://github.com/smealum/iosuhax) by
  **smealum**, carried on in [stroopwafel](https://github.com/StroopwafelCFW/stroopwafel) by
  **shinyquagsire23**, **Jan Hofmeier** and **Cynthia**; see
  [its README](plugins/wupserver/README.md#credits-and-license). iosuhax's license also makes it
  non-commercial; stroopwafel states no license for its authors' additions.
- **5pcfstcp.ipx** is written against [stroopwafel](https://github.com/StroopwafelCFW/stroopwafel)'s
  plugin API and builds with stroopwafel's example plugin template; see
  [its README](plugins/pcfs-tcp/README.md#credits).
- **5dktcp.ipx** is written against stroopwafel's plugin API too, and its socket code follows wupserver's
  (smealum's iosuhax and stroopwafel's authors); see [its README](plugins/dk-tcp/README.md#credits).
- **CAT Recovery Menu** follows the MCP and FSA structure layouts in
  [wut](https://github.com/devkitPro/wut)'s headers.

## AI

AI tools helped make this project: they wrote and checked code and documentation, and helped with the
research behind it.

- **[Claude](https://github.com/claude)** (Claude Code) by [Anthropic](https://www.anthropic.com/)
- **[Codex](https://github.com/codex)** by [OpenAI](https://openai.com/)
- **[Cursor](https://github.com/cursoragent)** by [Anysphere](https://cursor.com/), with **Grok** by [xAI](https://x.ai/)

Everyone is listed in [CONTRIBUTORS.md](CONTRIBUTORS.md).
