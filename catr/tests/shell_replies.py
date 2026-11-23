"""COS shell replies for the tests: short made-up examples in the console's format."""

STACK = 0x1055EC58          # the thread's r1 in CTXDUMP
CONTEXT = 0x100457E0        # its ThredCtx in THREADS

MODULES = '''\
cos modules
# 00;00;01;000:
Process 7 Loaded RPLs:
00;00;01;000: Code:0x02000000-0x02016d90 Data:0x10502200-0x1053cacc LoadInfo:0x1053cb00-0x1053ee88 FileInfo:0x1057236c-0x10572454 Module:cat_recovery_menu
00;00;01;000: Code:0x0101c400-0x01090f00 Data:0x10000000-0x10223d80 LoadInfo:0xfa000000-0xfa001000 FileInfo:0x10572134-0x1057223b Module:coreinit.rpl
00;00;01;000: Code:0x01100000-0x01180000 Data:0x10300000-0x10340000 LoadInfo:0xfa002000-0xfa003000 FileInfo:0x10572500-0x10572600 Module:example.rpl

[outcome: observed; read-only retry: False]
'''

THREADS = '''\
cos threads
# 00;00;02;000:
---- COS Debugging Shell Command: threads ----
00;00;02;000: Last 32 Threads:
00;00;02;000:  0:00[0x10085898] {SYS HeartBeat Core 0}
00;00;02;000: >1:01[0x00000000]
00;00;02;000:
 Time             Stat ThredCtx Attr Pr Ba Ctx.Stak Ctx.LR   Ctx.SRR0
00;00;02;000:  0000000000001000 WAIT 100457E0 000A 50 50 1055EC58 0103D2FC 0103D2FC coreinit.rpl|__OSUnlockScheduler Default Core 1
00;00;02;000:  0000000000000100 WAIT 10039178 0001 00 00 10023C28 0103D2FC 0103D2FC coreinit.rpl|__OSUnlockScheduler {SYS Alarm Driver Core 0} Waiting on OSAlarmQueue 0x10037D2C
00;00;02;000:  0000000000002000 RUNN 10096208 000A 00 00 100950B0 0103D2FC 0103D2FC coreinit.rpl|__OSUnlockScheduler {SYS CDS Thread}
00;00;02;000:  0000000000000200 WAIT 1031B8F8 000A 30 30 10A3A260 0103D2FC 0103D2FC coreinit.rpl|__OSUnlockScheduler  Waiting on OSMessageQueue 0x1031C198

[outcome: observed; read-only retry: False]
'''

HEAPS = '''\
cos heaps
# 00;00;03;000:
---- COS Debugging Shell Command: heaps ----
00;00;03;000:
Showing 3 heaps:

00;00;03;000: Heap Addr   Type   Range                    TotalSize   LargestFree TotalFree
00;00;03;000: 0x10572000  Exp    0x10572000 - 0x1057A000  0x00008000  0x00004000  0x00004000
00;00;03;000: 0xF4000000  Frame  0xF4000000 - 0xF6000000  0x02000000  0x00000000
00;00;03;000: 0x1057B000  Exp    0x1057B000 - 0x41FA0000  0x31A25000  0x10000000  0x10000000
00;00;03;000:

00;00;03;000: Code Addr   Type   Area Range               TotalAreaSize   Heap Range               TotalHeapSize   LargestFree TotalFree
00;00;03;000: 0x02000000  Code   0x02000000 - 0x10000000  0x0E000000      0x02000000 - 0x0FFFC000  0x0DFFC000      0x0DF00000  0x0DF00000
00;00;03;000:
---- Done ----

[outcome: observed; read-only retry: False]
'''


def _ctxdump():
    gpr = {1: STACK, 2: 0x1050A200, 13: 0x1050A200}
    rows = [f'r{i:<2}  = 0x{gpr.get(i, 0):08x} ({gpr.get(i, 0):14d})  r{i + 16}  = 0x00000000 (             0)'
            for i in range(16)]
    rows += ['LR   = 0x0103d2fc                   CR   = 0x20000022', 'SRR0 = 0x0103d2fc                   SRR1 = 0x00007072']
    for i in range(0, 32, 2):
        rows.append(f'fr{i} \t= 0x{0x3F911570C0000000 if i == 0 else 0:016x} \t fr{i + 1} \t= 0x{0:016x}')
    return ('cos ctxdump 0x100457E0\n# 00;00;04;000: \n---- COS Debugging Shell Command: ctxdump ----\n'
            + ''.join(f'00;00;04;000: {row}\n' for row in rows)
            + '00;00;04;000: 0x1055ec58:   0x1055ec78    0x0103d2fc coreinit.rpl|__OSUnlockScheduler+0x81c\n'
            + '\n[outcome: observed; address; sent once, never resent]\n')


CTXDUMP = _ctxdump()

# A title's own log line can come in between.
MEMSTACK = ('cos memdump 0x1055EC58 32\n# 00;00;05;000: \n---- COS Debugging Shell Command: memdump ----\n'
            '00;00;05;000: 0x1055EC58: 1055EC78 0103D2FC 00000001 00000001  U x               \n'
            '00;00;05;000: a log line of the title\n'
            + ''.join(f'00;00;05;000: 0x{row:08X}: 00000000 00000000 00000000 00000000                  \n'
                      for row in range(STACK + 16, STACK + 128, 16))
            + '\n[outcome: observed; address; sent once, never resent]\n')
