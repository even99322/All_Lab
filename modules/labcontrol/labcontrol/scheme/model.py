"""量測方案（Scheme）資料模型 —— 流程圖與 Labber 式參數表共用同一份資料。

流程圖的畫法（對應手繪草圖）：
    ┌────────┐
    │ DC set │◄──┐        設定方塊 + 右側括號 = 掃描迴圈；括號旁寫參數與範圍
    └───┬────┘   │ curr
    ┌───▼────┐   │ 50~100 mA
    │ DC set │◄┐ │        括號越外面 = 迴圈越外層
    └───┬────┘ │ │
    ┌───▼────┐ │ │
    │  VNA   │─┘ │        量測方塊
    └───┬────┘   │
    ┌───▼────┐   │
    │  Data  │───┘        Data 放在哪一層，就「那一層每走一步存一個檔」
    └────────┘

資料結構是一棵樹：掃描模式的 set 方塊擁有 children（= 括號包住的方塊）。
方塊種類：
    set      設定一個參數（磁鐵電流、VNA 功率…）；mode = fixed（設定一次）或 sweep（迴圈）
    measure  量測（VNA / SHFQC），含儀器設定
    save     Data：存檔位置、檔名、格式
    wait     等待
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

KINDS = ("set", "measure", "save", "wait")


def new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Block:
    kind: str
    id: str = field(default_factory=new_id)
    title: str = ""                          # 自訂顯示名稱（空白 = 自動）
    children: List["Block"] = field(default_factory=list)
    # ---- set ----
    target: str = ""                         # "magnet_A"、"VNA1.power"…
    mode: str = "fixed"                      # fixed | sweep
    value: Optional[float] = None            # fixed 的值（顯示單位）
    start: Optional[float] = None            # sweep（顯示單位）
    stop: Optional[float] = None
    step: Optional[float] = None
    points: Optional[int] = None             # 與 step 二擇一
    unit: str = ""                           # 顯示單位：mA、dBm…
    settle: float = 0.0                      # 設定後等待（s）
    axis_name: str = ""                      # 資料軸名稱（Labber step channel）；空白 = 自動
    range_mode: str = "startstop"            # startstop | centerspan（只影響 UI 顯示；存的一律是 start/stop）
    interp: str = "linear"                   # linear | log（用點數時）
    alternate: bool = False                  # 來回掃（Labber: Alternate step direction）
    after: str = "start"                     # 掃完後：start（回第一點）| stay（停在最後）| value（到指定值）
    after_value: Optional[float] = None      # after = value 時的值（顯示單位）
    interleave: bool = False                 # 電流異步（兩台交錯的電磁鐵組）：step = 每台步進，平均每點走 step/2
    # ---- measure ----
    instrument: str = ""                     # "VNA1"、"SHFQC1"
    traces: List[str] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)
    # ---- save ----
    file_name: str = ""
    formats: List[str] = field(default_factory=lambda: ["labber"])
    collision: str = "underscore"
    # ---- wait ----
    seconds: float = 0.0

    # ------------------------------------------------------------------
    @property
    def is_loop(self) -> bool:
        return self.kind == "set" and self.mode == "sweep"

    def sweep_values_count(self) -> int:
        """掃描點數（無效時回傳 0）。"""
        if not self.is_loop or self.start is None or self.stop is None:
            return 0
        if self.points:
            return int(self.points)
        if not self.step:
            return 0
        step = abs(self.step) / 2 if self.interleave else abs(self.step)
        return int(round(abs(self.stop - self.start) / step)) + 1

    _FIELDS = {
        "set": ("target", "mode", "value", "start", "stop", "step", "points", "unit", "settle", "axis_name",
                "range_mode", "interp", "alternate", "after", "after_value", "interleave"),
        "measure": ("instrument", "traces", "settings"),
        "save": ("file_name", "formats", "collision"),
        "wait": ("seconds",),
    }

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"kind": self.kind, "id": self.id}
        if self.title:
            d["title"] = self.title
        for f in self._FIELDS[self.kind]:
            v = getattr(self, f)
            if v is None or (isinstance(v, (str, list, dict)) and not v) or (f == "settle" and v == 0):
                continue
            if _FIELD_DEFAULTS.get(f, object()) == v:
                continue
            if self.kind == "set" and self.mode == "fixed" and f in ("start", "stop", "step", "points", "range_mode",
                                                                      "interp", "alternate", "after", "after_value",
                                                                      "interleave"):
                continue
            if self.kind == "set" and self.mode == "sweep" and f == "value":
                continue
            d[f] = copy.deepcopy(v)
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Block":
        d = dict(d)
        kids = [cls.from_dict(c) for c in d.pop("children", [])]
        kind = d.pop("kind")
        if kind not in KINDS:
            raise ValueError(f"未知的方塊種類 {kind}")
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        b = cls(kind=kind, **{k: v for k, v in d.items() if k in known})
        b.children = kids
        return b


_FIELD_DEFAULTS = {"range_mode": "startstop", "interp": "linear", "alternate": False, "after": "start",
                   "interleave": False}

DEFAULT_RUN: Dict[str, Any] = {}   # 執行預設值一律來自 settings.yaml run_defaults；方案只存使用者改過的


@dataclass
class Scheme:
    name: str = "新量測方案"
    blocks: List[Block] = field(default_factory=list)
    snake: bool = False                      # 內圈來回掃（省去每圈回到起點的斜坡時間）
    run: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_RUN))
    hooks: List[Dict[str, Any]] = field(default_factory=list)
    output: Dict[str, Any] = field(default_factory=dict)   # 檔案設定：file_name、formats、root、project、tags…
    notes: str = ""
    graph: Optional[Any] = None              # SchemeGraph（節點位置與連線）；None = 舊格式純方塊樹

    def sync_from_graph(self) -> None:
        """由節點圖重建方塊樹（編輯器每次修改後呼叫）。"""
        if self.graph is not None:
            self.blocks = self.graph.to_blocks()

    def ensure_graph(self) -> Any:
        from .graph import SchemeGraph

        if self.graph is None:
            self.graph = SchemeGraph.from_blocks(self.blocks)
        return self.graph

    @property
    def loose_ids(self) -> List[str]:
        return self.graph.loose() if self.graph is not None else []

    # ---- 走訪 ----------------------------------------------------------
    def walk(self) -> Iterator[Tuple[Block, int, Optional[Block]]]:
        """前序走訪 → (方塊, 深度, 父方塊)。順序 = 流程圖由上到下的順序。"""
        def rec(items: List[Block], depth: int, parent: Optional[Block]):
            for b in items:
                yield b, depth, parent
                yield from rec(b.children, depth + 1, b)
        yield from rec(self.blocks, 0, None)

    def flat(self) -> List[Block]:
        return [b for b, _, _ in self.walk()]

    def find(self, bid: str) -> Tuple[Block, List[Block], int, Optional[Block]]:
        """→ (方塊, 所在的 list, 索引, 父方塊)"""
        def rec(items: List[Block], parent: Optional[Block]):
            for i, b in enumerate(items):
                if b.id == bid:
                    return b, items, i, parent
                r = rec(b.children, b)
                if r:
                    return r
            return None
        r = rec(self.blocks, None)
        if r is None:
            raise KeyError(bid)
        return r

    def ancestors(self, bid: str) -> List[Block]:
        """外 → 內"""
        chain: List[Block] = []
        _, _, _, parent = self.find(bid)
        while parent is not None:
            chain.insert(0, parent)
            _, _, _, parent = self.find(parent.id)
        return chain

    def last_descendant(self, b: Block) -> Block:
        while b.children:
            b = b.children[-1]
        return b

    # ---- 編輯（UI 與測試共用，全部與 Qt 無關）-----------------------------
    def insert(self, block: Block, after: Optional[str] = None, inside: bool = False) -> Block:
        """after=None → 加到最後；inside=True 且 after 是迴圈 → 成為迴圈內第一個。"""
        if after is None:
            self.blocks.append(block)
            return block
        ref, items, i, _ = self.find(after)
        if inside and ref.is_loop:
            ref.children.insert(0, block)
        else:
            items.insert(i + 1, block)
        return block

    def remove(self, bid: str) -> None:
        b, items, i, _ = self.find(bid)
        items[i:i + 1] = b.children          # 迴圈內的方塊留下，往外提一層

    def move(self, bid: str, delta: int) -> bool:
        b, items, i, _ = self.find(bid)
        j = i + delta
        if not 0 <= j < len(items):
            return False
        items[i], items[j] = items[j], items[i]
        return True

    def indent(self, bid: str) -> bool:
        """移進上方相鄰的迴圈（放在迴圈最後）。"""
        b, items, i, _ = self.find(bid)
        if i == 0 or not items[i - 1].is_loop:
            return False
        items.pop(i)
        items[i - 1].children.append(b)
        return True

    def outdent(self, bid: str) -> bool:
        """移出所在迴圈，放在該迴圈之後。"""
        b, items, i, parent = self.find(bid)
        if parent is None:
            return False
        _, p_items, p_i, _ = self.find(parent.id)
        items.pop(i)
        p_items.insert(p_i + 1, b)
        return True

    def set_mode(self, bid: str, mode: str) -> None:
        """fixed ↔ sweep。改成 sweep 時自動把後面的方塊（到 Data 之前）包進迴圈；
        改成 fixed 時迴圈內的方塊往外提一層。"""
        b, items, i, _ = self.find(bid)
        if b.kind != "set" or b.mode == mode:
            return
        b.mode = mode
        if mode == "sweep" and not b.children:
            j = i + 1
            while j < len(items) and items[j].kind != "save":
                j += 1
            b.children = items[i + 1:j]
            del items[i + 1:j]
        elif mode == "fixed" and b.children:
            items[i + 1:i + 1] = b.children
            b.children = []

    def wrap(self, ids: List[str], loop: Block) -> Block:
        """把同一層、相鄰的幾個方塊包進新的迴圈（Labber 式「新增迴圈」）。"""
        if not ids:
            raise ValueError("沒有要包進迴圈的方塊")
        _, items, i0, _ = self.find(ids[0])
        idx = sorted(self.find(b)[2] for b in ids)
        if any(self.find(b)[1] is not items for b in ids) or idx != list(range(idx[0], idx[0] + len(idx))):
            raise ValueError("只能包住同一層、相鄰的方塊")
        loop.mode = "sweep"
        loop.children = items[idx[0]:idx[-1] + 1]
        items[idx[0]:idx[-1] + 1] = [loop]
        return loop

    def add_outer_loop(self, loop: Block) -> Block:
        """最外層加一圈：包住最上層除了 Data 以外的方塊（Data 維持在外面 = 一個檔）。"""
        ids = [b.id for b in self.blocks if b.kind != "save"]
        if not ids:
            self.blocks.insert(0, loop)
            loop.mode = "sweep"
            return loop
        first = self.find(ids[0])[2]
        ids = [b.id for b in self.blocks[first:first + len(ids)]]
        return self.wrap(ids, loop)

    def add_inner_loop(self, loop: Block) -> Block:
        """最內層加一圈：包住量測方塊。"""
        ms = [b for b in self.flat() if b.kind == "measure"]
        if not ms:
            return self.add_outer_loop(loop)
        _, items, _, _ = self.find(ms[0].id)
        ids = [b.id for b in items if b.kind == "measure"]
        return self.wrap(ids, loop)

    _SET_FIELDS = ("title", "target", "mode", "value", "start", "stop", "step", "points", "unit", "settle",
                   "axis_name", "id")

    def swap_with_inner(self, bid: str) -> bool:
        """迴圈與它裡面的第一個子迴圈交換巢狀順序（Labber 表格的上下移）。"""
        b, _, _, _ = self.find(bid)
        inner = next((c for c in b.children if c.is_loop), None)
        if not b.is_loop or inner is None:
            return False
        for f in self._SET_FIELDS:
            a, c = getattr(b, f), getattr(inner, f)
            setattr(b, f, c)
            setattr(inner, f, a)
        return True

    # ---- 存讀 ----------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        if self.graph is not None:
            d: Dict[str, Any] = {"scheme": 2, "name": self.name, "graph": self.graph.to_dict()}
        else:
            d = {"scheme": 1, "name": self.name, "blocks": [b.to_dict() for b in self.blocks]}
        if self.snake:
            d["snake"] = True
        d["run"] = dict(self.run)
        if self.hooks:
            d["hooks"] = copy.deepcopy(self.hooks)
        if self.output:
            d["output"] = dict(self.output)
        if self.notes:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Scheme":
        graph = None
        if d.get("graph") is not None:
            from .graph import SchemeGraph

            graph = SchemeGraph.from_dict(d["graph"])
            blocks = graph.to_blocks()
        else:
            blocks = [Block.from_dict(b) for b in d.get("blocks", [])]
        return cls(name=d.get("name", "新量測方案"), blocks=blocks,
                   snake=bool(d.get("snake", False)), run={**DEFAULT_RUN, **(d.get("run") or {})},
                   hooks=list(d.get("hooks") or []), output=dict(d.get("output") or {}),
                   notes=d.get("notes", ""), graph=graph)

    def save(self, path: str | Path) -> None:
        import yaml

        Path(path).write_text(yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False),
                              encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Scheme":
        import yaml

        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})

    def copy(self) -> "Scheme":
        return Scheme.from_dict(copy.deepcopy(self.to_dict()))
