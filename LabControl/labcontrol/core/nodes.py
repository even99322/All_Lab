"""儀器歸屬哪台量測電腦（instruments.yaml 的 ``node:``）。

全實驗室共用一份 instruments.yaml 時，用 ``node`` 標明每台儀器實體接在哪台量測電腦::

    DC1: {driver: yokogawa.gs200, address: USB0::..., node: LAB-1}
    VNA2: {driver: rs.zna, address: TCPIP0::192.168.1.12::INSTR, node: LAB-2}
    SHFQC1: {driver: zi.shfqc, node: [LAB-1, LAB-2]}     # 兩台都可能用（共用，誰用誰在儀器伺服器拉過去）

規則：
  * 這台是量測電腦——它的名稱（量測節點名稱：LAB_CONTROL_NODE_NAME、settings remote.node.name、
    工具列「🛰 量測節點」的名稱，或電腦名稱）出現在檔案裡任何一個 ``node``，或它是量測節點
    （App 量測電腦模式、量測節點執行中、或設定為自動上線）
    → 只載入 node 包含這台、或沒寫 node 的儀器；
    別台的儀器不建立、不連線、不回報給 Hub（儀器伺服器顯示在那台的群組）。
  * 虛擬儀器（magnet_A: sources [DC3, DC4]）沒寫 node → 跟著它的電源；
    反過來，虛擬儀器明寫 node 指定給別台 → 它沒寫 node 的電源也算那台的。
  * 這台的名稱不在任何 ``node`` 裡（客戶端電腦、模擬）→ 全部載入，與以前相同。
"""
from __future__ import annotations

import os
import socket
from typing import Any, Dict, Iterable, List, Optional, Set


def _low(s: Any) -> str:
    return str(s or "").strip().lower()


def pins(opts: Dict[str, Any]) -> List[str]:
    """一台儀器的 node 設定 → 名稱清單（空 = 沒指定）。"""
    v = (opts or {}).get("node")
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).split(",") if x.strip()]


#: 這個程序裡正在執行的量測節點名稱（NodeService.start 登記、stop 取消）
_running: Set[str] = set()


def register_running(name: str, on: bool = True) -> None:
    (_running.add if on else _running.discard)(_low(name))


def _node_state() -> Dict[str, Any]:
    """LAB/node_state.json（工具列「🛰 量測節點」記住的名稱與自動上線）。"""
    try:
        import json

        from ..paths import lab_path

        return json.loads(lab_path("node_state.json").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def my_names() -> Set[str]:
    """這台電腦可能被寫在 node: 的名稱（小寫）：量測節點名稱（環境變數、settings、工具列設定、執行中的節點）、電腦名稱。"""
    names = {_low(socket.gethostname())} | set(_running)
    try:
        from ..settings import setting

        cfg = setting("remote.node.name", "") or ""
        me = setting("lab_node_self", "") or ""          # 1.0.1：「量測電腦對應」裡選的「這台電腦是」
    except Exception:  # noqa: BLE001
        cfg = me = ""
    for v in (os.environ.get("LAB_CONTROL_NODE_NAME"), cfg, me, _node_state().get("name")):
        if v:
            names.add(_low(v))
    names.discard("")
    return names


def is_measure_pc() -> bool:
    """這台是量測電腦：App 量測電腦模式（LAB_CONTROL_NODE_NAME）、量測節點正在執行，或工具列「🛰 量測節點」設為自動上線。"""
    if os.environ.get("LAB_CONTROL_NODE_NAME") or _running:
        return True
    try:
        from ..settings import setting

        if setting("remote.node_enabled", False):
            return True
    except Exception:  # noqa: BLE001
        pass
    return bool(_node_state().get("auto_start"))


def foreign_map(instruments: Dict[str, Dict[str, Any]], me: Optional[Iterable[str]] = None,
                strict: Optional[bool] = None) -> Dict[str, str]:
    """{儀器名稱: 它歸屬的量測電腦}——只列「不屬於這台」的。

    這台不是量測電腦（名稱沒被任何 node 指名、也不是量測電腦模式；strict 可強制）→ 回傳空 dict（全部載入）。
    """
    me_set = {_low(x) for x in (me if me is not None else my_names())}
    all_pins = {_low(p) for o in instruments.values() for p in pins(o or {})}
    if strict is None:
        strict = bool(me_set & all_pins) or (me is None and is_measure_pc())
    if not strict:
        return {}
    out: Dict[str, str] = {}
    for name, o in instruments.items():
        p = pins(o or {})
        if p and not ({_low(x) for x in p} & me_set):
            out[name] = p[0]
    changed = True
    while changed:
        changed = False
        for name, o in instruments.items():
            srcs = list(dict.fromkeys(str(s).split(".")[0] for s in ((o or {}).get("sources") or [])))
            if name in out:
                # 虛擬儀器「明寫」指定給別台 → 它的電源（沒寫 node 的）也在那台（magnet_B: node LAB-1 → DC1、DC2）
                if not pins(o or {}):
                    continue
                for s_ in srcs:
                    if s_ in instruments and s_ not in out and not pins(instruments[s_] or {}):
                        out[s_] = out[name]
                        changed = True
                continue
            if pins(o or {}):
                continue
            # 虛擬儀器沒寫 node → 跟著電源（任何一個電源不在這台就不能建立）
            away = [out[s_] for s_ in srcs if s_ in out]
            if away:
                out[name] = away[0]
                changed = True
    return out
