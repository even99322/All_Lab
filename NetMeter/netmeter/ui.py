"""狀態視窗（tkinter）：雙擊程式時顯示；關掉視窗背景程式照常記錄。"""
from __future__ import annotations

import time
import webbrowser
from typing import Any, Callable, Dict, Optional

from . import APP_NAME, __version__, config


def fmt(b: float) -> str:
    b = float(b or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or u == "TB":
            return f"{b:.0f} {u}" if u == "B" else f"{b:.2f} {u}" if b < 10 else f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def ago(t: Optional[float]) -> str:
    if not t:
        return "—"
    s = max(0, time.time() - t)
    return f"{s:.0f} 秒前" if s < 90 else f"{s / 60:.0f} 分前" if s < 5400 else f"{s / 3600:.0f} 小時前"


class StatusWindow:
    def __init__(self, get_status: Callable[[], Optional[Dict[str, Any]]], on_uninstall: Callable[[], str],
                 on_reload: Callable[[], None], autostart: Callable[[], bool], note: str = "") -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.get_status, self.on_uninstall, self.on_reload = tk, get_status, on_uninstall, on_reload
        self.autostart = autostart
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {__version__}")
        self.root.resizable(False, False)
        f = ttk.Frame(self.root, padding=14)
        f.grid()
        self.v_state = tk.StringVar()
        self.v_today = tk.StringVar()
        self.v_month = tk.StringVar()
        self.v_rate = tk.StringVar()
        self.v_report = tk.StringVar()
        self.v_auto = tk.StringVar()
        ttk.Label(f, text=APP_NAME, font=("", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(f, textvariable=self.v_state, wraplength=420).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 8))
        rows = (("今天", self.v_today), ("本月", self.v_month), ("目前速率", self.v_rate), ("回報 Hub", self.v_report),
                ("自動啟動", self.v_auto))
        for i, (k, v) in enumerate(rows, start=2):
            ttk.Label(f, text=k, foreground="#6b7280").grid(row=i, column=0, sticky="nw", padx=(0, 12))
            ttk.Label(f, textvariable=v, wraplength=360).grid(row=i, column=1, sticky="w")
        if note:
            ttk.Label(f, text=note, foreground="#2b8a3e", wraplength=420).grid(row=8, column=0, columnspan=2,
                                                                              sticky="w", pady=(8, 0))
        b = ttk.Frame(f)
        b.grid(row=9, column=0, columnspan=2, sticky="we", pady=(12, 0))
        ttk.Button(b, text="開啟「網路用量」網頁", command=self.open_web).pack(side="left")
        ttk.Button(b, text="設定…", command=self.settings).pack(side="left", padx=6)
        ttk.Button(b, text="停止並移除", command=self.uninstall).pack(side="left")
        ttk.Button(b, text="關閉視窗", command=self.root.destroy).pack(side="right")
        ttk.Label(f, text="關閉視窗後仍在背景記錄；只算連到實驗室網段時的流量。", foreground="#6b7280").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.refresh()

    def refresh(self) -> None:
        st = self.get_status()
        if st is None:
            self.v_state.set("⚠ 背景程式沒有在執行（重新打開這個程式就會啟動）")
            for v in (self.v_today, self.v_month, self.v_rate, self.v_report):
                v.set("—")
        else:
            if st.get("on_net"):
                self.v_state.set(f"● 已連到實驗室網段（{'、'.join(st.get('ifaces') or [])}　{'、'.join(st.get('ips') or [])}），"
                                 "正在記錄")
            else:
                self.v_state.set(f"○ 目前不在實驗室網段（{'、'.join(st.get('subnets') or [])}），這段時間不計算")
            t, m, r = st.get("today") or {}, st.get("month") or {}, st.get("rate") or {}
            self.v_today.set(f"↓ {fmt(t.get('down'))}　↑ {fmt(t.get('up'))}" + _split_text(t))
            self.v_month.set(f"↓ {fmt(m.get('down'))}　↑ {fmt(m.get('up'))}" + _split_text(m))
            self.v_rate.set(f"↓ {fmt(r.get('down'))}/s　↑ {fmt(r.get('up'))}/s")
            lr = st.get("last_report") or {}
            if lr.get("ok"):
                self.v_report.set(f"{ago(lr.get('time'))}送到 {lr.get('hub')}")
            elif st.get("error"):
                self.v_report.set(f"失敗：{st['error']}")
            else:
                self.v_report.set("還沒送（連到實驗室網段後每分鐘送一次）")
            sp = st.get("split") or {}
            if not sp.get("ok") and sp.get("kind"):
                self.v_today.set(self.v_today.get() + f"\n（分不出內網 / 外網：{sp.get('error') or '沒有啟動'}）")
            lh = st.get("lan_https") or {}
            if lh.get("hosts"):
                self.v_report.set(self.v_report.get() + f"\n內網 https：{lh.get('domain')} → {lh.get('ip')}")
            if st.get("update"):
                self.v_report.set(self.v_report.get() + f"\n有新版 {st['update'].get('version')}")
        a = self.autostart()
        self.v_auto.set(a if isinstance(a, str) else ("已設定（登入時自動在背景執行）" if a else "沒有設定"))
        self.root.after(2000, self.refresh)

    def open_web(self) -> None:
        st = self.get_status() or {}
        lr = st.get("last_report") or {}
        hub = lr.get("hub") or (config.load()["hubs"] or [""])[0]
        lh = st.get("lan_https") or {}
        if lh.get("hosts") and lh.get("url"):                 # 1.0.4：內網也用 https 網址
            hub = lh["url"]
        webbrowser.open(hub + "/network")

    def settings(self) -> None:
        tk = self.tk
        from tkinter import messagebox, ttk

        cfg = config.load()
        w = tk.Toplevel(self.root)
        w.title("設定")
        w.resizable(False, False)
        f = ttk.Frame(w, padding=12)
        f.grid()
        hubs = tk.StringVar(value=", ".join(cfg["hubs"]))
        nets = tk.StringVar(value=", ".join(cfg["subnets"]))
        ttk.Label(f, text="Hub 位址").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=hubs, width=46).grid(row=0, column=1, pady=3)
        ttk.Label(f, text="實驗室網段").grid(row=1, column=0, sticky="w")
        ttk.Entry(f, textvariable=nets, width=46).grid(row=1, column=1, pady=3)
        ttk.Label(f, text="多個用逗號分開；Hub 依序試。", foreground="#6b7280").grid(row=2, column=1, sticky="w")

        def ok() -> None:
            hs = [config.normalize_hub(x) for x in hubs.get().split(",") if x.strip()]
            ns = [x.strip() for x in nets.get().split(",") if x.strip()]
            bad = [x for x in ns if not config.valid_subnet(x)]
            if bad or not hs or not ns:
                messagebox.showerror("設定", f"網段格式不對：{', '.join(bad)}（例如 192.168.50.0/24）" if bad else "不可空白",
                                     parent=w)
                return
            cfg.update(hubs=hs, subnets=ns)
            config.save(cfg)
            self.on_reload()
            w.destroy()
        ttk.Button(f, text="儲存", command=ok).grid(row=3, column=1, sticky="e", pady=(8, 0))

    def uninstall(self) -> None:
        from tkinter import messagebox

        if messagebox.askyesno("停止並移除", "停止背景記錄，並取消登入時自動啟動？\n（已經送到 Hub 的用量會留著）",
                               parent=self.root):
            messagebox.showinfo("停止並移除", self.on_uninstall(), parent=self.root)

    def run(self) -> None:
        self.root.mainloop()


def _split_text(d: dict) -> str:
    """1.0.4：內網 / 外網 / VPN（有資料才顯示）。"""
    tot = {c: int(d.get(f"{c}_up") or 0) + int(d.get(f"{c}_down") or 0) for c in ("lan", "wan", "tun", "tunw")}
    if not any(tot.values()):
        return ""
    s = f"\n內網 {fmt(tot['lan'])}　外網 {fmt(tot['wan'])}　VPN {fmt(tot['tun'])}"
    return s + (f"（經外網 {fmt(tot['tunw'])}）" if tot["tunw"] else "")
