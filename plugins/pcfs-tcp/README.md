# PCFS over TCP (`5pcfstcp.ipx`)

The console side of PCFS for a CAT-R without a MION. The CAT-R's IOSU already contains Nintendo's
PCFS-over-TCP code, but it is switched off. This [stroopwafel](https://github.com/StroopwafelCFW/stroopwafel)
plugin switches it on in RAM, so the console listens for a PCFS server on port 7500 early in each boot.
The PC side is [`pcfsserver`](../../pcfsserver/CHANGES.md), which catr and CatR-Tools.exe start for you.

| File | What it is |
|---|---|
| `5pcfstcp.ipx` | The build to put on the SD card (version V11). SHA-256 `1e1d04d0e983c4d016df195222ce982d53f7e8994e2999965f0fd8f9f8c21034` |
| `source/main.c` | Its source |
| `Makefile`, `build.sh` | The build. The rules, linker script and `crt0.S` come from stroopwafel's example plugin (see Building). |

## What you need on the console

- A CAT-R that boots with [minute](https://github.com/StroopwafelCFW/minute_minute) and stroopwafel, using
  one of minute's **Patch (sd)** options. They differ only in where IOSU is loaded from:
  - **Patch (sd) and boot IOSU (slc)**: the IOSU installed on the console. No extra file is needed.
  - **Patch (sd) and boot sdmc:/ios_orig.img**: a decrypted copy of it on the SD card. This file is
    **optional**. It is Nintendo's, so it is not in this repository: make it from your own console. The
    right one has SHA-256 `BB4A157D7FCDAB0AECDB1BEB50FBA32A473C75C5A13CBF4C2EF0A7B33160C5D0`.
- **IOSU OSv10, title version 15848**, either way. The plugin checks the instructions it patches before
  changing anything. On any other version it logs `signature mismatch; no patch applied` and does nothing.
- These other plugins on the SD card:

| SD card `wiiu/ios_plugins/` | From | Needed for |
|---|---|---|
| `00core.ipx` | stroopwafel `wafel_core` | Every plugin, this one included |
| `5isfshax.ipx`, `5payldr.ipx` | [wafel_isfshax_patch](https://github.com/jan-hofmeier/wafel_isfshax_patch) and [wafel_payloader](https://github.com/StroopwafelCFW/wafel_payloader), not included here | The usual ISFShax and Tiramisu boot; PCFS itself doesn't use them |
| `5dktcp.ipx` | [`../dk-tcp`](../dk-tcp/README.md) | Debugging with GDB or MULTI; PCFS doesn't use it |
| `5pcfstcp.ipx` | **this folder** | PCFS |
| `6wupserver.ipx` | [`../wupserver`](../wupserver/README.md), a build of stroopwafel's `wafel_plugin_wupserver` | wupserver on port 1337, which every catr command uses |

- The **CAT Recovery Menu** as the console's default title (see Known problems).

## Install

1. Copy `5pcfstcp.ipx` to `wiiu/ios_plugins/` on the SD card.
2. Boot with one of minute's **Patch (sd)** options.
3. On the PC: `catr pcfs up` (console on) or `catr pcfs keep --background`, then power on (console
   off). Or use **PCFS** in CatR-Tools.exe.

The USB serial log shows it working:

```text
PCFSTCP[V11]: config enable overridden to 1 in RAM
PCFSTCP[V11]: selected native TCP transport=3 port=7500
PCFSTCP[V11]: TCP_SM_Init result=0 attempts=1 retries=0 ncl_fd=...
PCFS: Waiting for PC connection on port 7500...
PCFS: Ping complete. PCFS file system is now available.
FSA: Attached volume to hfio01 (pcfs)
```

**Removing it:** delete `5pcfstcp.ipx` from the SD card, or boot with another card. Nothing on the
console is changed for good.

## What it does

All three changes are made in RAM, in IOSU's FS module:

1. **Config gate.** The FS module reads "PCFS enabled" from its config and finds 0. The plugin sets it
   to 1.
2. **Transport.** The transport selector returns 3 (TCP) on port 7500, and fills the same state as
   Nintendo's own TCP path.
3. **Start-up retry (V11).** Early in boot, the network driver isn't always registered yet when PCFS
   starts its TCP transport, and the start fails within milliseconds. The plugin retries the start
   every 250 ms, up to 25 times, but only for that failure. Other failures pass through unchanged.

Its log lines go through the FS module's own logger, so they appear in the CAT-R's **USB serial log**.

It writes nothing to SLC, MLC, OTP or SEEPROM, and changes no config file.

## Known problems

- **The console accepts PCFS for about 10 seconds per boot.** The PC has to connect in that window,
  and roughly every second boot the network isn't ready in time. catr restarts the console and tries
  again.
- **Apps that suspend the network cut PCFS.** System Config Tool does this. PCFS stays down until the
  next boot.
- **A dropped connection can hang the console.** A file access that is still waiting never returns. If
  it blocks the running title, the console needs a full power cycle.
- **Don't make a title that is only on PCFS the default title.** The default title starts long before
  PCFS connects. Keep the CAT Recovery Menu (installed on the console) as the default, and start PCFS
  titles from it (`catr run`, `catr launch`).

## Building

You need [devkitARM](https://devkitpro.org/) and a checkout of
[stroopwafel](https://github.com/StroopwafelCFW/stroopwafel), for its plugin headers (`wafel_core/include`)
and its example plugin (`wafel_plugin_example`: `elf_rules`, `ccd00.specs`, `ccd00.ld`, `source/crt0.S`).

```bash
export STROOPWAFEL_ROOT=/path/to/stroopwafel
./build.sh
```

Built with devkitARM GCC 16.1.0 against stroopwafel commit `f25549d`, this gives exactly the
`5pcfstcp.ipx` above.

## Credits

- [stroopwafel](https://github.com/StroopwafelCFW/stroopwafel) by Jan Hofmeier, shinyquagsire23 and
  contributors: the plugin loader, the plugin API this plugin is written against, the build
  template, `00core.ipx` and `6wupserver.ipx`.
- [minute](https://github.com/StroopwafelCFW/minute_minute) by shinyquagsire23 and contributors: the
  boot loader that loads IOSU and stroopwafel from the SD card.
- The PCFS-over-TCP code itself is Nintendo's, already in the console's IOSU. This plugin only switches
  it on.
