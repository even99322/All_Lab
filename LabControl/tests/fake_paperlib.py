"""測試用的假論文庫（只用標準函式庫），API 形狀與 paperlib 相同的那一小部分：

POST /api/auth/login、/api/auth/logout、GET /api/me、/api/site、/api/users、PATCH /api/users/<id>、
GET/POST /api/tags、GET /api/papers?tag=…

也可以單獨執行（做畫面 / 手動測試）：``python -m tests.fake_paperlib 8090``
帳號：admin / adminpw（站長）、alice / alicepw（成員）、bob / bobpw（已停用）、carol / carolpw（要兩步驟驗證，碼 123456）
"""
from __future__ import annotations

import json
import re
import secrets
import sys
import threading
import urllib.parse
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple


class FakePaperlib:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users: Dict[int, Dict[str, Any]] = {
            1: {"id": 1, "username": "admin", "display_name": "站長", "role": "admin", "owner": 1, "disabled": 0,
                "pw": "adminpw", "email": "", "totp": None},
            2: {"id": 2, "username": "alice", "display_name": "Alice", "role": "member", "owner": 0, "disabled": 0,
                "pw": "alicepw", "email": "", "totp": None},
            3: {"id": 3, "username": "bob", "display_name": "Bob", "role": "member", "owner": 0, "disabled": 1,
                "pw": "bobpw", "email": "", "totp": None},
            4: {"id": 4, "username": "carol", "display_name": "Carol", "role": "viewer", "owner": 0, "disabled": 0,
                "pw": "carolpw", "email": "", "totp": "123456"},
        }
        self.sessions: Dict[str, int] = {}
        self.tags = [{"id": 1, "name": "transmon", "color": "#1971c2", "description": "Transmon qubit", "sort": 1},
                     {"id": 2, "name": "JPA", "color": "#e67700", "description": "Josephson parametric amplifier",
                      "sort": 2},
                     {"id": 3, "name": "flux-tunable", "color": "#2b8a3e", "description": "", "sort": 3}]
        self.papers = [
            {"id": 11, "title": "Charge-insensitive qubit design derived from the Cooper pair box",
             "authors": ["J. Koch", "T. M. Yu"], "year": 2007, "venue": "PRA", "citekey": "koch2007",
             "doi": "10.1103/PhysRevA.76.042319", "tags": ["transmon"]},
            {"id": 12, "title": "Flux-tunable transmon coupler", "authors": ["A. Author"], "year": 2021,
             "venue": "PRApplied", "citekey": "author2021", "doi": "", "tags": ["transmon", "flux-tunable"]},
            {"id": 13, "title": "Josephson parametric amplifier review", "authors": ["B. Writer"], "year": 2019,
             "venue": "RMP", "citekey": "writer2019", "doi": "", "tags": ["JPA"]},
        ]
        # paperlib 1.6+ 的「專案專區」（projects 表 ＋ project_papers）
        self.projects = [{"id": 1, "name": "YIG mirror coupling", "short_name": "Mirror", "description": "單顆 YIG＋mirror",
                          "goal": "", "next_step": "", "status": "active", "color": "#2563eb", "sort": 1,
                          "paper_ids": [12]},
                         {"id": 2, "name": "JPA readout", "short_name": "", "description": "", "goal": "",
                          "next_step": "", "status": "active", "color": "#e67700", "sort": 2, "paper_ids": [13]}]
        self.calls = []                                  # (method, path, headers) 方便測試檢查
        self.fail_x_pl = 0

    def user_for(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        uid = self.sessions.get(token or "")
        u = self.users.get(uid) if uid else None
        return u if u and not u["disabled"] else None


def make_handler(state: FakePaperlib):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def _send(self, code: int, obj: Any, headers: Optional[Dict[str, str]] = None) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> Dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

        def _user(self) -> Optional[Dict[str, Any]]:
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return state.user_for(c["plsession"].value if "plsession" in c else None)

        def _route(self, method: str) -> Tuple[int, Any, Dict[str, str]]:
            u = urllib.parse.urlsplit(self.path)
            q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
            path = u.path
            state.calls.append((method, path, {k.lower(): v for k, v in self.headers.items()}))
            if method != "GET" and self.headers.get("X-PL") != "1":
                state.fail_x_pl += 1
                return 403, {"detail": "缺少 X-PL 標頭"}, {}
            if path == "/api/site":
                return 200, {"name": "假論文庫", "version": "9.9.9", "user": None}, {}
            if path == "/api/auth/login" and method == "POST":
                b = self._body()
                with state.lock:
                    usr = next((x for x in state.users.values() if x["username"] == b.get("username")
                                and not x["disabled"]), None)
                    if usr is None or usr["pw"] != b.get("password"):
                        return 400, {"detail": "帳號或密碼錯誤"}, {}
                    if usr["totp"]:
                        if not b.get("code"):
                            return 200, {"ok": False, "need_2fa": True}, {}
                        if b["code"] != usr["totp"]:
                            return 400, {"detail": "驗證碼錯誤"}, {}
                    tok = secrets.token_urlsafe(16)
                    state.sessions[tok] = usr["id"]
                return 200, {"ok": True}, {"Set-Cookie": f"plsession={tok}; HttpOnly; Max-Age=2592000; Path=/; "
                                                         f"SameSite=lax"}
            if path == "/api/auth/logout" and method == "POST":
                c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
                if "plsession" in c:
                    state.sessions.pop(c["plsession"].value, None)
                return 200, {"ok": True}, {}
            usr = self._user()
            if usr is None:
                return 401, {"detail": "請先登入"}, {}
            if path == "/api/me":
                return 200, {k: usr[k] for k in ("id", "username", "display_name", "email", "role", "owner")}, {}
            if path == "/api/users" and method == "GET":
                if usr["role"] != "admin":
                    return 200, [{"id": x["id"], "display_name": x["display_name"]} for x in state.users.values()], {}
                return 200, [{k: x[k] for k in ("id", "username", "display_name", "role", "owner", "disabled", "email")}
                             for x in state.users.values()], {}
            m = re.fullmatch(r"/api/users/(\d+)", path)
            if m and method == "PATCH":
                if usr["role"] != "admin":
                    return 403, {"detail": "需要管理員權限"}, {}
                uid = int(m.group(1))
                b = self._body()
                if "disabled" in b:
                    if uid == usr["id"]:
                        return 400, {"detail": "不能停用自己"}, {}
                    state.users[uid]["disabled"] = int(bool(b["disabled"]))
                    for t, i in list(state.sessions.items()):
                        if i == uid:
                            del state.sessions[t]
                return 200, {"ok": True}, {}
            if path == "/api/tags" and method == "GET":
                return 200, [dict(t, count=sum(1 for p in state.papers if t["name"] in p["tags"]))
                             for t in state.tags], {}
            if path == "/api/tags" and method == "POST":
                if usr["role"] != "admin":
                    return 403, {"detail": "沒有權限：manage"}, {}
                b = self._body()
                if any(t["name"] == b.get("name") for t in state.tags):
                    return 500, None, {}                      # 真的 paperlib：UNIQUE 衝突 → 500
                state.tags.append({"id": len(state.tags) + 1, "name": b["name"], "color": b.get("color") or "#6b7280",
                                   "description": b.get("description") or "", "sort": 99})
                return 200, {"ok": True}, {}
            if path == "/api/projects" and method == "GET":
                return 200, [{k: v for k, v in pr.items() if k != "paper_ids"} | {"n_papers": len(pr["paper_ids"])}
                             for pr in state.projects], {}
            if path == "/api/projects" and method == "POST":
                if usr["role"] == "viewer":
                    return 403, {"detail": "沒有權限"}, {}
                b = self._body()
                name = (b.get("name") or "").strip()
                if not name:
                    return 400, {"detail": "請填專案名稱"}, {}
                parent = b.get("parent_id")
                if parent is not None and not any(pr["id"] == int(parent) for pr in state.projects):
                    return 400, {"detail": "找不到上層專案"}, {}
                if any(pr["name"] == name and pr.get("parent_id") == parent for pr in state.projects):
                    return 409, {"detail": "已經有同名的專案"}, {}
                pid = max(pr["id"] for pr in state.projects) + 1
                state.projects.append({"id": pid, "name": name, "short_name": b.get("short_name") or "",
                                       "description": b.get("description") or "", "goal": "", "next_step": "",
                                       "status": "active", "color": b.get("color") or "#2563eb", "sort": pid,
                                       "paper_ids": [], "parent_id": int(parent) if parent is not None else None})
                return 200, {"ok": True, "id": pid}, {}
            m = re.fullmatch(r"/api/projects/(\d+)", path)
            if m and method == "GET":
                pr = next((x for x in state.projects if x["id"] == int(m.group(1))), None)
                if pr is None:
                    return 404, {"detail": "找不到專案"}, {}
                papers = [dict(p, role="核心") for p in state.papers if p["id"] in pr["paper_ids"]]
                return 200, {**{k: v for k, v in pr.items() if k != "paper_ids"}, "papers": papers}, {}
            if path == "/api/papers":
                items = [p for p in state.papers if not q.get("tag") or q["tag"] in p["tags"]]
                return 200, {"total": len(items), "items": items[: int(q.get("limit", 60))]}, {}
            return 404, {"detail": "Not Found"}, {}

        def _do(self, method: str) -> None:
            code, obj, hd = self._route(method)
            if obj is None:
                body = b"Internal Server Error"
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._send(code, obj, hd)

        def do_GET(self) -> None:  # noqa: N802
            self._do("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._do("POST")

        def do_PATCH(self) -> None:  # noqa: N802
            self._do("PATCH")
    return H


def start(port: int = 0) -> Tuple[ThreadingHTTPServer, FakePaperlib, str]:
    state = FakePaperlib()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    srv, _, url = start(int(sys.argv[1]) if len(sys.argv) > 1 else 8090)
    print("假論文庫：", url)
    threading.Event().wait()
