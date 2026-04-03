#!/usr/bin/env python3

from pathlib import Path
from sys import path

path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.plugin_deps_sync import main

if __name__ == '__main__':
    raise SystemExit(main())
