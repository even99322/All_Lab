"""LAB-QEL論文庫 控制台：監控網站狀況、看錯誤紀錄與容器日誌、執行背景工作、備份、重啟與更新網站。

執行：雙擊專案資料夾裡的 console-windows.bat（Mac：console-mac.command）。
需要 Python 3.10 以上；第一次執行會自動安裝連 NAS 用的 paramiko。
"""
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402

# 控制台所在的這份程式（用 .py 執行時；打包成 exe 時沒有，改由使用者選新版 zip）
HERE_ROOT = None if getattr(sys, "frozen", False) else core.find_root(Path(__file__).resolve().parents[2]) if len(Path(__file__).resolve().parents) > 2 else None
OK, WARN, BAD, OFF = "#16a34a", "#d97706", "#dc2626", "#9ca3af"
JOBS = [("feeds", "檢查新論文"), ("refs", "重新分析引用"), ("versions", "檢查預印本是否已發表"), ("scan_text", "檢查 PDF 文字層"),
        ("similar_index", "重建相似度索引"), ("digest", "寄送每週摘要")]


def ago(iso: str) -> str:
    if not iso:
        return "—"
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        s = (datetime.now(timezone.utc) - t).total_seconds()
    except ValueError:
        return iso
    return "剛剛" if s < 90 else f"{int(s // 60)} 分鐘前" if s < 3600 else f"{int(s // 3600)} 小時前" if s < 86400 else f"{int(s // 86400)} 天前"


class Console(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = core.load_config()
        self.title("LAB-QEL論文庫 控制台")
        try:
            ico = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "console.ico"
            if ico.exists() and core.platform_name() == "windows":
                self.iconbitmap(default=str(ico))
        except tk.TclError:
            pass
        self.geometry("1120x760")
        self.minsize(900, 600)
        self.q: "queue.Queue" = queue.Queue()
        self.site = core.Site(self.cfg["site_url"], self.cfg.get("cookie", ""))
        self.nas: core.Nas | None = None
        self.nas_pw = ""
        self.status = None
        self.state_container = None
        self.history: list[tuple[float, bool, float]] = []       # (時間, 是否正常, 回應毫秒)
        self.down_since = None
        self.busy = False
        self._style()
        self._build()
        self.after(100, self._pump)
        self.after(300, self.refresh)
        self._schedule()
        self.after(1500, self._offer_shortcut)
        self.protocol("WM_DELETE_WINDOW", self._quit)

    def _offer_shortcut(self):
        """exe 第一次開啟：問要不要放到桌面（複製到本機程式資料夾＋桌面捷徑）。"""
        if not getattr(sys, "frozen", False) or core.platform_name() != "windows" or self.cfg.get("shortcut_asked"):
            return
        self.cfg["shortcut_asked"] = True
        core.save_config(self.cfg)
        if messagebox.askyesno("論文庫控制台", "要在桌面建立「論文庫控制台」捷徑嗎？\n（程式會放到本機的應用程式資料夾，之後從桌面打開）", parent=self):
            try:
                path = core.install_desktop_shortcut(sys.executable)
                messagebox.showinfo("論文庫控制台", f"已建立桌面捷徑：\n{path}\n\n下載的 exe 可以刪掉了。", parent=self)
            except Exception as e:  # noqa: BLE001
                messagebox.showwarning("論文庫控制台", f"沒辦法建立捷徑：{e}\n可以直接把這個 exe 拖到桌面使用。", parent=self)

    # ---------------------------------------------------------------- 外觀
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("vista" if core.platform_name() == "windows" else "clam")
        except tk.TclError:
            pass
        base = ("Microsoft JhengHei UI", 10) if core.platform_name() == "windows" else ("PingFang TC", 12) if core.platform_name() == "mac" else ("Noto Sans CJK TC", 10)
        self.option_add("*Font", base)
        st.configure("Card.TLabelframe", padding=10)
        st.configure("Big.TLabel", font=(base[0], base[1] + 8, "bold"))
        st.configure("Muted.TLabel", foreground="#6b7280")
        st.configure("Accent.TButton", font=(base[0], base[1], "bold"))
        self.base_font = base

    def _build(self):
        top = ttk.Frame(self, padding=(12, 10))
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=18, height=18, highlightthickness=0)
        self.dot.pack(side="left")
        self.dot_id = self.dot.create_oval(2, 2, 16, 16, fill=OFF, outline="")
        self.head = ttk.Label(top, text="檢查中…", style="Big.TLabel")
        self.head.pack(side="left", padx=8)
        self.sub = ttk.Label(top, text="", style="Muted.TLabel")
        self.sub.pack(side="left", padx=6)
        ttk.Button(top, text="設定", command=self.open_settings).pack(side="right", padx=3)
        ttk.Button(top, text="開啟網站", command=lambda: webbrowser.open(self.cfg["site_url"])).pack(side="right", padx=3)
        ttk.Button(top, text="立即重新整理", command=self.refresh).pack(side="right", padx=3)
        self.login_btn = ttk.Button(top, text="登入網站", command=self.login_dialog)
        self.login_btn.pack(side="right", padx=3)
        self.nas_btn = ttk.Button(top, text="連線 NAS", command=self.nas_dialog)
        self.nas_btn.pack(side="right", padx=3)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._tab_overview()
        self._tab_users()
        self._tab_jobs()
        self._tab_logs()
        self._tab_update()
        self.foot = ttk.Label(self, text="", style="Muted.TLabel", padding=(12, 0, 12, 8))
        self.foot.pack(fill="x")

    # ---------------------------------------------------------------- 分頁：總覽
    def _card(self, parent, title, r, c, rowspan=1, colspan=1):
        f = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
        f.grid(row=r, column=c, rowspan=rowspan, columnspan=colspan, sticky="nsew", padx=5, pady=5)
        return f

    def _kv(self, parent, keys):
        out = {}
        for i, (k, label) in enumerate(keys):
            ttk.Label(parent, text=label, style="Muted.TLabel").grid(row=i, column=0, sticky="w", pady=1)
            v = ttk.Label(parent, text="—")
            v.grid(row=i, column=1, sticky="w", padx=(10, 0), pady=1)
            out[k] = v
        return out

    def _tab_overview(self):
        f = ttk.Frame(self.tabs, padding=6)
        self.tabs.add(f, text="  總覽  ")
        for c in range(3):
            f.columnconfigure(c, weight=1, uniform="c")
        f.rowconfigure(2, weight=1)
        self.k_site = self._kv(self._card(f, "網站", 0, 0), [("state", "狀態"), ("ms", "回應時間"), ("version", "程式版本"), ("uptime", "已連續執行"), ("url", "網址")])
        self.k_traffic = self._kv(self._card(f, "近 60 分鐘", 0, 1), [("req", "請求數"), ("err", "伺服器錯誤"), ("avg", "平均回應"), ("slow", "最慢的請求"), ("online", "線上人數（15 分鐘內）")])
        self.k_box = self._kv(self._card(f, "容器（需要連線 NAS）", 0, 2), [("status", "狀態"), ("health", "健康檢查"), ("cpu", "CPU"), ("mem", "記憶體"), ("restarts", "重新啟動次數")])
        self.k_data = self._kv(self._card(f, "資料", 1, 0), [("papers", "論文"), ("files", "檔案"), ("ann", "標註"), ("users", "使用者"), ("regs", "待審註冊")])
        self.k_disk = self._kv(self._card(f, "儲存空間", 1, 1), [("free", "NAS 剩餘空間"), ("data", "論文庫資料"), ("db", "資料庫"), ("integrity", "資料庫檢查"), ("backup", "最近備份")])
        self.k_cfg = self._kv(self._card(f, "設定檢查", 1, 2), [("smtp", "寄信（SMTP）"), ("ai", "AI／翻譯"), ("ocr", "OCR"), ("owner", "站長"), ("jobs", "背景工作")])
        chart = self._card(f, "回應時間（這台電腦每次檢查）與每分鐘請求數（伺服器）", 2, 0, colspan=2)
        self.chart = tk.Canvas(chart, height=170, bg="#ffffff", highlightthickness=0)
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda e: self.draw_chart())
        alerts = self._card(f, "提醒", 2, 2)
        self.alerts = tk.Text(alerts, height=8, wrap="word", relief="flat", bg=self.cget("bg"))
        self.alerts.pack(fill="both", expand=True)
        self.alerts.tag_configure("bad", foreground=BAD)
        self.alerts.tag_configure("warn", foreground=WARN)
        self.alerts.tag_configure("ok", foreground=OK)

    # ---------------------------------------------------------------- 分頁：帳號
    def _tab_users(self):
        f = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(f, text="  帳號  ")
        self.tab_users = f
        self.reg_box = ttk.LabelFrame(f, text="註冊申請", style="Card.TLabelframe")
        self.reg_box.pack(fill="x", pady=(0, 8))
        self.reg_tree = ttk.Treeview(self.reg_box, columns=("time", "name", "user", "email", "note"), show="headings", height=3)
        for c, t, w in zip(("time", "name", "user", "email", "note"), ("申請時間", "用戶名稱", "帳號", "Email", "說明"), (110, 140, 120, 220, 380)):
            self.reg_tree.heading(c, text=t); self.reg_tree.column(c, width=w, anchor="w", stretch=c == "note")
        self.reg_tree.pack(side="left", fill="x", expand=True)
        rb = ttk.Frame(self.reg_box); rb.pack(side="left", padx=(8, 0), anchor="n")
        self.reg_role = tk.StringVar(value="成員")
        ttk.Label(rb, text="通過後的角色", style="Muted.TLabel").pack(anchor="w")
        ttk.Combobox(rb, textvariable=self.reg_role, values=("成員", "唯讀訪客"), width=10, state="readonly").pack(fill="x")
        ttk.Button(rb, text="通過", style="Accent.TButton", command=self.reg_approve).pack(fill="x", pady=(4, 2))
        ttk.Button(rb, text="不通過…", command=self.reg_reject).pack(fill="x")
        bar = ttk.Frame(f); bar.pack(fill="x")
        for text, cmd in (("新增帳號…", self.user_add), ("編輯…", self.user_edit), ("重設密碼…", self.user_pw), ("重設 2FA", self.user_2fa),
                          ("停用／啟用", self.user_toggle), ("設為站長", lambda: self.user_owner(True)), ("取消站長", lambda: self.user_owner(False)),
                          ("刪除帳號…", self.user_delete)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)
        ttk.Button(bar, text="重新載入", command=self.load_users).pack(side="right")
        ttk.Button(bar, text="提醒沒填 Email 的人", command=self.remind_email).pack(side="right", padx=4)
        cols = ("id", "username", "name", "email", "role", "perm", "state", "twofa", "created")
        self.utree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, t, w in zip(cols, ("#", "帳號", "名稱", "Email", "身分", "個別調整", "狀態", "2FA", "建立"), (40, 130, 140, 220, 90, 240, 60, 50, 90)):
            self.utree.heading(c, text=t); self.utree.column(c, width=w, anchor="w", stretch=c in ("perm", "email"))
        self.utree.tag_configure("owner", foreground="#92400e")
        self.utree.tag_configure("off", foreground=OFF)
        self.utree.pack(fill="both", expand=True, pady=(6, 0))
        self.utree.bind("<Double-1>", lambda e: self.user_edit())
        self.uinfo = ttk.Label(f, text="", style="Muted.TLabel")
        self.uinfo.pack(fill="x", pady=(4, 0))
        self.users_data, self.site_meta, self.me = [], None, None
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self.tabs.index("current") == self.tabs.index(self.tab_users) and self.load_users(), add="+")

    def load_users(self):
        if not self.need_login():
            return

        def work():
            return self.site.users(), self.site.regs(), self.site.meta(), self.site.me()

        def done(r):
            users, regs, meta, me = r
            self.users_data, self.site_meta, self.me = users, meta, me
            self.utree.delete(*self.utree.get_children())
            P, RD, RN = meta["perm_names"], meta["role_defaults"], meta["role_names"]
            for u in users:
                diff = [k for k in P if u["role"] != "admin" and u["perms"].get(k) != RD[u["role"]][k]]
                ptxt = "、".join(("+" if u["perms"][k] else "−") + P[k].split("（")[0] for k in diff) or "—"
                self.utree.insert("", "end", iid=str(u["id"]), values=(u["id"], u["username"], u["display_name"], u.get("email") or "", "★ 站長" if u.get("owner") else RN.get(u["role"], u["role"]),
                                  ptxt, "停用" if u["disabled"] else "啟用", "✓" if u.get("has_2fa") else "", ago(u.get("created_at"))),
                                  tags=("owner",) if u.get("owner") else ("off",) if u["disabled"] else ())
            self.reg_tree.delete(*self.reg_tree.get_children())
            for g in regs.get("items", []):
                self.reg_tree.insert("", "end", iid=f"r{g['id']}", values=(ago(g["created_at"]), g["display_name"], g["username"], g["email"], g.get("note") or ""))
            self.reg_box.configure(text=f"註冊申請（{len(regs.get('items', []))}）" if regs.get("can_review") else "註冊申請（只有站長可以審核）")
            owners = [u["display_name"] for u in users if u.get("owner")]
            self.uinfo.configure(text=f"共 {len(users)} 個帳號　·　站長：{'、'.join(owners) or '還沒有（管理員可以指定一位）'}　·　你登入的是 {me['display_name']}（{'站長' if me.get('owner') else RN.get(me['role'])}）")
        self.bg(work, done)

    def sel_user(self):
        s = self.utree.selection()
        if not s:
            messagebox.showinfo("帳號", "請先在列表點選一個帳號", parent=self)
            return None
        return next((u for u in self.users_data if str(u["id"]) == s[0]), None)

    def _user_call(self, fn, ok_msg):
        self.bg(fn, lambda r: (self.foot.configure(text=ok_msg), self.load_users()))

    def user_add(self):
        self._user_form(None)

    def user_edit(self):
        u = self.sel_user()
        if u:
            self._user_form(u)

    def _user_form(self, u):
        meta = self.site_meta or self.site.meta()
        P, RD, RN = meta["perm_names"], meta["role_defaults"], meta["role_names"]
        d = tk.Toplevel(self); d.title("新增帳號" if u is None else f"編輯帳號：{u['display_name']}"); d.transient(self); d.grab_set(); d.resizable(False, False)
        fr = ttk.Frame(d, padding=16); fr.pack()
        v = {k: tk.StringVar(value=(u or {}).get(k2, "") or "") for k, k2 in (("username", "username"), ("name", "display_name"), ("email", "email"))}
        pw = tk.StringVar()
        code_of = {v: k for k, v in RN.items()}
        role = tk.StringVar(value=(u or {}).get("role", "member"))
        role_disp = tk.StringVar(value=RN.get(role.get(), role.get()))
        dis = tk.BooleanVar(value=bool((u or {}).get("disabled")))
        rows = [("帳號（登入用）", v["username"], ""), ("名稱", v["name"], ""), ("Email", v["email"], "")]
        if u is None:
            rows.append(("初始密碼（至少 6 字元）", pw, "•"))
        for i, (lab, var, show) in enumerate(rows):
            ttk.Label(fr, text=lab).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Entry(fr, textvariable=var, width=34, show=show).grid(row=i, column=1, sticky="w", pady=3)
        r0 = len(rows)
        ttk.Label(fr, text="角色").grid(row=r0, column=0, sticky="w", pady=3)
        rc = ttk.Combobox(fr, textvariable=role_disp, values=[RN.get(k, k) for k in RD], state="readonly" if not (u or {}).get("owner") else "disabled", width=12)
        rc.grid(row=r0, column=1, sticky="w")
        pf = ttk.LabelFrame(fr, text="權限（切換角色會先套用預設）", padding=8)
        pf.grid(row=r0 + 1, column=0, columnspan=2, sticky="ew", pady=6)
        boxes = {}
        cur = dict((u or {}).get("perms") or RD[role.get()])
        for i, (k, lab) in enumerate(P.items()):
            boxes[k] = tk.BooleanVar(value=bool(cur.get(k)))
            ttk.Checkbutton(pf, text=lab, variable=boxes[k]).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 16))

        def on_role(*_):
            role.set(code_of.get(role_disp.get(), role_disp.get()))
            for k in P:
                boxes[k].set(bool(RD[role.get()][k]))
            state = "disabled" if role.get() == "admin" else "normal"
            for w in pf.winfo_children():
                w.configure(state=state)
        rc.bind("<<ComboboxSelected>>", on_role)
        if role.get() == "admin":
            for w in pf.winfo_children():
                w.configure(state="disabled")
        if u is not None:
            ttk.Checkbutton(fr, text="停用這個帳號（不能登入，資料保留）", variable=dis).grid(row=r0 + 2, column=1, sticky="w")
            if u.get("owner"):
                ttk.Label(fr, text="站長一定是管理員；要改角色請先取消站長身分。", style="Muted.TLabel").grid(row=r0 + 3, column=0, columnspan=2, sticky="w")
        msg = ttk.Label(fr, text="", foreground=BAD); msg.grid(row=r0 + 4, column=0, columnspan=2, sticky="w")

        def save():
            perms = {k: b.get() for k, b in boxes.items()}
            if u is None:
                data = {"username": v["username"].get().strip(), "display_name": v["name"].get().strip(), "password": pw.get(), "role": role.get(), "perms": perms}

                def work():
                    self.site.add_user(data)
                    if v["email"].get().strip():
                        nu = next(x for x in self.site.users() if x["username"] == data["username"])
                        self.site.edit_user(nu["id"], {"email": v["email"].get().strip()})
            else:
                data = {}
                if v["username"].get().strip() != u["username"]: data["username"] = v["username"].get().strip()
                if v["name"].get().strip() != u["display_name"]: data["display_name"] = v["name"].get().strip()
                if v["email"].get().strip() != (u.get("email") or ""): data["email"] = v["email"].get().strip()
                if not u.get("owner") and (role.get() != u["role"] or perms != u["perms"]): data.update(role=role.get(), perms=perms)
                if dis.get() != bool(u["disabled"]): data["disabled"] = dis.get()
                if not data:
                    d.destroy(); return

                def work():
                    self.site.edit_user(u["id"], data)
            self.bg(work, lambda r: (d.destroy(), self.foot.configure(text="已儲存帳號"), self.load_users()), lambda m: msg.configure(text=m))
        ttk.Button(fr, text="建立" if u is None else "儲存", style="Accent.TButton", command=save).grid(row=r0 + 5, column=1, sticky="e", pady=(8, 0))

    def user_pw(self):
        u = self.sel_user()
        if not u:
            return
        pw = simpledialog.askstring("重設密碼", f"{u['display_name']}（{u['username']}）的新密碼（至少 6 字元）：\n重設後他在所有裝置都會被登出。", show="•", parent=self)
        if pw:
            self._user_call(lambda: self.site.edit_user(u["id"], {"password": pw}), f"已重設 {u['display_name']} 的密碼")

    def user_2fa(self):
        u = self.sel_user()
        if u and messagebox.askyesno("重設 2FA", f"關閉 {u['display_name']} 的兩步驟驗證？（例如他換手機、驗證 App 不見了）", parent=self):
            self._user_call(lambda: self.site.edit_user(u["id"], {"reset_2fa": True}), "已關閉兩步驟驗證")

    def user_toggle(self):
        u = self.sel_user()
        if u and messagebox.askyesno("停用／啟用", f"{'啟用' if u['disabled'] else '停用'} {u['display_name']}？" + ("" if u["disabled"] else "\n停用後他不能登入，資料都保留。"), parent=self):
            self._user_call(lambda: self.site.edit_user(u["id"], {"disabled": not u["disabled"]}), "已更新")

    def user_owner(self, on):
        u = self.sel_user()
        if not u:
            return
        text = (f"把 {u['display_name']} 設為站長？\n站長擁有管理員的全部權限並負責審核註冊；有站長之後，只有站長能變更站長身分。" if on
                else f"取消 {u['display_name']} 的站長身分？（他仍然是管理員）")
        if messagebox.askyesno("站長", text, parent=self):
            self._user_call(lambda: self.site.edit_user(u["id"], {"owner": on}), "已更新站長身分")

    def user_delete(self):
        u = self.sel_user()
        if not u:
            return
        d = tk.Toplevel(self); d.title("刪除帳號"); d.transient(self); d.grab_set(); d.resizable(False, False)
        fr = ttk.Frame(d, padding=16); fr.pack()
        ttk.Label(fr, text=f"刪除 {u['display_name']}（{u['username']}）", style="Big.TLabel").pack(anchor="w")
        ttk.Label(fr, text="會一起刪除：登入、閱讀狀態、被指派的論文、通知、入門路徑進度、筆記頁設定、私人筆記。\n"
                           "他上傳的論文與檔案會保留。這個動作不能復原（可以先用「停用」代替）。", justify="left").pack(anchor="w", pady=(6, 6))
        purge = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr, text="連同他公開的標註、手寫、筆記頁與討論回覆一起刪除\n（不勾：這些內容保留，顯示為「已刪除的帳號」）", variable=purge).pack(anchor="w")
        ttk.Label(fr, text=f"請輸入帳號「{u['username']}」確認：").pack(anchor="w", pady=(10, 2))
        conf = tk.StringVar(); ttk.Entry(fr, textvariable=conf, width=30).pack(anchor="w")
        msg = ttk.Label(fr, text="", foreground=BAD); msg.pack(anchor="w")

        def go():
            if conf.get().strip() != u["username"]:
                msg.configure(text="輸入的帳號不對"); return
            self.bg(lambda: self.site.delete_user(u["id"], purge.get()),
                    lambda r: (d.destroy(), self.foot.configure(text=f"已刪除 {u['display_name']}（一起刪掉 {r.get('removed_annotations', 0)} 則標註）"), self.load_users()),
                    lambda m: msg.configure(text=m))
        ttk.Button(fr, text="刪除帳號", command=go).pack(anchor="e", pady=(10, 0))

    def remind_email(self):
        if not self.need_login():
            return
        missing = [u["display_name"] for u in self.users_data if not u["disabled"] and not (u.get("email") or "").strip()]
        if not missing:
            return messagebox.showinfo("提醒填 Email", "所有帳號都已經填了 Email。", parent=self)
        if messagebox.askyesno("提醒填 Email", f"{len(missing)} 個帳號還沒填 Email：\n{'、'.join(missing)}\n\n送站內通知提醒他們？已經登入的人下次操作網站時會跳出填寫視窗。", parent=self):
            self.bg(self.site.remind_email, lambda r: messagebox.showinfo("提醒填 Email", f"已通知 {r['notified']} 人。", parent=self))

    def reg_approve(self):
        s = self.reg_tree.selection()
        if not s:
            return messagebox.showinfo("註冊申請", "請先點選一筆申請", parent=self)
        rid = int(s[0][1:])
        role = "viewer" if self.reg_role.get() == "唯讀訪客" else "member"
        self._user_call(lambda: self.site.approve(rid, role), "已通過，帳號已開通")

    def reg_reject(self):
        s = self.reg_tree.selection()
        if not s:
            return messagebox.showinfo("註冊申請", "請先點選一筆申請", parent=self)
        rid = int(s[0][1:])
        reason = simpledialog.askstring("不通過", "可以寫原因（會寄信告訴他；留空就不寄信）：", parent=self)
        if reason is None:
            return
        self._user_call(lambda: self.site.reject(rid, reason, bool(reason.strip())), "已刪除這筆申請")

    # ---------------------------------------------------------------- 分頁：背景工作
    def _tab_jobs(self):
        f = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(f, text="  背景工作與維護  ")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Label(bar, text="立即執行：").pack(side="left")
        for k, label in JOBS:
            ttk.Button(bar, text=label, command=lambda k=k, l=label: self.run_job(k, l)).pack(side="left", padx=2)
        bar2 = ttk.Frame(f)
        bar2.pack(fill="x", pady=(6, 8))
        ttk.Label(bar2, text="維護：").pack(side="left")
        ttk.Button(bar2, text="備份資料庫", command=self.do_backup).pack(side="left", padx=2)
        ttk.Button(bar2, text="備份並下載到這台電腦", command=lambda: self.do_backup(download=True)).pack(side="left", padx=2)
        ttk.Button(bar2, text="重建全文索引", command=self.do_reindex).pack(side="left", padx=2)
        ttk.Button(bar2, text="OCR 所有掃描檔", command=self.do_ocr).pack(side="left", padx=2)
        cols = ("id", "label", "state", "progress", "result", "created", "finished")
        self.jobs = ttk.Treeview(f, columns=cols, show="headings", height=18)
        for c, t, w in zip(cols, ("#", "工作", "狀態", "進度", "結果", "建立", "結束"), (50, 190, 70, 140, 360, 110, 110)):
            self.jobs.heading(c, text=t)
            self.jobs.column(c, width=w, anchor="w", stretch=c == "result")
        self.jobs.tag_configure("failed", foreground=BAD)
        self.jobs.tag_configure("running", foreground="#2563eb")
        self.jobs.pack(fill="both", expand=True)

    # ---------------------------------------------------------------- 分頁：日誌
    def _tab_logs(self):
        f = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(f, text="  日誌  ")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        self.log_src = tk.StringVar(value="site")
        ttk.Radiobutton(bar, text="網站的錯誤與警告", value="site", variable=self.log_src, command=self.load_logs).pack(side="left")
        ttk.Radiobutton(bar, text="容器日誌（最後 400 行，需要連線 NAS）", value="docker", variable=self.log_src, command=self.load_logs).pack(side="left", padx=12)
        ttk.Button(bar, text="重新載入", command=self.load_logs).pack(side="right")
        self.logtext = self._textbox(f)

    def _textbox(self, parent):
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True, pady=(6, 0))
        t = tk.Text(box, wrap="none", font=("Consolas" if core.platform_name() == "windows" else "Menlo", 10), bg="#0f172a", fg="#e2e8f0",
                    insertbackground="#e2e8f0", relief="flat", padx=8, pady=6)
        ys = ttk.Scrollbar(box, orient="vertical", command=t.yview)
        xs = ttk.Scrollbar(box, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        t.grid(row=0, column=0, sticky="nsew"); ys.grid(row=0, column=1, sticky="ns"); xs.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1); box.columnconfigure(0, weight=1)
        t.tag_configure("err", foreground="#fca5a5")
        t.tag_configure("warn", foreground="#fcd34d")
        t.tag_configure("ok", foreground="#86efac")
        t.tag_configure("head", foreground="#93c5fd")
        return t

    # ---------------------------------------------------------------- 分頁：更新與重啟
    def _tab_update(self):
        f = ttk.Frame(self.tabs, padding=8)
        self.tab_update = f
        self.tabs.add(f, text="  更新與重啟  ")
        src = ttk.LabelFrame(f, text="要安裝的新版", style="Card.TLabelframe")
        src.pack(fill="x")
        self.src_path = tk.StringVar(value=str(HERE_ROOT) if HERE_ROOT else self.cfg.get("last_zip", ""))
        ttk.Entry(src, textvariable=self.src_path).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(src, text="選擇新版 zip…", command=self.pick_zip).grid(row=0, column=1, padx=2)
        ttk.Button(src, text="選擇資料夾…", command=self.pick_dir).grid(row=0, column=2, padx=2)
        src.columnconfigure(0, weight=1)
        self.src_info = ttk.Label(src, text="", style="Muted.TLabel")
        self.src_info.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.src_path.trace_add("write", lambda *_: self.check_source())
        act = ttk.Frame(f)
        act.pack(fill="x", pady=8)
        self.upd_btn = ttk.Button(act, text="更新網站", style="Accent.TButton", command=lambda: self.do_update(False))
        self.upd_btn.pack(side="left")
        ttk.Button(act, text="完全重建（不用快取，約 5–10 分鐘）", command=lambda: self.do_update(True)).pack(side="left", padx=6)
        ttk.Separator(act, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(act, text="重新啟動網站", command=lambda: self.box_action("restart")).pack(side="left", padx=2)
        ttk.Button(act, text="停止", command=lambda: self.box_action("stop")).pack(side="left", padx=2)
        ttk.Button(act, text="啟動", command=lambda: self.box_action("start")).pack(side="left", padx=2)
        ttk.Label(f, text="更新會：上傳這份程式 → 停止舊容器 → 備份資料庫 → 換上新程式 → 重建 → 啟動 → 確認版本。data/ 與 import/ 不會被動到。",
                  style="Muted.TLabel", wraplength=1000).pack(fill="x")
        self.out = self._textbox(f)
        self.check_source()

    # ---------------------------------------------------------------- 執行緒與畫面更新
    def _pump(self):
        try:
            while True:
                fn = self.q.get_nowait()
                fn()
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def ui(self, fn):
        self.q.put(fn)

    def bg(self, fn, done=None, err=None):
        def run():
            try:
                r = fn()
                if done:
                    self.ui(lambda: done(r))
            except Exception as e:  # noqa: BLE001
                msg = str(e) or e.__class__.__name__
                self.ui(lambda: (err or self.show_err)(msg))
        threading.Thread(target=run, daemon=True).start()

    def show_err(self, msg):
        messagebox.showerror("控制台", msg, parent=self)

    # ---------------------------------------------------------------- 定期檢查
    def refresh(self):
        if getattr(self, "_refreshing", False):
            return
        self._refreshing = True

        def work():
            ok, ms, site, err = self.site.ping()
            st = None
            st_err = ""
            if ok and site and site.get("user") is None and self.site.cookie:
                self.site.cookie = ""      # 登入已過期
            if ok:
                try:
                    st = self.site.status()
                except core.ApiError as e:
                    st_err = str(e) if e.status != 401 else "未登入"
            box = None
            if self.nas and self.nas_pw:
                try:
                    box = self.nas.container_state()
                except Exception as e:  # noqa: BLE001
                    box = {"error": str(e)}
            return ok, ms, site, err, st, st_err, box

        def done(r):
            self._refreshing = False
            self.apply(*r)

        def fail(msg):
            self._refreshing = False
            self.foot.configure(text=f"檢查失敗：{msg}")
        self.bg(work, done, fail)

    def _schedule(self):
        self.after(max(10, int(self.cfg.get("interval") or 30)) * 1000, self._tick)

    def _tick(self):
        self.refresh()
        self._schedule()

    def apply(self, ok, ms, site, err, st, st_err, box):
        now = time.time()
        self.history.append((now, ok, ms))
        self.history = self.history[-240:]
        self.status = st
        self.state_container = box
        v = self.k_site
        if ok:
            if self.down_since:
                self.notify(f"網站恢復正常（中斷約 {core.fmt_dur(now - self.down_since)}）", good=True)
            self.down_since = None
            slow = ms > 2000
            self.dot.itemconfigure(self.dot_id, fill=WARN if slow else OK)
            self.head.configure(text=f"{site.get('name', '論文庫')} 正常運作" if not slow else f"{site.get('name', '論文庫')} 回應偏慢")
            v["state"].configure(text="正常", foreground=OK)
            v["ms"].configure(text=f"{ms:.0f} ms", foreground=WARN if slow else "")
            v["version"].configure(text=site.get("version") or "（舊版）")
        else:
            if not self.down_since:
                self.down_since = now
                self.notify(f"網站沒有回應：{err}")
            self.dot.itemconfigure(self.dot_id, fill=BAD)
            self.head.configure(text="網站沒有回應")
            v["state"].configure(text=f"離線（{ago(datetime.fromtimestamp(self.down_since, timezone.utc).isoformat())}開始）", foreground=BAD)
            v["ms"].configure(text=err[:60], foreground=BAD)
        v["url"].configure(text=self.cfg["site_url"])
        logged = bool(st)
        self.login_btn.configure(text="已登入網站 ✓" if logged else "登入網站")
        self.nas_btn.configure(text="NAS 已連線 ✓" if self.nas and self.nas.ok() else "連線 NAS")
        self.sub.configure(text=("" if logged else "（登入管理員帳號才看得到詳細狀態）" if ok else "") + f"　上次檢查 {datetime.now():%H:%M:%S}")
        if st:
            v["uptime"].configure(text=core.fmt_dur(st["uptime_s"]))
            tr = st["traffic"]
            self.k_traffic["req"].configure(text=str(tr["requests"]))
            self.k_traffic["err"].configure(text=str(tr["errors"]), foreground=BAD if tr["errors"] else "")
            self.k_traffic["avg"].configure(text=f"{tr['avg_ms']} ms" if tr["avg_ms"] is not None else "—")
            self.k_traffic["slow"].configure(text=f"{tr['slowest']['ms']:.0f} ms　{tr['slowest']['path'][:40]}" if tr["slowest"]["path"] else "—")
            self.k_traffic["online"].configure(text=str(tr["online"]))
            c = st["counts"]
            self.k_data["papers"].configure(text=f"{c['papers']}（近 7 天 +{c['papers_7d']}）")
            self.k_data["files"].configure(text=str(c["files"]))
            self.k_data["ann"].configure(text=str(c["annotations"]))
            self.k_data["users"].configure(text=f"{c['users']}（登入中的裝置 {c['sessions']}）")
            self.k_data["regs"].configure(text=str(c["registrations"]), foreground=WARN if c["registrations"] else "")
            d = st["disk"]
            freep = d["free"] / d["total"] * 100 if d["total"] else 0
            self.k_disk["free"].configure(text=f"{core.fmt_bytes(d['free'])}（{freep:.0f}%）", foreground=BAD if freep < 5 else WARN if freep < 12 else "")
            self.k_disk["data"].configure(text=f"{core.fmt_bytes(d['data_bytes'])}（PDF {core.fmt_bytes(d['pdf_bytes'])}）")
            self.k_disk["db"].configure(text=core.fmt_bytes(st["db"]["size"]))
            self.k_disk["integrity"].configure(text="正常" if st["db"]["integrity"] == "ok" else st["db"]["integrity"], foreground="" if st["db"]["integrity"] == "ok" else BAD)
            self.k_disk["backup"].configure(text=ago(st["backup"]["last_at"]) if st["backup"]["last_at"] else "從來沒有備份", foreground=self._backup_color(st))
            cf = st["config"]
            yn = lambda b: ("✓ 已設定", OK) if b else ("✗ 未設定", WARN)  # noqa: E731
            for key, val in (("smtp", cf["smtp"]), ("ocr", cf["ocr"]), ("owner", cf["owner"])):
                t, col = yn(val); self.k_cfg[key].configure(text=t, foreground=col)
            self.k_cfg["ai"].configure(text=f"AI {'✓' if cf['ai'] else '✗'}　翻譯 {'✓' if cf['translate'] else '✗'}")
            j = st["jobs"]
            self.k_cfg["jobs"].configure(text=f"排隊 {j['queued']}　執行中 {j['running']}　失敗（24h）{j['failed_24h']}", foreground=BAD if j["failed_24h"] else "")
            self.fill_jobs(j["recent"])
        if box:
            if box.get("error"):
                self.k_box["status"].configure(text=box["error"][:60], foreground=BAD)
            elif not box.get("exists"):
                self.k_box["status"].configure(text="找不到容器（沒有在執行）", foreground=BAD)
            else:
                self.k_box["status"].configure(text=f"{box['status']}（{ago(box['started'])}啟動）", foreground=OK if box["status"] == "running" else BAD)
                hl = box.get("health") or "—"
                self.k_box["health"].configure(text={"healthy": "健康", "unhealthy": "不健康", "starting": "啟動中"}.get(hl, hl),
                                               foreground=OK if hl == "healthy" else BAD if hl == "unhealthy" else "")
                self.k_box["cpu"].configure(text=box.get("cpu") or "—")
                self.k_box["mem"].configure(text=f"{box.get('mem') or '—'}")
                self.k_box["restarts"].configure(text=box.get("restarts") or "0", foreground=WARN if (box.get("restarts") or "0") not in ("0", "") else "")
        self.draw_chart()
        self.draw_alerts(ok, ms, st, st_err, box)
        self.foot.configure(text=f"每 {self.cfg.get('interval', 30)} 秒自動檢查　·　設定檔：{core.CONFIG}")

    def _backup_color(self, st):
        at = st["backup"]["last_at"]
        if not at:
            return WARN
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(at)).days
        return BAD if age > 30 else WARN if age > 7 else ""

    def draw_alerts(self, ok, ms, st, st_err, box):
        a = []
        if not ok:
            a.append(("bad", "網站沒有回應。可以到「更新與重啟」按「重新啟動網站」，或看「日誌 → 容器日誌」找原因。"))
        elif ms > 2000:
            a.append(("warn", f"回應偏慢（{ms:.0f} ms）。可能正在建立索引、OCR 或 NAS 忙碌。"))
        if ok and not st:
            a.append(("warn", "還沒登入網站：按右上「登入網站」用管理員或站長帳號登入，才看得到詳細狀態。" if st_err in ("", "未登入") else f"讀取狀態失敗：{st_err}"))
        if st:
            d = st["disk"]
            if d["total"] and d["free"] / d["total"] < 0.12:
                a.append(("bad" if d["free"] / d["total"] < 0.05 else "warn", f"NAS 剩餘空間只剩 {core.fmt_bytes(d['free'])}。"))
            if st["db"]["integrity"] != "ok":
                a.append(("bad", f"資料庫檢查異常：{st['db']['integrity']}。請先備份並聯絡維護的人。"))
            if not st["backup"]["last_at"]:
                a.append(("warn", "還沒有任何資料庫備份。到「背景工作與維護」按「備份並下載到這台電腦」。"))
            elif (datetime.now(timezone.utc) - datetime.fromisoformat(st["backup"]["last_at"])).days > 7:
                a.append(("warn", f"最近一次備份是 {ago(st['backup']['last_at'])}，建議備份一次。"))
            if st["jobs"]["failed_24h"]:
                a.append(("warn", f"過去 24 小時有 {st['jobs']['failed_24h']} 個背景工作失敗，看「背景工作與維護」。"))
            if st["traffic"]["errors"]:
                a.append(("warn", f"近 60 分鐘有 {st['traffic']['errors']} 次伺服器錯誤，看「日誌」。"))
            if st["counts"]["registrations"]:
                a.append(("warn", f"有 {st['counts']['registrations']} 筆註冊申請等待審核（網站「管理 → 使用者與權限」）。"))
            if not st["config"]["owner"]:
                a.append(("warn", "網站還沒有站長。到「管理 → 使用者與權限」指定一位。"))
            if not st["config"]["smtp"]:
                a.append(("warn", "還沒設定寄信（SMTP），通知信、註冊審核信寄不出去。"))
            local = core.read_version(Path(self.src_path.get())) if Path(self.src_path.get()).is_dir() else ""
            if local and local != st["version"]:
                a.append(("warn", f"這份程式是 {local}，網站目前是 {st['version']}。到「更新與重啟」可以更新。"))
        if box and box.get("exists") and box.get("health") == "unhealthy":
            a.append(("bad", "容器健康檢查失敗，建議重新啟動網站。"))
        self.alerts.configure(state="normal")
        self.alerts.delete("1.0", "end")
        for tag, text in a or [("ok", "一切正常 ✓")]:
            self.alerts.insert("end", ("⚠ " if tag != "ok" else "") + text + "\n", tag)
        self.alerts.configure(state="disabled")

    def draw_chart(self):
        c = self.chart
        c.delete("all")
        w, h = max(c.winfo_width(), 200), max(c.winfo_height(), 100)
        pad = 34
        c.create_line(pad, h - 20, w - 8, h - 20, fill="#e5e7eb")
        # 伺服器每分鐘請求數（長條）
        series = (self.status or {}).get("traffic", {}).get("series", []) if self.status else []
        if series:
            mx = max(1, max(s["n"] for s in series))
            bw = (w - pad - 8) / len(series)
            for i, s in enumerate(series):
                if s["n"]:
                    x = pad + i * bw
                    y = (h - 20) - (s["n"] / mx) * (h - 40)
                    c.create_rectangle(x + 1, y, x + bw - 1, h - 20, fill="#fecaca" if s["err"] else "#dbeafe", outline="")
            c.create_text(w - 10, 10, text=f"長條：每分鐘請求（最多 {mx}）", anchor="ne", fill="#6b7280", font=(self.base_font[0], 8))
        # 回應時間（折線）
        pts = self.history[-120:]
        if len(pts) >= 2:
            mx = max(200, max(p[2] for p in pts))
            t0, t1 = pts[0][0], max(pts[-1][0], pts[0][0] + 1)
            xy = []
            for t, ok, ms in pts:
                x = pad + (t - t0) / (t1 - t0) * (w - pad - 8)
                y = (h - 20) - min(ms, mx) / mx * (h - 40)
                xy += [x, y]
                if not ok:
                    c.create_line(x, 10, x, h - 20, fill="#fca5a5", width=2)
            c.create_line(*xy, fill="#2563eb", width=2, smooth=True)
            c.create_text(4, 12, text=f"{mx:.0f}ms", anchor="w", fill="#6b7280", font=(self.base_font[0], 8))
            c.create_text(4, h - 20, text="0", anchor="w", fill="#6b7280", font=(self.base_font[0], 8))
            c.create_text(pad, h - 6, text=datetime.fromtimestamp(t0).strftime("%H:%M"), anchor="w", fill="#6b7280", font=(self.base_font[0], 8))
            c.create_text(w - 8, h - 6, text=datetime.fromtimestamp(t1).strftime("%H:%M"), anchor="e", fill="#6b7280", font=(self.base_font[0], 8))
        elif not series:
            c.create_text(w / 2, h / 2, text="收集資料中…", fill="#9ca3af")

    def notify(self, text, good=False):
        """網站掛掉或恢復時提醒：狀態列、視窗閃爍、嗶一聲；可在設定關掉跳出視窗。"""
        self.foot.configure(text=text)
        try:
            self.bell()
            if self.cfg.get("alert", True) and not good:
                self.deiconify(); self.lift(); self.attributes("-topmost", True); self.after(1500, lambda: self.attributes("-topmost", False))
                messagebox.showwarning("論文庫控制台", text, parent=self)
        except tk.TclError:
            pass

    def fill_jobs(self, rows):
        self.jobs.delete(*self.jobs.get_children())
        st_name = {"queued": "排隊中", "running": "執行中", "done": "完成", "failed": "失敗"}
        for j in rows:
            self.jobs.insert("", "end", values=(j["id"], j["label"], st_name.get(j["state"], j["state"]), (j.get("progress") or "")[:60],
                                                (j.get("result") or "").replace("\n", " ")[:200], ago(j["created_at"]), ago(j.get("finished_at"))),
                             tags=(j["state"],))

    # ---------------------------------------------------------------- 登入
    def need_login(self) -> bool:
        if self.status:
            return True
        self.login_dialog()
        return bool(self.status)

    def login_dialog(self):
        d = tk.Toplevel(self); d.title("登入網站"); d.transient(self); d.grab_set(); d.resizable(False, False)
        fr = ttk.Frame(d, padding=16); fr.pack()
        ttk.Label(fr, text="用管理員或站長帳號登入，才看得到詳細狀態與執行維護。", style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        url = tk.StringVar(value=self.cfg["site_url"]); user = tk.StringVar(value=self.cfg.get("username", "")); pw = tk.StringVar(); code = tk.StringVar()
        rem = tk.BooleanVar(value=bool(self.cfg.get("remember")))
        for i, (lab, var, show) in enumerate((("網站網址", url, ""), ("帳號", user, ""), ("密碼", pw, "•"), ("兩步驟驗證碼（有開才填）", code, ""))):
            ttk.Label(fr, text=lab).grid(row=i + 1, column=0, sticky="w", pady=3)
            e = ttk.Entry(fr, textvariable=var, width=36, show=show); e.grid(row=i + 1, column=1, pady=3)
            if i == (2 if user.get() else 1):
                e.focus_set()
        ttk.Checkbutton(fr, text="記住登入（下次開控制台不用再輸入）", variable=rem).grid(row=5, column=1, sticky="w", pady=4)
        msg = ttk.Label(fr, text="", foreground=BAD); msg.grid(row=6, column=0, columnspan=2, sticky="w")

        def go(*_):
            msg.configure(text="登入中…")
            self.cfg["site_url"] = url.get().strip().rstrip("/")
            self.site = core.Site(self.cfg["site_url"])

            def work():
                return self.site.login(user.get().strip(), pw.get(), code.get().strip())

            def done(r):
                if r == "2fa":
                    msg.configure(text="這個帳號有開兩步驟驗證，請填驗證碼後再按登入"); return
                self.cfg["username"] = user.get().strip(); self.cfg["remember"] = rem.get(); self.cfg["cookie"] = self.site.cookie
                core.save_config(self.cfg); d.destroy(); self.refresh()
            self.bg(work, done, lambda m: msg.configure(text=m))
        ttk.Button(fr, text="登入", style="Accent.TButton", command=go).grid(row=7, column=1, sticky="e", pady=(8, 0))
        d.bind("<Return>", go)

    # ---------------------------------------------------------------- NAS
    def need_nas(self) -> bool:
        if self.nas and self.nas.ok():
            return True
        self.nas_dialog()
        return bool(self.nas and self.nas.ok())

    def nas_dialog(self):
        if core.paramiko is None:
            messagebox.showwarning("連線 NAS", "這台電腦沒有安裝 paramiko，無法連線 NAS。\n請關掉控制台，重新用 console-windows.bat 開啟（會自動安裝）。", parent=self)
            return
        d = tk.Toplevel(self); d.title("連線 NAS（SSH）"); d.transient(self); d.grab_set(); d.resizable(False, False)
        fr = ttk.Frame(d, padding=16); fr.pack()
        ttk.Label(fr, text="用來看容器狀態與日誌、重新啟動與更新網站。\nDSM 要先開啟 SSH（控制台 → 終端機和 SNMP），帳號要在 administrators 群組。",
                  style="Muted.TLabel", justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        host = tk.StringVar(value=self.cfg.get("nas_host") or self.cfg["site_url"].split("//")[-1].split(":")[0].split("/")[0])
        port = tk.StringVar(value=str(self.cfg.get("nas_port") or 22)); user = tk.StringVar(value=self.cfg.get("nas_user", ""))
        pw = tk.StringVar(); path = tk.StringVar(value=self.cfg.get("nas_path") or "/volume1/docker/paperlib")
        rows = (("NAS 的 IP 或名稱", host, ""), ("SSH 埠", port, ""), ("DSM 管理員帳號", user, ""), ("DSM 密碼", pw, "•"), ("NAS 上的專案資料夾", path, ""))
        for i, (lab, var, show) in enumerate(rows):
            ttk.Label(fr, text=lab).grid(row=i + 1, column=0, sticky="w", pady=3)
            e = ttk.Entry(fr, textvariable=var, width=36, show=show); e.grid(row=i + 1, column=1, pady=3)
            if var is (pw if user.get() else user):
                e.focus_set()
        ttk.Label(fr, text="密碼只留在這次開啟的控制台裡，不會存到電腦上。", style="Muted.TLabel").grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))
        msg = ttk.Label(fr, text="", foreground=BAD); msg.grid(row=7, column=0, columnspan=2, sticky="w")

        def go(*_):
            msg.configure(text="連線中…")
            cfg = {"nas_host": host.get().strip(), "nas_port": int(port.get() or 22), "nas_user": user.get().strip(), "nas_path": path.get().strip().rstrip("/")}

            def work():
                n = core.Nas(cfg["nas_host"], cfg["nas_port"], cfg["nas_user"], pw.get(), cfg["nas_path"], self.cfg.get("container", "paperlib")).connect()
                code, out = n.run(f"test -f {core.shq(cfg['nas_path'] + '/docker-compose.yml')} && echo FOUND; id -Gn", timeout=20)
                n.run("true", sudo=True, timeout=20)            # 確認 sudo 可以用
                return n, "FOUND" in out

            def done(r):
                n, found = r
                self.nas, self.nas_pw = n, pw.get()
                self.cfg.update(cfg); core.save_config(self.cfg); d.destroy()
                if not found:
                    messagebox.showwarning("連線 NAS", f"已連線，但在 {cfg['nas_path']} 找不到 docker-compose.yml，請確認專案資料夾位置。", parent=self)
                self.refresh()
            self.bg(work, done, lambda m: msg.configure(text=m))
        ttk.Button(fr, text="連線", style="Accent.TButton", command=go).grid(row=8, column=1, sticky="e", pady=(8, 0))
        d.bind("<Return>", go)

    # ---------------------------------------------------------------- 背景工作與維護
    def run_job(self, kind, label):
        if not self.need_login():
            return
        self.bg(lambda: self.site.run_job(kind), lambda r: (self.foot.configure(text=f"已排入：{label}（#{r['job']}）"), self.refresh()))

    def do_backup(self, download=False):
        if not self.need_login():
            return
        dest = None
        if download:
            dest = filedialog.asksaveasfilename(parent=self, title="備份存到哪裡", defaultextension=".db",
                                                initialfile=f"paperlib-{datetime.now():%Y%m%d-%H%M}.db", filetypes=[("SQLite 資料庫", "*.db")])
            if not dest:
                return
        self.foot.configure(text="備份中…")

        def work():
            r = self.site.backup()
            if dest:
                self.site.download_backup(r["file"], Path(dest))
            return r

        def done(r):
            self.foot.configure(text=f"已備份：{r['file']}" + (f"，並存到 {dest}" if dest else "（在 NAS 的 data/backups/）"))
            messagebox.showinfo("備份", f"已備份 {r['file']}" + (f"\n已下載到：{dest}\n\n（PDF 檔案在 NAS 的 data/files/，請另外用 Hyper Backup 等備份整個 data 資料夾）" if dest else ""), parent=self)
            self.refresh()
        self.bg(work, done)

    def do_reindex(self):
        if self.need_login() and messagebox.askyesno("重建全文索引", "重新讀取每篇 PDF 的文字並重建搜尋索引，論文多時要幾分鐘。要開始嗎？", parent=self):
            self.foot.configure(text="重建全文索引中…（完成前請不要關閉）")
            self.bg(self.site.reindex, lambda r: (self.foot.configure(text=f"已重建 {r['papers']} 篇的全文索引"), self.refresh()))

    def do_ocr(self):
        if self.need_login() and messagebox.askyesno("OCR", "對所有沒有文字層的掃描檔做文字辨識（背景執行，可能很久）。要開始嗎？", parent=self):
            self.bg(self.site.ocr_all, lambda r: (self.foot.configure(text="已排入 OCR 工作"), self.refresh()))

    # ---------------------------------------------------------------- 日誌
    def load_logs(self):
        t = self.logtext
        t.delete("1.0", "end")
        t.insert("end", "載入中…\n")
        if self.log_src.get() == "site":
            if not self.need_login():
                return

            def done(rows):
                t.delete("1.0", "end")
                if not rows:
                    t.insert("end", "沒有錯誤或警告（網站重新啟動後重新累計）。\n", "ok")
                for r in rows:
                    t.insert("end", f"[{r['t']}] {r['level']:<7} {r['src']}\n", "err" if r["level"] in ("ERROR", "CRITICAL") else "warn")
                    t.insert("end", r["msg"].rstrip() + "\n\n")
            self.bg(self.site.logs, done)
        else:
            if not self.need_nas():
                return

            def done(text):
                t.delete("1.0", "end")
                for line in text.splitlines():
                    tag = "err" if any(k in line for k in ("ERROR", "Traceback", "Exception", " 500 ")) else "warn" if "WARNING" in line else "head" if "啟動" in line else None
                    t.insert("end", line + "\n", tag)
                t.see("end")
            self.bg(lambda: self.nas.logs(400), done)

    # ---------------------------------------------------------------- 更新與重啟
    def pick_zip(self):
        p = filedialog.askopenfilename(parent=self, title="選擇新版 paperlib.zip", filetypes=[("zip", "*.zip")])
        if p:
            self.src_path.set(p); self.cfg["last_zip"] = p; core.save_config(self.cfg)

    def pick_dir(self):
        p = filedialog.askdirectory(parent=self, title="選擇解壓縮後的 paperlib 資料夾")
        if p:
            self.src_path.set(p)

    def check_source(self):
        p = Path(self.src_path.get())
        if not self.src_path.get():
            self.src_info.configure(text="請選擇新版的 zip 或資料夾")
            return
        try:
            if p.is_dir():
                root = core.find_root(p)
                ver = core.read_version(root) if root else ""
                self.src_info.configure(text=f"版本 {ver}　（{root}）" if root else "這個資料夾裡找不到論文庫的程式", foreground="" if root else BAD)
            elif p.is_file() and p.suffix.lower() == ".zip":
                import zipfile
                with zipfile.ZipFile(p) as z:
                    name = next((n for n in z.namelist() if n.endswith("app/config.py")), None)
                    ver = ""
                    if name:
                        import re
                        m = re.search(r'VERSION = "([^"]+)"', z.read(name).decode("utf-8", "replace"))
                        ver = m.group(1) if m else ""
                self.src_info.configure(text=f"zip 裡的版本 {ver}" if name else "這個 zip 裡找不到論文庫的程式", foreground="" if name else BAD)
            else:
                self.src_info.configure(text="找不到這個檔案或資料夾", foreground=BAD)
        except Exception as e:  # noqa: BLE001
            self.src_info.configure(text=f"無法讀取：{e}", foreground=BAD)

    def log(self, line, tag=None):
        t = self.out
        if tag is None:
            tag = "err" if line.lstrip().startswith(("✗", "Error", "ERROR")) else "ok" if "✓" in line else "head" if line.lstrip().startswith("▶") else None
        t.insert("end", line + "\n", tag)
        t.see("end")

    def do_update(self, clean):
        if self.busy:
            return
        if not self.need_nas():
            return
        src = self.src_path.get()
        try:
            root, ver, tmp = core.open_source(src)
        except Exception as e:  # noqa: BLE001
            return self.show_err(str(e))
        cur = (self.status or {}).get("version") or "?"
        if not messagebox.askyesno("更新網站", f"把網站從 {cur} 更新到 {ver}？\n\n過程中網站會暫停 1–3 分鐘{('（完全重建約 5–10 分鐘）' if clean else '')}；資料庫會先自動備份。", parent=self):
            if tmp:
                tmp.cleanup()
            return
        self.busy = True
        self.upd_btn.configure(state="disabled")
        self.out.delete("1.0", "end")
        self.log(f"開始更新：{cur} → {ver}　{datetime.now():%Y-%m-%d %H:%M:%S}", "head")

        def work():
            try:
                return self.nas.update(root, clean, on_line=lambda s: self.ui(lambda: self.log(s)))
            finally:
                if tmp:
                    tmp.cleanup()

        def done(code):
            self.busy = False
            self.upd_btn.configure(state="normal")
            if code == 0:
                self.log(f"✓ 更新完成（{ver}）。請各位成員在瀏覽器按 Ctrl+F5，手機把 App 關掉再開。", "ok")
                messagebox.showinfo("更新網站", f"更新完成，網站目前是 {ver}。", parent=self)
            else:
                self.log(f"✗ 更新沒有完成（代碼 {code}），請看上面的訊息。舊資料都還在。", "err")
            self.refresh()

        def fail(msg):
            self.busy = False
            self.upd_btn.configure(state="normal")
            self.log(f"✗ {msg}", "err")
        self.bg(work, done, fail)

    def box_action(self, what):
        if self.busy or not self.need_nas():
            return
        label = {"restart": "重新啟動網站", "stop": "停止網站", "start": "啟動網站"}[what]
        if what != "start" and not messagebox.askyesno(label, f"確定要{label}？（大家會暫時連不上）", parent=self):
            return
        self.busy = True
        self.out.delete("1.0", "end")
        self.tabs.select(self.tab_update)
        self.log(f"▶ {label}…")

        def work():
            fn = {"restart": self.nas.restart, "stop": self.nas.stop, "start": self.nas.start}[what]
            code, _ = fn(on_line=lambda s: self.ui(lambda: self.log("  " + s)))
            if what != "stop":
                for _ in range(45):
                    ok, _ms, site, _e = self.site.ping()
                    if ok:
                        return code, site.get("version")
                    time.sleep(2)
                return code, None
            return code, None

        def done(r):
            self.busy = False
            code, v = r
            if code != 0:
                self.log(f"✗ {label}失敗（代碼 {code}）", "err")
            elif what == "stop":
                self.log("✓ 已停止", "ok")
            else:
                self.log(f"✓ 網站已恢復（版本 {v}）" if v else "✗ 90 秒內沒有回應，請看「日誌 → 容器日誌」", "ok" if v else "err")
            self.refresh()

        def fail(m):
            self.busy = False
            self.log(f"✗ {m}", "err")
        self.bg(work, done, fail)

    # ---------------------------------------------------------------- 設定
    def open_settings(self):
        d = tk.Toplevel(self); d.title("設定"); d.transient(self); d.grab_set(); d.resizable(False, False)
        fr = ttk.Frame(d, padding=16); fr.pack()
        url = tk.StringVar(value=self.cfg["site_url"]); iv = tk.StringVar(value=str(self.cfg.get("interval", 30)))
        alert = tk.BooleanVar(value=bool(self.cfg.get("alert", True))); cont = tk.StringVar(value=self.cfg.get("container", "paperlib"))
        for i, (lab, var) in enumerate((("網站網址", url), ("自動檢查間隔（秒）", iv), ("容器名稱", cont))):
            ttk.Label(fr, text=lab).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Entry(fr, textvariable=var, width=36).grid(row=i, column=1, pady=3)
        ttk.Checkbutton(fr, text="網站沒有回應時跳出視窗提醒", variable=alert).grid(row=3, column=1, sticky="w", pady=4)

        def forget():
            self.cfg["cookie"] = ""; self.cfg["remember"] = False; core.save_config(self.cfg); self.site.cookie = ""; self.status = None
            messagebox.showinfo("設定", "已登出並清除記住的登入", parent=d); self.refresh()
        ttk.Button(fr, text="登出並清除記住的登入", command=forget).grid(row=4, column=1, sticky="w", pady=4)

        def save():
            try:
                self.cfg["interval"] = max(10, int(iv.get()))
            except ValueError:
                return messagebox.showerror("設定", "間隔請填數字", parent=d)
            changed = url.get().strip().rstrip("/") != self.cfg["site_url"]
            self.cfg.update(site_url=url.get().strip().rstrip("/"), alert=alert.get(), container=cont.get().strip() or "paperlib")
            if changed:
                self.site = core.Site(self.cfg["site_url"]); self.status = None; self.history.clear()
            if self.nas:
                self.nas.container = self.cfg["container"]
            core.save_config(self.cfg); d.destroy(); self.refresh()
        ttk.Button(fr, text="儲存", style="Accent.TButton", command=save).grid(row=5, column=1, sticky="e", pady=(8, 0))

    def _quit(self):
        if self.busy and not messagebox.askyesno("控制台", "還在執行中（例如更新），關掉可能讓更新停在一半。確定要關閉？", parent=self):
            return
        if self.site.cookie and self.cfg.get("remember"):
            self.cfg["cookie"] = self.site.cookie
        core.save_config(self.cfg)
        if self.nas:
            self.nas.close()
        self.destroy()


if __name__ == "__main__":
    Console().mainloop()
