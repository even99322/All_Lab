"""手機 / 平板看數據（1.0.6）：用 NAS 數據索引（labhub/indexer.py）瀏覽「數據集散地」、看預覽與量測資訊。

    GET /m/data                          → 網頁（手機、平板的瀏覽器；「加到主畫面」像 App 一樣開）
    GET /api/data/list?dir=<相對路徑>     → 這個資料夾的子資料夾（含檔案數、最近修改）與檔案
    GET /api/data/search?q=&star=1&limit= → 檔名 / 路徑 / 標籤搜尋（全部數據）
    GET /api/data/preview?path=          → 一個檔的預覽：座標、數值（2D 為 float32 base64）、量測資訊
                          &complex=1     → 1.0.23：另外帶複數原始資料 zre / zim（有的話；網頁換相位、實部、虛部）

預覽來自索引程式算好的 .npz（labhub/index_codec.py）；Hub 只用標準函式庫讀它（zip + .npy），不需要 numpy。
權限：用論文庫帳號登入（或裝置 token、數據集散地的 .qel/index_key），與 /api/index/* 相同。
"""
from __future__ import annotations

import ast
import base64
import io
import json
import math
import struct
import sys
import threading
import zipfile
from array import array
from collections import OrderedDict
from typing import Any, Dict, List, Tuple

from .index_routes import _allowed, _db, _send_json, norm_rel
from .indexer import preview_path
from .server import Handler, HubHTTPError, route

LIST_LIMIT = 3000              # 一個資料夾最多列出幾個檔
SEARCH_LIMIT = 300
_TYPECODES = {"f8": "d", "f4": "f", "u1": "B", "i8": "q", "i4": "i", "u8": "Q", "u4": "I", "i2": "h", "u2": "H",
              "i1": "b"}


# ---- .npz（標準函式庫）-----------------------------------------------------------------------
def read_npy(raw: bytes) -> Tuple[str, Tuple[int, ...], array]:
    """一個 .npy → (型別, 形狀, array)。只支援索引程式寫的簡單型別（數字）；Fortran 順序（轉置過的 2D）會轉成 C 順序。"""
    if raw[:6] != b"\x93NUMPY":
        raise ValueError("不是 .npy")
    major = raw[6]
    if major == 1:
        hlen, start = struct.unpack("<H", raw[8:10])[0], 10
    else:
        hlen, start = struct.unpack("<I", raw[8:12])[0], 12
    header = ast.literal_eval(raw[start:start + hlen].decode("latin1"))
    descr, shape = str(header["descr"]), tuple(int(n) for n in header["shape"])
    fortran = bool(header.get("fortran_order"))
    if fortran and len(shape) > 2:
        raise ValueError("不支援 3 維以上的 Fortran 順序")
    order, kind = descr[0], descr[1:]
    if kind not in _TYPECODES:
        raise ValueError(f"不支援的型別 {descr}")
    a = array(_TYPECODES[kind])
    a.frombytes(raw[start + hlen:])
    if a.itemsize > 1 and ((order == ">" and sys.byteorder == "little") or (order == "<" and sys.byteorder == "big")):
        a.byteswap()
    n = 1
    for k in shape:
        n *= k
    if len(a) != n:
        raise ValueError("資料長度不對")
    if fortran and len(shape) == 2 and shape[0] > 1 and shape[1] > 1:
        # 1.0.21：Fortran 順序 (r, c) 的第 i 列 = a[i::r]（例如量測資料轉置後存的預覽）
        rows = shape[0]
        c = array(a.typecode)
        for i in range(rows):
            c.extend(a[i::rows])
        a = c
    return kind, shape, a


def read_npz(blob: bytes) -> Tuple[Dict[str, Any], Dict[str, Tuple[str, Tuple[int, ...], array]]]:
    arrays: Dict[str, Tuple[str, Tuple[int, ...], array]] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if name.endswith(".npy"):
                arrays[name[:-4]] = read_npy(z.read(name))
    if "meta" not in arrays:
        raise ValueError("預覽沒有 meta")
    meta = json.loads(arrays.pop("meta")[2].tobytes().decode("utf-8"))
    return meta, arrays


def _floats(a: array) -> List[Any]:
    return [v if math.isfinite(v) else None for v in a]


def _f32_b64(a: array) -> str:
    out = a if a.typecode == "f" else array("f", a)
    if sys.byteorder == "big":
        out = array("f", out)
        out.byteswap()
    return base64.b64encode(out.tobytes()).decode("ascii")


META_KEYS = ("kind", "log_name", "channel_name", "colormap", "z_min", "z_max", "acquisition", "x_name", "x_unit",
             "y_name", "y_unit", "z_name", "z_unit", "transform", "operation_label", "display_unit", "shape",
             "fixed_dims")


def preview_json(blob: bytes, with_complex: bool = False) -> Dict[str, Any]:
    """.npz → 給網頁畫圖的 JSON：1D = x、y 數列；2D = x、y 座標與 z（float32、列優先、base64）。"""
    meta, arrays = read_npz(blob)
    out: Dict[str, Any] = {k: meta.get(k) for k in META_KEYS}
    out["metadata"] = meta.get("metadata")
    x = arrays["x"][2]
    out["x"] = _floats(x)
    if meta.get("kind") == "2d":
        _k, zshape, z = arrays["z"]
        out["y"] = _floats(arrays["y"][2])
        out["ny"], out["nx"] = (zshape + (1, 1))[:2]
        out["z"] = _f32_b64(z)
        out["complex"] = "zre" in arrays and "zim" in arrays          # 1.0.23：可以換成相位、實部、虛部
        if with_complex and out["complex"]:
            out["zre"], out["zim"] = _f32_b64(arrays["zre"][2]), _f32_b64(arrays["zim"][2])
    else:
        out["y"] = _floats(arrays["y"][2])
    return out


class _Cache:
    """最近看過的預覽（同一個檔在手機上來回切換不必重新解）。"""

    def __init__(self, n: int = 24) -> None:
        self.n, self.lock, self.items = n, threading.Lock(), OrderedDict()

    def get(self, key):
        with self.lock:
            v = self.items.get(key)
            if v is not None:
                self.items.move_to_end(key)
            return v

    def put(self, key, value) -> None:
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > self.n:
                self.items.popitem(last=False)


CACHE = _Cache()


# ---- API -------------------------------------------------------------------------------------
def _file(row, cut: int = 0) -> Dict[str, Any]:
    tags = row["file_tags"]
    try:
        tags = json.loads(tags) if tags else []
    except ValueError:
        tags = []
    rel = row["rel"]
    return {"name": rel.rsplit("/", 1)[-1], "rel": rel, "size": row["size"], "mtime": row["mtime"],
            "status": row["status"], "error": row["error"], "log_name": row["log_name"],
            "created": row["creation_time"], "dims": row["sweep_dimension"] or "",
            "tags": tags if isinstance(tags, list) else [], "star": bool(row["file_star"]),
            "preview": row["preview"] == "ok"}


@route("GET", r"/api/data/list", auth=False)
def _list(h: Handler) -> None:
    _allowed(h)
    d = norm_rel(h._query().get("dir", ""))
    con = _db(h)
    try:
        lo = d + "/" if d else ""
        hi = d + "0" if d else "\U0010ffff"
        n = len(lo)
        sub: Dict[str, Dict[str, Any]] = {}
        for r in con.execute("SELECT rel FROM folders WHERE rel >= ? AND rel < ?", (lo, hi)):
            rest = r["rel"][n:]
            if rest:
                name = rest.split("/", 1)[0]
                sub.setdefault(name, {"name": name, "files": 0, "latest": None})
        for r in con.execute(
                "SELECT substr(rel, ?, instr(substr(rel, ?), '/') - 1) AS child, count(*) AS n, max(mtime) AS m "
                "FROM files WHERE rel >= ? AND rel < ? AND instr(substr(rel, ?), '/') > 0 GROUP BY child",
                (n + 1, n + 1, lo, hi, n + 1)):
            f = sub.setdefault(r["child"], {"name": r["child"], "files": 0, "latest": None})
            f["files"], f["latest"] = r["n"], r["m"]
        if d and not sub and con.execute("SELECT 1 FROM folders WHERE rel = ?", (d,)).fetchone() is None \
                and con.execute("SELECT 1 FROM files WHERE folder = ? LIMIT 1", (d,)).fetchone() is None:
            raise HubHTTPError(404, f"數據索引裡沒有 {d}")
        rows = con.execute("SELECT * FROM files WHERE folder = ? ORDER BY mtime DESC LIMIT ?",
                           (d, LIST_LIMIT + 1)).fetchall()
        folders = sorted(sub.values(), key=lambda f: f["name"])
        _send_json(h, {"ok": True, "dir": d, "folders": folders, "files": [_file(r) for r in rows[:LIST_LIMIT]],
                       "more": len(rows) > LIST_LIMIT})
    finally:
        con.close()


@route("GET", r"/api/data/search", auth=False)
def _search(h: Handler) -> None:
    _allowed(h)
    q = h._query()
    text = (q.get("q") or "").strip()
    star = q.get("star") in ("1", "true", "yes")
    try:
        limit = max(1, min(SEARCH_LIMIT, int(q.get("limit", SEARCH_LIMIT))))
    except ValueError:
        limit = SEARCH_LIMIT
    if not text and not star:
        raise HubHTTPError(400, "請輸入要找的文字")
    where, args = [], []
    for word in text.split()[:6]:
        like = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(rel LIKE ? ESCAPE '\\' OR log_name LIKE ? ESCAPE '\\' OR file_tags LIKE ? ESCAPE '\\')")
        args += [like, like, like]
    if star:
        where.append("file_star = 1")
    con = _db(h)
    try:
        rows = con.execute(f"SELECT * FROM files WHERE {' AND '.join(where)} ORDER BY mtime DESC LIMIT ?",
                           (*args, limit + 1)).fetchall()
        _send_json(h, {"ok": True, "q": text, "files": [_file(r) for r in rows[:limit]], "more": len(rows) > limit})
    finally:
        con.close()


@route("GET", r"/api/data/preview", auth=False)
def _preview(h: Handler) -> None:
    _allowed(h)
    rel = norm_rel(h._query().get("path", ""))
    con = _db(h)
    try:
        row = con.execute("SELECT * FROM files WHERE rel = ?", (rel,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise HubHTTPError(404, "數據索引裡沒有這個檔")
    info = _file(row)
    if row["preview"] != "ok":
        _send_json(h, {"ok": True, "file": info, "preview": None,
                       "reason": row["error"] or ("索引還沒算到這個檔" if row["preview"] is None else "這個檔沒有預覽")})
        return
    cplx = h._query().get("complex") == "1"                # 1.0.23：要切換相位 / 實部 / 虛部時才送複數資料
    key = (rel, row["size"], row["mtime"], cplx)
    data = CACHE.get(key)
    if data is None:
        try:
            blob = preview_path(rel, h.hub.cfg.data_dir).read_bytes()
        except OSError:
            raise HubHTTPError(404, "預覽檔不見了（索引程式會重新產生）")
        try:
            data = preview_json(blob, with_complex=cplx)
        except (ValueError, KeyError, zipfile.BadZipFile) as e:
            raise HubHTTPError(500, f"預覽讀不懂：{e}")
        CACHE.put(key, data)
    _send_json(h, {"ok": True, "file": info, "preview": data})


@route("GET", r"/(?:m/)?data/?", auth="any")
def _page(h: Handler) -> None:
    from .web_nav import page

    page(h, "mdata.html", "data")
