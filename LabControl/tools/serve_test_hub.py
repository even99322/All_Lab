"""測試用的 Hub（不用登入）：發佈指定的安裝檔後一直服務，給一鍵安裝程式 / 程式內更新做端對端測試。

    python tools/serve_test_hub.py <資料夾> <埠> 安裝檔...       （GitHub Actions 的 Windows 測試用）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv) -> int:
    from labhub.packages import identify, identify_binary
    from labhub.server import Config, make_server

    data, port, files = Path(argv[1]), int(argv[2]), [Path(p) for p in argv[3:]]
    cfg = Config(data, token=None)
    srv, state = make_server(cfg, "127.0.0.1", port)
    for f in files:
        raw = f.read_bytes()
        b = identify(raw, f.name) if f.suffix.lower() == ".zip" else identify_binary(f.name, raw)   # 寵物圖檔 zip
        if b is None:
            raise SystemExit(f"認不出 {f.name}")
        state.platform.add_release(b["target"], b["version"], f.name, raw, "ci", notes="CI")
        print(f"published {b['target']} {b['version']} {f.name}", flush=True)
    print(f"ready http://127.0.0.1:{srv.server_address[1]}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
