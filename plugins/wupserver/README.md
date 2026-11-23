# wupserver (`6wupserver.ipx`)

The server on the console that catr talks to: it runs inside IOSU's MCP process and listens on TCP port
**1337**. Every catr command that uses the network needs it.

**This is not our code.** It is a copy of `wafel_plugin_wupserver` from
[stroopwafel](https://github.com/StroopwafelCFW/stroopwafel), which in turn carries on **wupserver** from
[iosuhax](https://github.com/smealum/iosuhax) by **smealum**. It is kept here so the exact source of the
wupserver that catr is used with is in one place. See Credits.

> **Security:** wupserver has no password. Anyone on the same network can read and write IOSU's memory and
> run its system calls. Use it only on a network you trust, never open port 1337 to the internet, and
> remove `6wupserver.ipx` from the SD card when you don't need it.

| File | What it is |
|---|---|
| `6wupserver.ipx` | The build to put on the SD card. SHA-256 `c6e0134425c8c27b23b41a72ba1c5bf451e4394f89abe1c057b43b0f737e965c` |
| `source/wupserver.c`, `.h` | The server and its command handler |
| `source/text.c`, `.h` | Logging and on-screen text |
| `source/main.c` | The stroopwafel plugin entry points that start it |
| `Makefile`, `build.sh` | The build. The rules, linker script and `crt0.S` come from a stroopwafel checkout (see Building). |
| `LICENSE-iosuhax.md` | iosuhax's license |

## Where it comes from

- **Source:** stroopwafel commit `f2ebe52`, folder `wafel_plugin_wupserver`.
- **iosuhax:** much of `wupserver.c` and most of `text.c` is still iosuhax's original code.
- **Changes made here,** so that it builds and runs with stroopwafel's current headers:
  - `wupserver.c`: in `fsa_copydir`, `directoryEntry_s` is now `FSDirectoryEntry`, and `dirStat` is now
    `stat`.
  - `text.c`: `drawString` does nothing when the on-screen font isn't available (`wafel_core` doesn't
    export `font_bin`).
- `Makefile` and `build.sh` are the ones from [`../pcfs-tcp`](../pcfs-tcp/README.md), which are based on
  stroopwafel's example plugin. They also add the `wafel_core/source` headers.

## Building

You need [devkitARM](https://devkitpro.org/) and a checkout of
[stroopwafel](https://github.com/StroopwafelCFW/stroopwafel):

```bash
export STROOPWAFEL_ROOT=/path/to/stroopwafel
./build.sh
```

With devkitARM GCC 16.1.0 and stroopwafel `f2ebe52` or `f25549d`, this gives exactly the `6wupserver.ipx`
above.

The code uses fixed addresses in IOSU's MCP (`MCP_SVC_BASE` in `wupserver.c`, the logger in `text.c`), so
it only works on an IOSU version where those match. The CAT-R's IOSU here is OSv10, title version 15848.

## Install

Copy `6wupserver.ipx` to `wiiu/ios_plugins/` on the SD card, next to stroopwafel's `00core.ipx`, and boot
with minute (**Patch (sd)**).

## Credits and license

- **smealum**: [iosuhax](https://github.com/smealum/iosuhax), where wupserver comes from. Its license asks
  for credit and forbids commercial use; the full text is in [`LICENSE-iosuhax.md`](LICENSE-iosuhax.md).
- **shinyquagsire23**: wupserver in stroopwafel (2023), including the emergency install and the first
  stroopwafel version.
- **Jan Hofmeier**: turned it into a stroopwafel plugin (2023) and fixed its BSS setup (2025).
- **Cynthia**: fixes and removal of the syscall table (2025).
- The names are as they appear in stroopwafel's git history for these files.

stroopwafel does not state a license. smealum's part is covered by the iosuhax license; the stroopwafel
authors' additions remain theirs, and are included here with credit. If you are one of them and want this
changed or removed, please open an issue.
