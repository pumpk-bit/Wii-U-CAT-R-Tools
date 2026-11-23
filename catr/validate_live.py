"""Read-only acceptance checks; run explicitly against the selected console."""
import concurrent.futures
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / 'src'))
from catr.transport.serial import rpc


def command(*args):
    result = subprocess.run([sys.executable, '-I', str(root / 'catr.py'), *args], capture_output=True, text=True, timeout=60)
    return {'args': args, 'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}


report = {'time': datetime.now(timezone.utc).isoformat(), 'console_mutations': False}
report['before'] = rpc({'op': 'status'})
# The read-only command and log subscriber use separate connections concurrently.
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    future = pool.submit(command, 'cos', 'help')
    report['concurrent_log_reads'] = []
    while not future.done():
        result = rpc({'op': 'log', 'cursor': report['before']['cursor']})
        report['concurrent_log_reads'].append({'cursor': result['cursor'], 'error': result['error']})
        time.sleep(.1)
    report['help'] = future.result()
report['info'] = command('info')
report['sdkversion'] = command('cos', 'sdkversion')
# `catr cos` sends any single command since 2026-11-14, so `cos kill` would really kill the
# title. What is still refused locally is more than one command in one line.
report['policy_rejects_chained'] = command('cos', 'help;kill')
report['after'] = rpc({'op': 'status'})
target = root / 'logs' / ('phase0-live-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(target)
checks = {'help': report['help']['exit'] == 0, 'info': report['info']['exit'] == 0,
          'sdkversion': report['sdkversion']['exit'] == 0,
          'chained_blocked': report['policy_rejects_chained']['exit'] != 0,
          'concurrent_log': len(report['concurrent_log_reads']) > 0 and all(item['error'] is None for item in report['concurrent_log_reads'])}
print(json.dumps(checks, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
