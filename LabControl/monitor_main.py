"""Lab Control Monitor 的 exe 進入點（PyInstaller 用；也可以 python monitor_main.py）。"""
import sys

from labmonitor.app import main

if __name__ == "__main__":
    sys.exit(main())
