"""Serial log lines for the tests: only the ones catr looks at."""

INCIDENT = '''\
----------- Starting System Log -----------
00:00:01:020: MCP: Boot PM flags - PON_COLDBOOT PON_POWER_BTN PON_SMC 
00:00:02:858: PCFSTCP[V11]: selected native TCP transport=3 port=7500
00:00:07:134: PCFSTCP[V11]: TCP_SM_Init result=0 attempts=2 retries=1 ncl_fd=815108
00:00:07:135: PCFS: Waiting for PC connection on port 7500...
00:00:17:134: PCFS: No response from PCFSServer, PCFS file system is unavailable!
00;00;18;084: ROOT: OSID 0x000500101000400A
00;00;18;801: DIAG:Issued KiPrepareTitleAsync(0x000500301001110A)
00;00;19;494: ------------------PROCESS 6 GAIN FOREGROUND--------------------
cos slowlaunch 0x000500101F700700
00;00;20;149: ------------------PROCESS 1 GAIN FOREGROUND--------------------
00;00;20;150: DIAG:Issued KiPrepareTitleAsync(0x0005003010010100)
00;00;20;159: ***Launch or Call Outstanding. Cannot do another concurrently.
Process 1, Error 0xCAFEBADD
'''

NORMAL = '''\
----------- Starting System Log -----------
00;00;18;012: ------------------PROCESS 1 GAIN FOREGROUND--------------------
00;00;18;013: DIAG:Issued KiPrepareTitleAsync(0x0005003010010100)
00;00;18;242: ------------------PROCESS 5 GAIN FOREGROUND--------------------
00;00;19;925: ------------------PROCESS 1 GAIN FOREGROUND--------------------
ROOT IS STARTING MASTER TITLE
00;00;19;926: DIAG:Issued KiPrepareTitleAsync(0x000500101F700700)
00;00;20;021: ------------------PROCESS 2 GAIN FOREGROUND--------------------
'''

FAST = '''\
00;01;58;107: DIAG:Issued KiPrepareTitleAsync(0x000500301001710A)
00;01;58;304: ------------------PROCESS 1 GAIN FOREGROUND--------------------
00;01;58;361: ROOT: Issuing reboot.
---PPC USER-LEVEL FAST RESTART---
ROOT IS STARTING MASTER TITLE
00;01;58;596: DIAG:Issued KiPrepareTitleAsync(0x000500301001710A)
00;01;58;831: ------------------PROCESS 15 GAIN FOREGROUND--------------------
'''
