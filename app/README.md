# CatR-Tools

CatR-Tools is a Windows program for working with a CAT-R: see what is installed, install and start
titles, run your own builds straight from this PC, read the console's logs, and back up its settings.

Everything is in one file, CatR-Tools.exe. It contains **catr** (the command-line toolkit that does the
actual work) and everything catr needs. The only thing to install is the .NET 10 Desktop Runtime.

## Getting started

1. Connect the CAT-R's USB serial cable to this PC, and put the console on the same network.
2. On the console, wupserver must be running (`6wupserver.ipx` on the SD card, next to stroopwafel's
   `00core.ipx`; it comes with CatR-Tools.exe), and the default title should be the **CAT Recovery Menu**
   (see **CAT Recovery Menu** below).
3. Open **Settings**, type the console's IP address and COM port (Device Manager > Ports shows it), and press
   **Find and save**. With the IP empty it searches this PC's network; with the COM port
   empty it uses the only USB serial port on the PC.
4. **Overview** should now show the console as connected, with its default title and free space.
5. **Titles** shows what is installed. **Install** installs a title from this PC.
6. To run your own builds from this PC without installing them, see **PCFS** below.

Press **F1** for this documentation at any time, and **Ctrl+F** to search it.

## Pages

### Overview

Whether the console answers, which title it boots into, whether PCFS and the serial cable are in use,
free space, and the last commands run from this window. **Refresh** only reads; it never changes anything
on the console.

### Titles

Everything installed on the console, with icons, in a list you can search. Icons load in the background
after a refresh.

- **Details** shows a title's icon and information.
- **Launch** starts the title. The Homebrew Launcher asks first, because it is unstable on the CAT-R.
- **Uninstall** asks first, and is only offered for games, updates and DLC.
- System titles are hidden unless you turn them on in Settings. Titles without a name can be hidden too.
- Builds in your PCFS folder are listed as **PCFS** titles. Launching one connects PCFS first if needed.
  **Remove from PCFS** deletes the build's folder on this PC (to the Recycle Bin, unless Settings says
  otherwise). Its save data is kept.

### Install

Pick an install image folder or a .wumad file and press Install. The progress is shown while the files
are copied. Every file is checked after copying. **Size only** skips that check: about twice as fast,
but a damaged file is not noticed. **Keep** leaves the copied files on the console after installing.

### Commands

Type any catr command (see **catr commands** below), or one command for the console's COS shell. ↑ and ↓
bring back earlier commands.

### PCFS

Connect PCFS (**pcfs up**, **Start keeper**), disconnect it (**pcfs down**), and **Stage and launch** a
build folder. See **PCFS** below for which to use when.

### Diagnostics

Console information, the system log, running threads, and crash logs (copying them takes about 7
minutes).

### Settings

- **Console**: the console's IP address and COM port. **Find and save** checks and saves them.
- **PCFS host folder**: the folder on this PC that the console sees over PCFS. **Default** chooses the
  PCFS folder next to CatR-Tools.exe.
- **Titles**: which titles the Titles page lists, and what Remove from PCFS does.
- **Window**: light or dark theme, and what to do with background services when you close the window.

### USB Log

**View > USB Log** shows the console's log from the serial cable as it arrives. You can filter it, pause
it, change the font size and save it.

### While commands run

- Anything that changes the console asks first.
- Only one console command runs at a time. The status bar shows it and has a **Stop** button. Stopping a
  file transfer halfway can leave the console needing a reboot.
- When you close the window while PCFS or the serial service is still running, you are asked whether to
  stop them or leave them running.

## CAT Recovery Menu

The title the console should boot into. It is **not a real recovery menu**: it can't repair a console that
doesn't boot, and it has nothing to do with the console's own recovery mode (POWER + EJECT, where an update
erases everything). It is a safe place to return to, where the network and PCFS stay on and CatR-Tools
keeps working, without System Config Tool or the Wii U Menu getting in the way.

Its source, and how to build and install it, are in the Wii-U-CAT-R-Tools repository, in
`titles/recovery-menu`.

## PCFS

PCFS lets the console load a title straight from a folder on this PC, so you don't have to install every
new build. Titles can also save files to this PC through it (SPARK captures, logs).

**How it connects.** The console only accepts a PCFS connection for about 10 seconds early in each boot,
and the PC has to connect in that window. Roughly every second boot, the console's network isn't ready
in time and that boot can't connect; another restart tries again. Once connected, PCFS stays connected
until the console restarts. Starting a title from the Recovery Menu doesn't restart the console, so PCFS
stays up.

**What it needs.** The PCFS-over-TCP plugin `5pcfstcp.ipx` on the console's SD card (in the
Wii-U-CAT-R-Tools repository, `plugins/pcfs-tcp`, with install steps), next to stroopwafel's plugins. The
PC side, `pcfsserver.exe`, is built in. Both ways below also need the USB serial cable: that is how catr
knows when the window opens.

### Console off: Start keeper, then power on

**Start keeper** waits in the background. Every time the console boots, including when you switch it on,
it connects PCFS at the right moment. So: start the keeper, then switch the console on. If that boot
doesn't connect, restart the console. **Stop keeper** ends it.

### Console on: pcfs up

**pcfs up** restarts the console into the Recovery Menu and connects during that boot. It tries up to 3
times. If PCFS is already connected, it does nothing.

**Stage and launch** does all of this for you: it copies your build into the PCFS folder, connects PCFS
if needed, starts the title and shows its log.

**pcfs down** disconnects PCFS. It asks first if a title is running that might be using it.

Tips:

- A build copied in while PCFS is connected can't start until PCFS reconnects. Stage and launch notices
  and reconnects for you.
- The `usr/tmp` folder over PCFS is emptied at every boot. Don't keep files there.
- Don't start PCFS titles from System Config Tool: it switches off the network, and PCFS with it. That is
  why the default title should be the Recovery Menu.

## Folders and app data

Next to CatR-Tools.exe (**File > Open CatR-Tools folder**):

```
CatR-Tools.exe
README.md      this documentation
config.json    the console's IP address, COM port and PCFS folder
PCFS\          the PCFS folder the console sees
Workspace\     backups, captures and crash logs
licenses\      licenses of the included programs
```

- **config.json** is created the first time CatR-Tools starts, from the settings catr already had.
  From then on catr's own `%APPDATA%\catr\config.json` points to it, so catr run from a terminal
  (`catr.cmd`) uses the same settings. If you have several copies of CatR-Tools.exe, the one started
  last is the one catr follows.
- **PCFS** is created the first time the window opens. If the Cafe SDK is installed (`CAFE_ROOT` is
  set, or `"cafe_root"` in config.json), its system files are copied in (about 1 GB, with a progress window). PCFS needs them to work.
  If you cancel, the copy carries on next time.
- If CatR-Tools.exe is in a folder it can't write to (such as Program Files), these are kept in app
  data instead.

App data (**File > Open app data**) holds caches and saved state: window settings, the title list,
icons, and the logs of catr and its PCFS and serial services. PCFS logs older than 30 days are removed
automatically. Backups, captures and crash logs go to the workspace folder shown in **Help > About**:
**Workspace** next to the exe, unless `"workspace"` in config.json names another folder.

## Command line

Every catr command can also be run from a terminal:

```
CatR-Tools titles
CatR-Tools install D:\images\MyGame --size-only
CatR-Tools pcfs root D:\pcfs
```

With no arguments, CatR-Tools opens the window. The terminal prompt can come back before the output has
finished, because CatR-Tools is a window program.

## Credits

catr talks to the console's wupserver with a modified copy of **wupclient** from **iosuhax** by
**smealum** (https://github.com/smealum/iosuhax). Its license allows anyone to use the code as long as
they credit the original creator and don't use it for commercial purposes; sharing improvements is
appreciated. So CatR-Tools is free and must not be used commercially. The full license text is in the
Wii-U-CAT-R-Tools repository, in `catr/src/catr/vendor/LICENSE-iosuhax.md`.

The built-in `pcfsserver.exe` is the `pcfsserver` program from **sprig** by **Cynthia Coan** (rem-verse,
https://codeberg.org/rem-verse/sprig), under the MIT license. The PCFS server is sprig's work; the
changes made for the CAT-R (connecting out over Wi-Fi, large writes, write results) are listed in the
repository's `pcfsserver/CHANGES.md`, next to sprig's license in `pcfsserver/LICENSE-sprig` and the
licenses of the Rust libraries in it (`pcfsserver/THIRD-PARTY-NOTICES.txt`).

**wupserver** (`6wupserver.ipx`), which runs on the console, is by **smealum** (iosuhax) and stroopwafel's authors. Its source
and full credits are in the repository, in `plugins/wupserver`.

## Build

You need **Python 3.12 or newer** from [python.org](https://www.python.org/downloads/) with its `py` launcher
(or set `CATR_PYTHON` to a python.exe), the
**[.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0)**, and the internet for the first build
(nuget.org, for the SDK's own packs). In the repository folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\app\build.ps1
```

This writes CatR-Tools.exe, its README, the licenses, the console's plugins (`.ipx`) and the debugger's
`dk_gdb.py` to the repository's `ReleaseBuilds` folder, which git ignores. Nothing from your PC is built in, so the exe can be shared.

| Option | What it does |
|---|---|
| `-Output FOLDER` | Writes to another folder |
| `-Workspace FOLDER` | Builds a workspace folder in, for an exe you only use on this PC |
| `-PcfsServer FILE` | Another pcfsserver.exe to build in |

`Bundle\make_bundle.py` packs the running Python's runtime and standard library (no tkinter, tests or
site-packages), this repository's `catr` and `pcfsserver.exe` into `obj\bundle\catr-bundle.zip`, which is
embedded in the exe. A plain `dotnet build` makes an exe without it, which uses the repository's `catr` folder
and the installed Python.

When it runs, the workspace is `CATR_WORKSPACE`, else `"workspace"` in config.json, else the one built in
if that folder exists, else `Workspace` next to the exe. The built-in catr is told that workspace
(`CATR_WORKSPACE`), its log folder in app data (`CATR_LOGS`) and the built-in server (`CATR_PCFS_SERVER`).

`Bundle\make_readme.py` writes `obj\bundle\help.md`: this README up to Build, then catr's command reference
with `.\catr\catr.cmd` written as `CatR-Tools`. It is built into the exe (Help > Documentation, F1) and
copied next to it as README.md. A plain `dotnet build` embeds this README instead.

`CATRTOOLS_SNAPSHOT=<folder>` renders every page to PNG files and exits (a layout check; with
`CATRTOOLS_THEME=Dark` for dark mode). The Overview page reads the default title on its first show;
icons are not fetched in this mode.
