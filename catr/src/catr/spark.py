"""SPARK frame captures written over PCFS (file capture only; Analyze/live Capture need HIO).

A SPARK title (SparkCube) logs, when A is pressed (serial, 2026-11-14):
    SparkCube: capture request 1 at frame 745 -> /cygdrive/%SAVE_EMU_DIR/spark/spark_cube.4mp
    GX2: Warning! Swap interval set 0.  Use this setting only for debug!
    ... ~20 s of PCFS writes (the title pauses) ...
    SparkCube: alive, frame 1200
`/cygdrive/%SAVE_EMU_DIR/` is pcfs-root\\data\\save\\. A capture file starts with b'PM4\\n'.
"""
import re
from pathlib import Path
from . import config, pcfs

SAVE_DIR = pcfs.host_root() / 'data/save'
MAGIC = b'PM4\n'
REQUEST = re.compile(r'capture request (\d+) at frame (\d+) -> (/cygdrive/%SAVE_EMU_DIR/(\S+))')
ALIVE = re.compile(r'alive, frame (\d+)')
REJECTED = 'Capture not supported in production mode'


def spark_exe():
    return config.cafe_path('system/bin/win64/Spark.exe')[1]


def host_path(relative, save_dir=SAVE_DIR):
    path = (Path(save_dir) / relative).resolve()
    if Path(save_dir).resolve() not in path.parents:
        raise ValueError(f'capture path {relative!r} leaves the PCFS save folder')
    return path


class CaptureWatch:
    """Serial text -> capture requests and whether the title has resumed after each."""

    def __init__(self, save_dir=SAVE_DIR):
        self.save_dir, self.partial = save_dir, ''
        self.captures, self.rejected = [], False

    def feed(self, text):
        lines = re.split('[\r\n]', self.partial + text)
        self.partial = lines.pop()[-4096:]
        for line in lines:
            match = REQUEST.search(line)
            if match:
                self.captures.append({'request': int(match[1]), 'frame': int(match[2]), 'console_path': match[3],
                                      'path': str(host_path(match[4], self.save_dir)), 'resumed_frame': None})
            elif REJECTED in line:
                self.rejected = True
            else:
                match = ALIVE.search(line)
                if match and self.captures and self.captures[-1]['resumed_frame'] is None:
                    self.captures[-1]['resumed_frame'] = int(match[1])

    @property
    def pending(self):
        return [c for c in self.captures if c['resumed_frame'] is None]


def describe(path):
    path = Path(path)
    stat = path.stat()
    with path.open('rb') as handle:
        head = handle.read(4)
    return {'path': str(path), 'bytes': stat.st_size, 'modified': stat.st_mtime, 'pm4': head == MAGIC}


def listing(save_dir=SAVE_DIR):
    return sorted((describe(p) for p in Path(save_dir).rglob('*.4mp')), key=lambda item: item['modified'])
