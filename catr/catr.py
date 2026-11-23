"""Uninstalled entry point; works with Python isolated mode."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from catr.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
