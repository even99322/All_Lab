"""1.0.9：代讀電腦（Hub 指定這台讀另一間實驗室的華碩路由器）要做的事。

* 每次回報前讀路由器的外網累計與連線設備（1.0.6）；每 10 分鐘另外讀 Traffic Analyzer（每小時、各設備、App）。
* 設備登記：Hub 回應的 site_block = 該擋的 MAC（沒登記、寬限時間過了）→ 華碩家長電腦控制（只擋上網，內網照通）。
* 登記頁：這台電腦開 http://<這台>:47680/（防火牆規則由安裝程式加）——那間實驗室的手機連不到 Hub，
  在這裡填設備名稱、身分；用它的 IP 在路由器的連線設備清單找到 MAC，下一次回報轉給 Hub。
路由器帳密只在記憶體（Hub 每次回報的回應給）。只用標準函式庫。
"""
from __future__ import annotations

import json
import logging
import re
import socketserver
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Dict, List, Optional

from . import asus

log = logging.getLogger("netmeter")
REG_PORT = 47680
TA_EVERY_S = 600.0
BLOCK_CHECK_S = 600.0
ROLES = {"student": "學生", "teacher": "老師", "guest": "訪客"}


class Collector:
    def __init__(self, kick: Optional[Callable[[], None]] = None, port: int = REG_PORT,
                 asus_factory: Optional[Callable[..., Any]] = None) -> None:
        self.kick = kick or (lambda: None)
        self.port = port
        self.asus_factory = asus_factory
        self.lock = threading.Lock()
        self.cfg: Dict[str, Any] = {}
        self._router: Any = None
        self._router_key: Any = None
        self.last: Optional[Dict[str, Any]] = None        # 上一次讀的（site_router）
        self.clients: List[Dict[str, Any]] = []
        self.ta: Optional[Dict[str, Any]] = None
        self.ta_error = ""
        self._ta_due = 0.0
        self.want_block: Optional[List[str]] = None       # Hub 說要擋的
        self._applied: Optional[List[str]] = None
        self._block_due = 0.0
        self.block_state: Optional[Dict[str, Any]] = None
        self.regs: List[Dict[str, Any]] = []              # 登記頁送來、還沒被 Hub 收的
        self.reg_results: Dict[str, Dict[str, Any]] = {}
        self.reg_status: Dict[str, Dict[str, Any]] = {}   # Hub 給的：各 MAC 的登記狀態（登記頁顯示）
        self.enforce = False
        self.srv: Optional[socketserver.ThreadingTCPServer] = None
        self.srv_error = ""

    # ---- Hub 的設定 ----
    def configure(self, c: Dict[str, Any]) -> None:
        with self.lock:
            self.cfg = dict(c or {})
        if self.cfg:
            self.start_page()
        else:
            self.stop_page()

    def active(self) -> bool:
        return bool(self.cfg.get("host"))

    def _client(self) -> Any:
        c = self.cfg
        key = (c.get("host"), c.get("user"), c.get("password"), c.get("port"), c.get("ssl"))
        if self._router is None or key != self._router_key:
            factory = self.asus_factory or asus.Asus
            self._router = factory(c["host"], c.get("user") or "admin", c.get("password") or "",
                                             int(c.get("port") or 0), bool(c.get("ssl")))
            self._router_key = key
        return self._router

    # ---- 每次回報前 ----
    def read(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not self.active():
            return None
        now = time.time() if now is None else now
        site = self.cfg.get("site")
        try:
            r = self._client().read()
            self.clients = list(r.get("clients") or [])
            out = dict(r, site=site, t=now)
        except Exception as e:  # noqa: BLE001
            self._router = None                           # 下一次重新登入
            out = {"site": site, "ok": False, "error": str(e)[:300], "t": now}
        if out.get("ok") is not False and now >= self._ta_due:
            self._ta_due = now + TA_EVERY_S
            try:
                fn = getattr(self._client(), "traffic_analyzer", None)
                if fn is not None:
                    self.ta = fn(now)
                    self.ta_error = ""
                    out["ta"] = self.ta
            except Exception as e:  # noqa: BLE001
                self.ta_error = str(e)[:300]
        self.last = out
        return out

    # ---- Hub 的回應 ----
    def on_hub(self, resp: Dict[str, Any]) -> None:
        if isinstance(resp.get("site_block"), list):
            want = sorted({str(m).upper() for m in resp["site_block"] if re.match(r"^[0-9A-Fa-f:]{17}$", str(m))})
            self.want_block = want
        if isinstance(resp.get("reg_status"), dict):
            self.reg_status = {str(k).upper(): v for k, v in resp["reg_status"].items() if isinstance(v, dict)}
        if "enforce" in resp:
            self.enforce = bool(resp.get("enforce"))
        acks = resp.get("regs_ack")
        if isinstance(acks, list):
            done = {}
            for a in acks:
                if isinstance(a, dict) and a.get("id"):
                    done[str(a["id"])] = a
            with self.lock:
                self.regs = [r for r in self.regs if r["id"] not in done]
                for k, a in done.items():
                    self.reg_results[k] = {"ok": bool(a.get("ok")), "error": str(a.get("error") or ""), "t": time.time()}
        self.apply_block()

    def apply_block(self, force: bool = False, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        if self.want_block is None or not self.active():
            return
        if not force and self.want_block == self._applied and now < self._block_due:
            return
        try:
            r = self._client().set_blocked(self.want_block)
            self._applied = list(self.want_block)
            self.block_state = {"ok": True, "macs": list(self.want_block), "t": now}
            if r.get("changed"):
                log.info("設備登記：停網 %s", ", ".join(self.want_block) or "（無）")
        except Exception as e:  # noqa: BLE001
            self._router = None
            self.block_state = {"ok": False, "macs": list(self._applied or []), "error": str(e)[:300], "t": now}
        self._block_due = now + BLOCK_CHECK_S

    def payload(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.last:
            out["site_router"] = self.last
        if self.block_state is not None:
            out["block_state"] = self.block_state
        with self.lock:
            if self.regs:
                out["regs"] = [dict(r) for r in self.regs[:50]]
        return out

    def status(self) -> Dict[str, Any]:
        sr = self.last or {}
        return {"site": self.cfg.get("site"), "host": self.cfg.get("host"), "ok": sr.get("ok"),
                "error": sr.get("error", ""), "ta_error": self.ta_error, "ta_time": (self.ta or {}).get("t"),
                "block": self.block_state, "enforce": self.enforce, "page": self.srv is not None,
                "page_error": self.srv_error, "port": self.port, "pending_regs": len(self.regs)}

    # ---- 登記頁 ----
    def mac_of(self, ip: str) -> str:
        for c in self.clients:
            if c.get("ip") == ip:
                return str(c.get("mac") or "").upper()
        try:                                              # 剛連上的：重新讀一次連線設備
            self.clients = list(self._client().read().get("clients") or [])
        except Exception:  # noqa: BLE001
            return ""
        return next((str(c.get("mac") or "").upper() for c in self.clients if c.get("ip") == ip), "")

    def self_view(self, ip: str) -> Dict[str, Any]:
        mac = self.mac_of(ip) if self.active() else ""
        if not mac:
            return {"ok": False, "ip": ip, "why": "unknown"}
        s = self.reg_status.get(mac) or {}
        pend = next((r for r in self.regs if r["mac"] == mac), None)
        return {"ok": True, "ip": ip, "mac": mac, "reg": s, "name": s.get("name", ""), "roles": ROLES,
                "sent": bool(pend), "enforce": self.enforce}

    def submit(self, ip: str, body: Dict[str, Any]) -> Dict[str, Any]:
        mac = self.mac_of(ip)
        if not mac:
            raise ValueError("路由器上還看不到這台設備：等 1 分鐘再試")
        name = re.sub(r"[\x00-\x1f<>]", "", str(body.get("name") or "")).strip()[:40]
        role = str(body.get("role") or "")
        until = str(body.get("until") or "")[:10]
        if not name:
            raise ValueError("請填設備名稱")
        if role not in ROLES:
            raise ValueError("請選身分")
        if role == "guest" and not re.match(r"^\d{4}-\d{2}-\d{2}$", until):
            raise ValueError("訪客請填到期日")
        rid = uuid.uuid4().hex[:12]
        with self.lock:
            self.regs = [r for r in self.regs if r["mac"] != mac]
            self.regs.append({"id": rid, "mac": mac, "ip": ip, "name": name, "role": role,
                              "until": until if role == "guest" else "", "t": time.time()})
        self.kick()
        return {"ok": True, "id": rid}

    def start_page(self) -> None:
        if self.srv is not None:
            return
        col = self

        class H(_PageHandler):
            collector = col
        try:
            srv = socketserver.ThreadingTCPServer(("0.0.0.0", self.port), H, bind_and_activate=False)
            srv.daemon_threads = True
            srv.allow_reuse_address = True
            srv.server_bind()
            srv.server_activate()
        except OSError as e:
            self.srv_error = f"登記頁開不起來（埠 {self.port}）：{e}"
            log.info(self.srv_error)
            return
        self.srv = srv
        self.srv_error = ""
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
        log.info("設備登記頁：http://<這台>:%s/", self.port)

    def stop_page(self) -> None:
        srv, self.srv = self.srv, None
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:  # noqa: BLE001
                pass


class _PageHandler(BaseHTTPRequestHandler):
    collector: Collector
    server_version = "QELNetMeter"

    def log_message(self, *a: Any) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] == "/api/self":
            try:
                self._json(self.collector.self_view(self.client_address[0]))
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "why": "error", "error": str(e)[:200]})
            return
        if self.path.split("?")[0] in ("/", "/reg"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/api/self":
            self._send(404, b"not found", "text/plain")
            return
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 8192)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            self._json(self.collector.submit(self.client_address[0], body if isinstance(body, dict) else {}))
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": str(e)[:200]}, 500)


PAGE = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>設備登記</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--text:#1d2330;--muted:#6b7385;--line:#dfe3ea;--accent:#2f6fe4;--ok:#1f8f4e;--warn:#c77700;--err:#d23b3b}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1d2129;--text:#e7eaf0;--muted:#98a1b3;--line:#2e3440;--accent:#5b8ff0;--ok:#3fb871;--warn:#e2a13b;--err:#ef6464}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,-apple-system,"Segoe UI","Noto Sans TC","PingFang TC",sans-serif}
main{max-width:460px;margin:0 auto;padding:28px 16px 40px}h1{font-size:22px;margin:0 0 4px}.muted{color:var(--muted);font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:16px}
label{display:block;font-weight:600;margin:14px 0 6px}input[type=text],input[type=date]{width:100%;font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--text)}
.roles{display:flex;gap:8px}.roles label{flex:1;margin:0;font-weight:500;border:1px solid var(--line);border-radius:10px;padding:10px;text-align:center;cursor:pointer}
.roles input{display:none}.roles label.on{border-color:var(--accent);color:var(--accent);font-weight:700}
button{width:100%;margin-top:20px;font:inherit;font-weight:700;padding:12px;border:0;border-radius:10px;background:var(--accent);color:#fff;cursor:pointer}button:disabled{opacity:.5}
.ok{color:var(--ok)}.warn{color:var(--warn)}.err{color:var(--err)}.mac{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;color:var(--muted)}
</style></head><body><main>
<h1>實驗室網路 · 設備登記</h1>
<div class="muted">手機、平板、電腦連上實驗室網路後要登記，沒登記的會在寬限時間後被暫停上網（內網與這一頁照常可以開）。</div>
<div class="card" id="box">讀取中…</div></main>
<script>
const $=s=>document.querySelector(s);const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function st(v){const r=v.reg||{};if(r.state==="ok")return `<b class="ok">✔ 已登記</b>`;if(v.sent)return `<b class="warn">已送出，1 分鐘內生效</b>`;
if(r.state==="pending")return `<b class="warn">尚未登記：還有 ${Math.ceil((r.left||0)/60)} 分鐘</b>`;if(r.state==="due"||r.state==="blocked")return `<b class="err">尚未登記：已暫停上網</b>`;return `<b class="warn">尚未登記</b>`}
function render(v){if(!v.ok){$("#box").innerHTML=`<b>路由器上還看不到這台設備</b>：剛連上的話等 1 分鐘再重新整理。<div class="mac">位址 ${esc(v.ip)}</div>`;return}
const r=v.reg||{},role=r.role||"student";
$("#box").innerHTML=`<div>${st(v)}</div><div class="mac">這台設備：${esc(v.mac)}　${esc(v.ip)}</div><form id="f">
<label for="nm">設備名稱</label><input type="text" id="nm" maxlength="40" placeholder="例：王小明的 iPhone" value="${esc(v.name||"")}" required>
<label>身分</label><div class="roles">${Object.entries(v.roles).map(([k,t])=>`<label class="${k===role?"on":""}"><input type="radio" name="role" value="${k}" ${k===role?"checked":""}>${esc(t)}</label>`).join("")}</div>
<div id="gu" style="display:none"><label for="un">使用到哪一天</label><input type="date" id="un"></div>
<button id="go">${r.state==="ok"?"更新登記":"登記"}</button><div id="msg" class="muted" style="margin-top:10px"></div></form>`;
const sync=()=>{const k=document.querySelector("input[name=role]:checked").value;document.querySelectorAll(".roles label").forEach(l=>l.classList.toggle("on",l.querySelector("input").checked));$("#gu").style.display=k==="guest"?"":"none"};
document.querySelectorAll("input[name=role]").forEach(i=>i.onchange=sync);sync();$("#un").value=new Date(Date.now()+7*864e5).toISOString().slice(0,10);
$("#f").onsubmit=async e=>{e.preventDefault();const k=document.querySelector("input[name=role]:checked").value;$("#go").disabled=true;$("#msg").textContent="送出中…";
try{const res=await fetch("/api/self",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:$("#nm").value.trim(),role:k,until:k==="guest"?$("#un").value:""})});
const j=await res.json().catch(()=>({}));if(!res.ok||j.ok===false)throw new Error(j.error||res.statusText);$("#msg").innerHTML=`<span class="ok">✔ 已送出，1 分鐘內生效，謝謝！</span>`;setTimeout(load,1500)}
catch(err){$("#msg").innerHTML=`<span class="err">✖ ${esc(err.message)}</span>`;$("#go").disabled=false}}}
async function load(){try{render(await (await fetch("/api/self",{cache:"no-store"})).json())}catch(e){$("#box").textContent="讀取失敗："+e.message}}
load();
</script></body></html>"""
