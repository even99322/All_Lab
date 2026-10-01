"""python -m labhub [--port 8765] [--data DIR] [--releases DIR] [--token TOKEN]"""
import sys

from .selfupdate import boot

boot(sys.argv[1:])            # data/hub_app 有更新的版本 → 切換過去執行（不回傳）

from .server import main  # noqa: E402

sys.exit(main())
