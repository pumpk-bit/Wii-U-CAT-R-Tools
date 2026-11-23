# catr

catr is a command-line toolkit for the CAT-R. It talks to the console in two ways:

- **over the network**, through wupserver (files, titles, installs, settings);
- **over the USB serial cable** (the console's log and its COS shell).

Run it from PowerShell or cmd in the repo folder:

```powershell
.\catr\catr.cmd info
```

CatR-Tools.exe has catr built in, so there the same command is `CatR-Tools info`. catr needs Python 3.12
or newer (`py -3`, or set `CATR_PYTHON` to a python.exe) and nothing else. Commands that change the console ask first; add `--yes`
to skip the question in scripts.

## Commands

The examples use `.\catr\catr.cmd`; with CatR-Tools.exe, write `CatR-Tools` instead.

### Connect and check

```powershell
.\catr\catr.cmd find                       # look for the console on this PC's network
.\catr\catr.cmd find --ip 192.168.1.50 --port COM3   # check that wupserver answers, save the IP and COM port
.\catr\catr.cmd info                       # default title, versions, free space
.\catr\catr.cmd info --iosu                # also check the IOSU files (takes about 80 s)
.\catr\catr.cmd serial --status            # the serial service that shares the COM port
.\catr\catr.cmd serial --stop              # stop it and free the COM port for other programs
```

### Console log and shell

```powershell
.\catr\catr.cmd log --follow               # follow the console log over the serial cable
.\catr\catr.cmd log --follow --grep TEXT   # only lines containing TEXT
.\catr\catr.cmd syslog --tail 50           # the IOSU log, read from memory (no serial cable needed)
.\catr\catr.cmd cos threads                # send one COS shell command and show the answer
```

### Files on the console

```powershell
.\catr\catr.cmd ls /vol/storage_mlc01/sys/title/00050010
.\catr\catr.cmd get -r /vol/storage_mlc01/usr/save/00050000 saves     # copy to this PC
.\catr\catr.cmd put -r .\folder /vol/storage_mlc01/usr/tmp/catr/folder   # copy to the console (checked after)
.\catr\catr.cmd mkdir /vol/storage_mlc01/usr/tmp/catr/new
.\catr\catr.cmd rm -r /vol/storage_mlc01/usr/tmp/catr/folder
```

Writes go to catr's own folder, `/vol/storage_mlc01/usr/tmp/catr`, unless you add `--unsafe`.

### Titles

```powershell
.\catr\catr.cmd titles --names             # what is installed (--json for scripts)
.\catr\catr.cmd icons                      # fetch title icons to this PC
.\catr\catr.cmd install D:\images\MyGame   # install an install image folder
.\catr\catr.cmd install D:\images\MyGame.wumad
.\catr\catr.cmd uninstall 0005000010c0be00
```

`install` copies the image to the console, reads every file back, installs it and cleans up. Add
`--size-only` to check only file sizes: about twice as fast, but a damaged file is not caught (the
console's installer does not check file contents either).

### Starting and stopping titles

```powershell
.\catr\catr.cmd launch 0005000010c0be00              # start a title
.\catr\catr.cmd launch 0005000010c0be00 mode=test    # start it with arguments
.\catr\catr.cmd restart                              # start the running title again
.\catr\catr.cmd kill                                 # end the running title
.\catr\catr.cmd reboot                               # restart the console and wait for it
.\catr\catr.cmd default-title                        # which title the console boots into
```

### Running your own builds over PCFS

PCFS lets the console load a title straight from a folder on this PC, without installing it.

```powershell
.\catr\catr.cmd run D:\builds\MyGame          # copy the build to the PCFS folder, connect, start it, show the log
.\catr\catr.cmd run D:\builds\MyGame --until "Ready" --wait 120   # stop once the log shows "Ready"
.\catr\catr.cmd stop                          # back to the Recovery Menu, stop PCFS
.\catr\catr.cmd pcfs up                       # connect PCFS now (restarts the console)
.\catr\catr.cmd pcfs keep --background        # connect at every boot, also when you power the console on
.\catr\catr.cmd pcfs keep --stop
.\catr\catr.cmd pcfs status
.\catr\catr.cmd pcfs down                     # stop the PCFS server
.\catr\catr.cmd pcfs root D:\pcfs             # show or change the PC folder the console sees
```

### SPARK captures

```powershell
.\catr\catr.cmd spark wait                    # wait for a capture (press A on the console) and copy it here
.\catr\catr.cmd spark list
.\catr\catr.cmd spark open                    # open the newest capture in SPARK
.\catr\catr.cmd hio                           # live capture from SPARK, for titles that support it
```

### Debugging

```powershell
.\catr\catr.cmd debug threads                 # also: stacks, modules, heaps, cpu
.\catr\catr.cmd debug mem 0x10023c00 32       # read memory (the address is checked first)
.\catr\catr.cmd debug disasm 0x02000000
.\catr\catr.cmd debug sym 0x02000000          # which module and symbol an address is in
.\catr\catr.cmd gdb                           # a read-only GDB server on port 2331
```

Connect GDB with `powerpc-eabi-gdb -ex "target remote 127.0.0.1:2331"` (devkitPPC). It shows a snapshot
of the running title: threads, registers and memory. The title keeps running; breakpoints, stepping and
memory writes are not supported.

### Console settings

```powershell
.\catr\catr.cmd config get                    # system settings (also: rtc, sys_prod)
.\catr\catr.cmd config get system log/enable
.\catr\catr.cmd config set log/enable 1 --dry-run   # show what would change
.\catr\catr.cmd config set log/enable 1
.\catr\catr.cmd maintenance status            # boot into another title for a while: sct, menu, tiramisu
.\catr\catr.cmd maintenance sct
.\catr\catr.cmd maintenance end               # back to the Recovery Menu
```

### Backups, crash logs and repair

```powershell
.\catr\catr.cmd backup config                 # also: sct, sct-code, recovery-menu
.\catr\catr.cmd backup mydata --path /vol/storage_mlc01/usr/save/00050000/10101000
.\catr\catr.cmd restore BACKUP --check        # compare the console with a backup, change nothing
.\catr\catr.cmd restore BACKUP --to /vol/storage_mlc01/usr/tmp/catr/restored
.\catr\catr.cmd crashlogs                     # copy the console's system logs here (about 7 minutes)
.\catr\catr.cmd repair recovery-menu D:\images\CAT-Recovery-Menu
```

Backups are saved in the workspace under `backups`, with a manifest of every file's
size and checksum.

## Safety rules

catr refuses things that could leave the CAT-R unable to boot:

- **Questions first.** Anything that changes the console asks before it starts. Scripts pass `--yes`.
- **Files.** `put`, `rm` and `mkdir` only write to catr's own folder unless you add `--unsafe`. System
  folders and installed titles are never written or deleted directly.
- **Installs.** `install` refuses system titles that the console needs to boot, the Recovery Menu, and
  older versions of installed titles. `uninstall` only removes games, updates and DLC on MLC or USB.
- **Boot title.** The title the console boots into only changes through `maintenance`, and `info`
  reminds you until `maintenance end` switches back to the Recovery Menu (it refuses if the menu isn't
  installed).
- **Settings.** `config set` only changes known-safe keys. It never changes the default title, the OS
  version or anything in `sys_prod`. It saves a backup first, reads the value back, and puts the old
  file back if the read-back is wrong.
- **Restores.** `restore` never writes over system or title folders (use `--to` to restore somewhere
  else). The one exception is a Recovery Menu backup, which needs a typed confirmation.
- **Recovery Menu.** `repair recovery-menu` is the only way to reinstall it. It checks the package,
  backs up the current menu, asks for a typed confirmation, and checks that the menu starts again.
- **COS shell.** `cos` sends any command. Commands that only read are retried if needed; everything else
  is sent once and written to catr's audit log. Commands that take an address (`memdump`, `disasm`) can
  crash the console on a bad address; `debug mem` and `debug disasm` check the address first.
- **Debugger.** `kill` refuses while a debugger has the title stopped (that can hang the console).
  Detach or continue in the debugger first. The debugger plugin is in the repository, in `plugins/dk-tcp`.

## Good to know

- **The COM port** is the one saved with `find --port`. With none saved, catr uses the only USB serial port
  on the PC, and asks you to choose when there are several (Device Manager > Ports lists them).
- **The serial cable.** One hidden catr service opens the COM port (57600 baud) and shares it with every
  catr command. Close other serial programs first, or run `serial --stop` before using one.
- **Transfers** over wupserver run at about 1 MB/s.
- **PCFS titles are read when PCFS connects.** A title copied in while PCFS is up shows in the Recovery
  Menu but can't start until the next connection. `run` notices and reconnects.
- **`usr/tmp` over PCFS is emptied at every boot**, on the PC side too. Don't keep files there.
- **A title that ignores the exit request** blocks `reboot` and `launch`; only power-cycling helps.
- **After a fatal error** the console can stop during shutdown (the log ends with
  `PPC TOLD TO SHUT DOWN`). `stop` says so; power-cycle the console.
- **Don't mount the SD card** over wupserver: it can hang the console.
- **Git Bash** changes `/vol/...` paths; set `MSYS_NO_PATHCONV=1` there, or use PowerShell or cmd.

## Development

Tests (no console needed) and the live check:

```powershell
py -3 -m unittest discover -s .\catr\tests -v
py -3 -I .\catr\validate_live.py
```

- **Workspace.** catr keeps backups, captures and logs in a workspace folder: `CATR_WORKSPACE`, else
  `"workspace"` in catr's config.json, else the `workspace` folder next to catr (git ignores it).
- **Cafe SDK.** `"cafe_root"` in catr's config.json, else the `CAFE_ROOT` variable.
- **Settings file.** `CATR_CONFIG_FILE`, else the file named by `"config_file"` in
  `%APPDATA%\catr\config.json` (CatR-Tools.exe points it at its own config.json), else that file.
- **PCFS folder.** `catr pcfs root`, else the `PCFS` folder in the workspace. It needs the Cafe SDK's
  `data\disc`, `data\mlc` and `data\slc` in its own `data` folder (CatR-Tools.exe copies them for you).
- **Other variables.** `CATR_LOGS` (audit and debug logs) and
  `CATR_PCFS_SERVER` (pcfsserver.exe; by default the repo's `pcfsserver\pcfsserver.exe`, see its
  `CHANGES.md`). CatR-Tools.exe sets them for its built-in catr.
- **wupclient.** `src/catr/vendor/wupclient.py` is a modified copy of the wupclient from
  [iosuhax](https://github.com/smealum/iosuhax) by smealum (non-commercial license; full text in
  `src/catr/vendor/LICENSE-iosuhax.md`). The local changes are listed in `PROVENANCE.json`.
