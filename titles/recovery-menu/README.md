# CAT Recovery Menu

> **This is not a real recovery menu.** It can't repair or recover a console that doesn't boot: it is an
> ordinary title, and it needs a working IOSU, OS and MLC to start at all.
>
> "Recovery" here means a **safe zone**: a place the CAT-R boots into and can always return to, where the
> tools on the PC keep working without System Config Tool or the Wii U Menu getting in the way.

It also has nothing to do with the console's own recovery mode (holding POWER and EJECT at power-on).
That one is Nintendo's, runs without this title, and **an update started there erases everything on the
console**. This menu only borrows its look.

## Why it exists

A CAT-R has to boot into some title, and the usual ones get in the way:

- **System Config Tool** suspends the network. wupserver, catr and PCFS stop answering while it runs.
- **The Wii U Menu** is made for playing games, not for starting and stopping builds from a PC.

The CAT Recovery Menu does very little: it boots, keeps the network and PCFS up, and waits. From there you
can start, install and debug titles from the PC with catr or CatR-Tools.exe, and come back to it
afterwards (`catr stop`). When you do need System Config Tool, press **+**.

| It can | It can't |
|---|---|
| Be the title the console boots into, with the network and PCFS left on | Start if IOSU, the OS or the MLC is broken |
| Show the console's IP address, PCFS state and installed titles | Restore a backup or fix a damaged system title |
| Install titles from the SD card and uninstall games | Replace Nintendo's recovery mode, or a NAND backup |
| Start games and System Config Tool | Help if it is the thing that's broken: keep another way in (below) |

## Using it

D-Pad up/down picks a tab, **A** (or right) opens it, **B** goes back to the tabs. **+** starts System
Config Tool from any tab, except while something is being installed or uninstalled.

| Tab | What it does |
|---|---|
| **Status** | SDK, OS and title IDs, console type, network and IP address, PCFS, title counts, and whether the console is ready for catr. Refreshes every 5 seconds; A or X refreshes now. |
| **Install** | A lists the packages in `sd:/install/<folder>`. **-** switches to installed titles, to uninstall games, updates and DLC. Every install and uninstall needs **L + R held while pressing A**. |
| **Launch** | Games, including builds on the PC over PCFS (marked `PC`). A starts the game. |
| **Logs** | The menu's own log. Up/down scroll, left/right page, X jumps to the newest line. Every line also goes to the USB serial log as `CAT Recovery Menu: ...` (`catr log`). |
| **Config Tool** | A starts System Config Tool. catr and PCFS stop answering until you come back here. |

A game started from the Launch tab becomes the console's main title through a fast restart. That is not
the same as `catr launch`, which leaves the menu as the main title.

## Identity

| | |
|---|---|
| Title ID | `000500101F700700` |
| Version | 1.1 (title version 11) |
| App type | `90000001` (Cafe Menu) |
| OS | `000500101000400A` |

The app type and permissions are those of the SDK's System Config Tool.

## Build

You need the **Cafe SDK 2.12.13**, **GHS v5.3.27** and **devkitPro's MSYS2** (for `make`), in folders
without spaces in their paths, and Windows PowerShell 5.1. None of these are in this repository.

```powershell
.\scripts\Build-Install.ps1 -CafeRoot C:\path\to\Cafe_SDK-2.12.13 -GhsRoot C:\path\to\ghs
```

This writes `CAT-Recovery-Menu-v11.wumad` and the install image in `Install\`. `-CafeRoot` and `-GhsRoot`
default to the `CAFE_ROOT` and `GHS_ROOT` variables; `-DevkitPro` defaults to `C:\devkitPro`.

- `.\scripts\Build.ps1` only compiles. With `-PcfsRoot <folder>` it also stages the menu into a PCFS
  folder. Don't make a PCFS copy the default title: it would be needed before PCFS connects.
- The DEMO font, boot images, icon and `meta.xml` template are copied from your SDK while building.
- For a new version, change `<title_version>` in `metadata\code\app.xml` and `MENU_VERSION` in
  `source\main.c`.
- make prints a harmless "pattern recipe did not update peer target" warning. Don't redirect the script's
  error output in PowerShell 5.1, which turns that warning into an error.

Neither script touches the console.

## Install

Keep a way back before changing what the console boots into: a NAND backup, and System Config Tool still
installed.

**The first time:** install the image from the SD card with System Config Tool, start the menu and check
that it works, then make it the default title (this restarts the console):

```powershell
.\catr\catr.cmd maintenance end
```

catr refuses if the menu isn't installed.

**Updating it:** start another title first, then:

```powershell
.\catr\catr.cmd repair recovery-menu C:\path\to\CAT-Recovery-Menu-v11_p01
```

`repair` checks the package, asks for a typed confirmation, backs up the installed menu, installs, and
waits for the new menu to start. `install` refuses to touch the menu.

**If the menu misbehaves:** wupserver runs in IOSU, not in the menu, so catr usually still answers.

- `catr maintenance sct` boots the console into System Config Tool instead. `catr maintenance end` goes
  back to the menu.
- `catr restore <backup> --recovery-menu` puts back the backup that `repair` made (with another title in
  front).
- If the font or its file access isn't available, the menu starts System Config Tool by itself.

## For developers: keep these

- The serial line `CAT Recovery Menu v<x> starting`. `catr repair recovery-menu` waits for it.
- The ProcUI loop keeps running after `OSLaunchTitlel`, so `catr launch` works from the menu without a
  restart and PCFS stays up.
- Background daemons stay on, which keeps the network up.
- The menu's own file access is asynchronous with a 3 second limit, so a dropped PCFS connection can't
  freeze it.
- Listing packages uses the normal SD mount. The MCP-side mount (`FSAMount` on `sdcard01`) happens only
  once an install is confirmed: mounting it twice can hang the console.

## Credits

- [wut](https://github.com/devkitPro/wut) by the devkitPro community: the MCP and FSA structure layouts in
  `source/titles.c` follow its coreinit headers.
- The look follows the console's own recovery mode, drawn from scratch. No Nintendo code or artwork is in
  this repository.
