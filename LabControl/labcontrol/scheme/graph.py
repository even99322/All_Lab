"""量測方案的「節點圖」表示法：節點可自由擺放、用連線決定執行順序。

    ┌──────┐
    │ 開始 │
    └──┬───┘ next
    ┌──▼──────────────┐
    │ 磁鐵 A 50→100 mA│── 每一點 ⟲ ──▶ [磁鐵 B 掃描] ── 每一點 ⟲ ──▶ [VNA]
    └──┬──────────────┘                      │ 完成後
       │ 完成後                               ▼
       ▼                                   [Data]   ← 放在 A 的迴圈裡 = 每個 A 值一個檔
     （結束）

規則（與 Scheme 的方塊樹一一對應）：
  * 每個節點有一個輸入；「next」輸出接下一步。
  * 掃描節點另有「body」輸出（每一點 ⟲）：接出去的那條鏈 = 迴圈內容，走完自動回到掃描節點取下一個值。
  * 一個輸出只能接一個節點、一個節點只能有一條輸入線（樹狀），不能形成環。
  * 沒有接到「開始」的節點不會執行（檢查時會警告）。

這個模組不 import Qt：UI、CLI、測試共用同一份邏輯。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .model import Block

START = "start"
NEXT, BODY = "next", "body"

Pos = Tuple[float, float]
PortKey = Tuple[str, str]        # (節點 id, 輸出名稱)

# 自動排版間距（像素，只影響第一次開啟舊格式方案時的位置）
DX, DY = 300.0, 120.0


class GraphError(ValueError):
    pass


@dataclass
class SchemeGraph:
    nodes: Dict[str, Block] = field(default_factory=dict)       # 不含 children
    pos: Dict[str, Pos] = field(default_factory=lambda: {START: (0.0, 0.0)})
    links: Dict[PortKey, str] = field(default_factory=dict)

    # ---- 查詢 ------------------------------------------------------------
    @staticmethod
    def ports_of(block: Optional[Block]) -> List[str]:
        if block is None:                    # 開始節點
            return [NEXT]
        return [BODY, NEXT] if block.is_loop else [NEXT]

    def ports(self, nid: str) -> List[str]:
        return self.ports_of(None if nid == START else self.nodes.get(nid))

    def incoming(self, dst: str) -> Optional[PortKey]:
        return next((k for k, v in self.links.items() if v == dst), None)

    def target(self, src: str, port: str) -> Optional[str]:
        return self.links.get((src, port))

    def reachable(self, frm: str = START) -> List[str]:
        """由 frm 出發可走到的節點（不含 frm），深度優先 = 執行順序。"""
        out: List[str] = []
        seen = {frm}
        stack = [frm]
        while stack:
            n = stack.pop()
            for port in reversed(self.ports(n)):
                d = self.links.get((n, port))
                if d is not None and d not in seen:
                    seen.add(d)
                    out.append(d)
                    stack.append(d)
        return out

    def loose(self) -> List[str]:
        """沒有接到開始的節點。"""
        r = set(self.reachable())
        return [n for n in self.nodes if n not in r]

    def body_nodes(self, loop_id: str) -> List[str]:
        """迴圈內容（body 鏈及其下游的所有節點）。"""
        first = self.links.get((loop_id, BODY))
        if first is None:
            return []
        return [first, *self.reachable(first)]

    def loop_parents(self, nid: str) -> List[str]:
        """由外到內包住 nid 的掃描節點。"""
        chain: List[str] = []
        cur = nid
        while True:
            inc = self.incoming(cur)
            if inc is None:
                break
            src, port = inc
            if port == BODY:
                chain.append(src)
            cur = src
        return list(reversed(chain))

    # ---- 編輯 ------------------------------------------------------------
    def add(self, block: Block, pos: Pos = (0.0, 0.0)) -> Block:
        block = copy.deepcopy(block)
        block.children = []
        self.nodes[block.id] = block
        self.pos[block.id] = (float(pos[0]), float(pos[1]))
        return block

    def connect(self, src: str, port: str, dst: str) -> None:
        if dst == START:
            raise GraphError("不能連到「開始」")
        if src == dst:
            raise GraphError("不能連到自己")
        if src != START and src not in self.nodes or dst not in self.nodes:
            raise GraphError("節點不存在")
        if port not in self.ports(src):
            raise GraphError(f"這個節點沒有「{port}」輸出")
        if src == dst or dst in [START, *self.upstream(src)]:
            raise GraphError("不能形成迴圈連線（流程只能往下走）")
        old = self.incoming(dst)
        if old is not None:
            del self.links[old]
        self.links[(src, port)] = dst

    def upstream(self, nid: str) -> List[str]:
        out = []
        cur = nid
        while True:
            inc = self.incoming(cur)
            if inc is None:
                return out
            cur = inc[0]
            out.append(cur)

    def disconnect(self, src: str, port: str) -> None:
        self.links.pop((src, port), None)

    def insert_on_link(self, src: str, port: str, nid: str) -> None:
        """把 nid 插進 src.port → 原本目標 之間。"""
        old = self.links.get((src, port))
        inc = self.incoming(nid)
        if inc is not None:
            del self.links[inc]
        self.links[(src, port)] = nid
        if old is not None and old != nid:
            tail = self.chain_tail(nid)
            self.links[(tail, NEXT)] = old

    def chain_tail(self, nid: str) -> str:
        cur = nid
        seen = set()
        while (cur, NEXT) in self.links and cur not in seen:
            seen.add(cur)
            cur = self.links[(cur, NEXT)]
        return cur

    def append_after(self, src: str, nid: str) -> None:
        """接在 src 後面（src 若是掃描節點且 body 還空著 → 接到 body）。"""
        if src != START and self.nodes[src].is_loop and (src, BODY) not in self.links:
            self.connect(src, BODY, nid)
        else:
            self.insert_on_link(src, NEXT, nid)

    def remove(self, nid: str, heal: bool = True) -> None:
        """刪除節點；heal=True 時把前一個與下一個接起來（像刪除清單中的一項）。"""
        if nid == START or nid not in self.nodes:
            return
        inc = self.incoming(nid)
        nxt = self.links.get((nid, NEXT))
        body = self.links.get((nid, BODY))
        for k in [k for k in self.links if k[0] == nid or self.links[k] == nid]:
            del self.links[k]
        if heal and inc is not None:
            head = body if body is not None else nxt
            if head is not None:
                self.links[inc] = head
                if body is not None and nxt is not None:
                    self.links[(self.chain_tail(body), NEXT)] = nxt
        del self.nodes[nid]
        self.pos.pop(nid, None)

    def set_mode(self, nid: str, mode: str) -> None:
        """切換 fixed / sweep。從迴圈改成固定值時，body 鏈接回主流程。"""
        b = self.nodes[nid]
        if b.kind != "set" or b.mode == mode:
            return
        body = self.links.pop((nid, BODY), None)
        b.mode = mode
        if body is not None:
            nxt = self.links.get((nid, NEXT))
            self.links[(nid, NEXT)] = body
            if nxt is not None:
                self.links[(self.chain_tail(body), NEXT)] = nxt

    # ---- 與方塊樹互轉 ---------------------------------------------------------
    def to_blocks(self) -> List[Block]:
        seen: set = set()

        def chain(nid: Optional[str]) -> List[Block]:
            out = []
            while nid is not None and nid not in seen and nid in self.nodes:
                seen.add(nid)
                b = copy.deepcopy(self.nodes[nid])
                b.children = chain(self.links.get((nid, BODY))) if b.is_loop else []
                out.append(b)
                nid = self.links.get((nid, NEXT))
            return out

        return chain(self.links.get((START, NEXT)))

    @classmethod
    def from_blocks(cls, blocks: List[Block], origin: Pos = (0.0, 0.0)) -> "SchemeGraph":
        """由方塊樹建圖並自動排版：主流程往下，迴圈內容往右。"""
        g = cls()
        g.pos[START] = origin

        def place(items: List[Block], prev: Tuple[str, str], x: float, y: float) -> float:
            for b in items:
                g.add(b, (x, y))
                g.links[prev] = b.id
                y += DY
                if b.is_loop:
                    by = place(b.children, (b.id, BODY), x + DX, y - DY)
                    y = max(y, by)
                prev = (b.id, NEXT)
            return y

        place(blocks, (START, NEXT), origin[0], origin[1] + DY)
        return g

    # ---- 存讀 -------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        nodes = []
        for nid, b in self.nodes.items():
            d = b.to_dict()
            d.pop("children", None)
            x, y = self.pos.get(nid, (0.0, 0.0))
            d["pos"] = [round(x, 1), round(y, 1)]
            nodes.append(d)
        sx, sy = self.pos.get(START, (0.0, 0.0))
        return {"start": [round(sx, 1), round(sy, 1)], "nodes": nodes,
                "links": [[s, p, d] for (s, p), d in self.links.items()]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SchemeGraph":
        g = cls()
        g.pos[START] = tuple(d.get("start") or (0.0, 0.0))  # type: ignore[assignment]
        for nd in d.get("nodes") or []:
            nd = dict(nd)
            p = nd.pop("pos", None) or (0.0, 0.0)
            nd.pop("children", None)
            b = Block.from_dict(nd)
            g.nodes[b.id] = b
            g.pos[b.id] = (float(p[0]), float(p[1]))
        for s, p, t in d.get("links") or []:
            if (s == START or s in g.nodes) and t in g.nodes and p in g.ports(s):
                g.links[(s, p)] = t
        return g

    def copy(self) -> "SchemeGraph":
        return SchemeGraph.from_dict(copy.deepcopy(self.to_dict()))

    def __iter__(self) -> Iterator[Block]:
        return iter(self.nodes.values())
