"""程式入口。

    QELNetMeter.exe                 雙擊：安裝（複製到使用者資料夾、登入自動啟動）＋ 啟動背景程式 ＋ 顯示狀態視窗
    QELNetMeter.exe --agent         背景程式（登入時自動執行的就是這個）
    QELNetMeter.exe --update <pid>  自動更新：等舊的背景程式結束 → 換掉安裝的 exe → 啟動新版
    QELNetMeter.exe --install       安裝並啟動背景程式（不開視窗；大量部署用）
    QELNetMeter.exe --uninstall     停止背景程式、取消自動啟動
    QELNetMeter.exe --status        印出背景程式的狀態（JSON）
    QELNetMeter.exe --self-test     打包檢查
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import List, Optional

from . import APP_NAME, __version__, agent, hub, install


def _wait_agent(timeout: float = 8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = agent.control("status", timeout=1.0)
        if st is not None:
            return st
        time.sleep(0.3)
    return None


def ensure_running() -> str:
    """裝好並確定背景程式在跑；回傳給使用者看的一句話。"""
    st = agent.control("status")
    if st is not None and hub.vkey(st.get("version", "0")) >= hub.vkey(__version__):
        if not install.autostart_enabled():
            install.enable_autostart()
        return ""
    if st is not None:                                         # 舊版在跑 → 叫它結束，換新版
        agent.control("quit")
        agent.wait_gone(int(st.get("pid") or 0), 10)
    exe = install.copy_self()
    how = install.enable_autostart(exe)
    install.start_agent(exe)
    _wait_agent()
    return f"已安裝並開始在背景記錄（{how}）。" if st is None else f"已更新到 {__version__}。"


def uninstall() -> str:
    st = agent.control("quit")
    install.disable_autostart()
    return "已停止背景記錄、取消自動啟動。" + ("" if st is not None else "（背景程式原本就沒有在執行）")


def _print(text: str) -> None:
    """--noconsole 打包時沒有 stdout。"""
    if sys.stdout is not None:
        try:
            print(text, flush=True)
        except (OSError, ValueError):
            pass


def self_test(out: Optional[str] = None) -> int:
    import tempfile
    import os

    os.environ.setdefault("QELNETMETER_HOME", tempfile.mkdtemp(prefix="netmeter_test_"))
    from . import config, counters
    from .meter import Meter

    lines = [f"{APP_NAME} {__version__} 自我檢查（{sys.platform}，{'打包版' if install.frozen() else 'Python'}）"]
    ok = True
    try:
        s = counters.read()
        names = list(s["ifaces"])
        lines.append(f"✔ 網卡計數：{len(names)} 張（{'、'.join(names[:6])}）")
        m = Meter(config.data_dir() / "state.json", config.networks(config.load()), counters.read)
        m.step()
        time.sleep(0.5)
        m.step()
        m.save()
        lines.append(f"✔ 計量：在實驗室網段 = {bool(m.on_ifaces)}，狀態檔 {m.file.name}")
    except Exception as e:  # noqa: BLE001
        ok = False
        lines.append(f"✖ 計量：{e}")
    try:
        cfg = config.load()
        cfg["control_port"] = 0                                # 隨便一個空的埠
        ag = agent.Agent(cfg=cfg, reporter=lambda hubs, p: ("test", {"ok": True}))
        srv = agent.ControlServer(ag)
        import threading

        threading.Thread(target=srv.serve_forever, daemon=True).start()
        st = agent.control("status", port=srv.server_address[1])
        srv.shutdown()
        srv.server_close()
        assert st and st.get("version") == __version__, st
        lines.append("✔ 控制埠")
    except Exception as e:  # noqa: BLE001
        ok = False
        lines.append(f"✖ 控制埠：{e}")
    if os.environ.get("NETMETER_SELFTEST_GUI", "1") != "0":
        try:
            from .ui import StatusWindow

            w = StatusWindow(lambda: None, lambda: "", lambda: None, lambda: False, note="自我檢查")
            w.root.update()
            w.root.destroy()
            lines.append("✔ 視窗（tkinter）")
        except Exception as e:  # noqa: BLE001
            ok = False
            lines.append(f"✖ 視窗：{e}")
    _print("\n".join(lines))
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="QELNetMeter", description=APP_NAME)
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--update", metavar="PID", type=int)
    ap.add_argument("--install", action="store_true", help="安裝並啟動背景程式（不開視窗）")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--out", help="--self-test 的結果也寫到這個檔")
    a, _ = ap.parse_known_args(argv)
    if a.self_test:
        return self_test(a.out)
    if a.agent:
        agent.setup_logging()
        try:
            ag = agent.Agent()
            ag.run()
        except OSError as e:                                   # 控制埠被占用 = 已經有一個在跑
            agent.log.info("不啟動：%s", e)
        return 0
    if a.update:
        agent.setup_logging()
        agent.wait_gone(a.update, 20)
        exe = install.copy_self()
        install.enable_autostart(exe)
        install.start_agent(exe)
        return 0
    if a.install:
        _print(ensure_running() or "已經在背景執行")
        return 0
    if a.uninstall:
        _print(uninstall())
        return 0
    if a.status:
        _print(json.dumps(agent.control("status"), ensure_ascii=False, indent=1))
        return 0
    note = ""
    try:
        note = ensure_running()
    except Exception as e:  # noqa: BLE001
        note = f"⚠ 安裝 / 啟動失敗：{e}"
    from .ui import StatusWindow

    StatusWindow(lambda: agent.control("status", timeout=1.0), uninstall, lambda: agent.control("reload"),
                 install.autostart_enabled, note).run()
    return 0
