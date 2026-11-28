# USB Ethernet for AX88772E adapters (`5usbe72e.ipx`)

A [stroopwafel](https://github.com/StroopwafelCFW/stroopwafel) plugin that lets a CAT-R use a USB Ethernet
adapter with an **ASIX AX88772E** chip (USB id `0b95:1790`). The console's own USB Ethernet driver only
knows the older AX88772. This plugin also includes the AX88772A/B/C support from
[wafel_ax88772b](https://github.com/StroopwafelCFW/wafel_ax88772b), so it covers those adapters too.

With a cable from the adapter straight to the PC, PCFS runs much faster and steadier than over Wi-Fi.

| File | What it is |
|---|---|
| `5usbe72e.ipx` | The build to put on the SD card. SHA-256 `3fbbd6625759c5687a4f653bd778734193b2d5adffdea09326da68abb2e01e03` |
| `source/main.c` | Its source |
| `source/uhs.h` | USB host structures, from wafel_ax88772b |
| `Makefile`, `build.sh`, `elf_rules`, `ccd00.specs`, `ccd00.ld`, `source/crt0.S` | The build. The rules, linker script and start-up code are an older copy of stroopwafel's example plugin, which this build needs to give exactly the `.ipx` above. |

## Install

1. Copy `5usbe72e.ipx` to `wiiu/ios_plugins/` on the SD card.
2. Remove `5usbeth.ipx` (wafel_ax88772b) if it is there. Both patch the same driver code.
3. Plug the adapter into the console and boot with one of minute's **Patch (sd)** options.

Like the other plugins here, it needs `00core.ipx` and IOSU OSv10 (title version 15848). It checks the
instructions it patches before changing them.

## What it does

All changes are made in RAM, in IOSU's USB Ethernet driver. Nothing on the console is changed for good.

- **Chip setup.** An AX88772E uses the AX88179A register set. At first use the plugin reads the chip
  version and, for the AX88179A family, runs the start-up sequence from ASIX's own driver: firmware
  mode, EEPROM reload, PHY power, MAC setup, and a link-up sequence each time the driver sets the link
  mode. It also turns off the chip's CDC mode, which it starts in.
- **Packet headers.** Received and sent packets use the chip's 8-byte descriptors. The plugin converts
  them to and from the 4-byte headers the stock driver expects.
- **Link detection.** The stock driver waits for an ASIX PHY id, reads the link only from a 16-byte
  status message, and wants three link-up reports in a row. The AX88179A family sends 8-byte messages and
  reports only changes. The plugin bridges each of these. On every connection test it restarts
  autonegotiation, so the driver sees a fresh link-up even when the cable stayed connected.
- **Console restarts.** When the driver takes the interface down, the plugin stops the receiver
  instead of turning it back on with the endpoints disabled, which hung many restarts.
- **Sent data.** Each sent frame is moved 4 bytes to make room for the larger descriptor. The plugin
  flushes the data cache over the whole frame; without that, some full-size frames went out with their
  last bytes stale, and the chip's checksum offload hid the damage (it corrupted files written over
  PCFS).
- **Receive speed.** After each receive the stock driver decides when to queue the next one. When
  transfers come in less than full, it waits up to about 4 ms first, which held PCFS to about 0.4 MB/s
  (its replies arrive 4 KB at a time). The plugin makes it queue the next receive at once. That made
  PCFS loading about four times faster.

## Log

The plugin's lines start with `AX772E:` and appear in the USB serial log next to `USBETH Trace:`. The
first one, `probe vidpid VVVVPPPP bcd BBBB class CC`, shows the adapter it found; class `ff` is the one
it drives.

## Building

You need [devkitARM](https://devkitpro.org/) and a checkout of
[stroopwafel](https://github.com/StroopwafelCFW/stroopwafel), for its plugin headers (`wafel_core/include`).

```bash
export STROOPWAFEL_ROOT=/path/to/stroopwafel
./build.sh
```

Built with devkitARM GCC 16.1.0 against stroopwafel commit `f25549d`, this gives exactly the
`5usbe72e.ipx` above.

## Credits

- [wafel_ax88772b](https://github.com/StroopwafelCFW/wafel_ax88772b) (StroopwafelCFW): the AX88772A/B/C
  support, the USB host structures in `uhs.h`, and the driver addresses this plugin started from.
- [stroopwafel](https://github.com/StroopwafelCFW/stroopwafel) by Jan Hofmeier, shinyquagsire23 and
  contributors: the plugin loader, the plugin API and the build template.
- ASIX's AX88179A/772D/E Linux and Windows drivers: the chip start-up sequence and register values.
