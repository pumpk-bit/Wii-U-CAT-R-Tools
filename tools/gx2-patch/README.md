# gx2 patch for SPARK

`patch_gx2_spark_gate.py` lets SPARK capture frames on a CAT-R.

The console's `gx2.rpl` only accepts SPARK captures on certain console types and refuses on a CAT-R
(`Capture not supported in production mode`). The script turns that one check off. It changes a single
instruction, and leaves the security level check next to it alone.

No Nintendo code is included: the script downloads `gx2.rpl` from your own console over FTP, patches it,
and uploads it again.

> **This changes a system file on the console's SLC.** If it gets damaged, the titles that need it stop
> starting, the CAT Recovery Menu and FTP included. **Make a NAND backup first**, and don't restart the
> console until the script says `readback matches`.

## What you need

- Python 3 on the PC.
- OS title `000500101000400A` with the **SDK 2.13.01** `gx2.rpl`. The script refuses any other version.
- An FTP server running on the console that can write system files, for example ftpiiu under Tiramisu.
  System Config Tool and the Recovery Menu don't have one.

## Use it

First without changing anything on the console:

```powershell
py -3 -I .\tools\gx2-patch\patch_gx2_spark_gate.py 192.168.1.50 --dry-run
```

Then for real. It asks before it uploads:

```powershell
py -3 -I .\tools\gx2-patch\patch_gx2_spark_gate.py 192.168.1.50
```

When it says `readback matches`, restart the console. The old `gx2.rpl` stays in use until then.

- The downloaded and patched files are kept in `workspace\gx2-patch`, which git ignores. Keep the
  `gx2.rpl.downloaded-...` file: it is your way back. **Don't share either file**, they are Nintendo's.
- If the upload goes wrong, the script puts the original back. If that fails too, it tells you **not to
  restart**, and which file to upload by hand.
- If your FTP server uses other paths, pass `--path`.

**To undo it:** upload the `gx2.rpl.downloaded-...` file to
`/storage_slc/sys/title/00050010/1000400a/code/gx2.rpl` over FTP, and restart.

| Option | What it does |
|---|---|
| `--dry-run` | Downloads and patches on the PC only |
| `--yes` | Uploads without asking |
| `--path PATH` | Where `gx2.rpl` is on the FTP server |
| `--port N` | FTP port (21) |
| `--work FOLDER` | Where the copies are kept |
