"""1.0.27：量測電腦之間同步儀器清單（經 Hub，不需要兩台同時開著）。

每台量測節點把自己的 instruments.yaml 放到 Hub（已有的 PUT /api/nodes/<n>/instruments），
其他量測節點定期讀回來，把「那台自己的儀器」寫進自己的 instruments.yaml，並標上 ``synced_from: <那台>``：

  * USB / GPIB / 序列埠 / 虛擬儀器（電磁鐵組）→ 實體只接在那台：寫入 ``node: <那台>``，
    這台不載入、不連線，儀器伺服器顯示在那台的群組（那台在線時可以遠端連線 / 控制）。
  * 網路儀器（TCPIP、SHFQC）→ 兩台都載入；兩台都看得到 = 共用，在儀器伺服器把它拉到要用的那台（Hub 記錄歸屬）。

那台改名、移除、隱藏 → 下一次同步跟著改；同名但不同台的儀器不覆蓋（記在 log）。
「那台自己的儀器」= 不是從別台同步來的（沒有 synced_from）、且 node: 沒有指定給別台。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

from ..core.nodes import foreign_map, pins
from .instruments import instrument_key, is_network_key

log = logging.getLogger(__name__)

SYNC_KEY = "synced_from"
#: 同步時不比較 / 不覆蓋的欄位（這台自己的顯示設定）
LOCAL_KEYS = ("hidden",)


def _low(s: Any) -> str:
    return str(s or "").strip().lower()


def parse_text(text: str) -> Dict[str, Dict[str, Any]]:
    import yaml

    try:
        data = yaml.safe_load(text or "") or {}
    except Exception:  # noqa: BLE001
        return {}
    inst = data.get("instruments") if isinstance(data, dict) else None
    if not isinstance(inst, dict):
        return {}
    return {str(k): dict(v) for k, v in inst.items() if isinstance(v, dict) and v.get("driver")}


def portable_key(opts: Dict[str, Any], host: str = "") -> Optional[str]:
    """跨電腦比得起來的識別碼（網路 / USB / SHFQC）；GPIB、序列埠、虛擬儀器 → None。"""
    k = instrument_key(str(opts.get("address") or ""), opts, host or "x")
    if k and k.startswith(("net:", "zi:", "usb:")):
        return k
    return None


def own_entries(conf: Dict[str, Dict[str, Any]], names) -> Dict[str, Dict[str, Any]]:
    """那台「自己的」儀器：不是同步來的、node: 沒有指定給別台。"""
    away = foreign_map(conf, names, strict=True)
    return {n: o for n, o in conf.items() if not o.get(SYNC_KEY) and n not in away}


def mirror_options(opts: Dict[str, Any], owner_node: str, owner_host: str = "") -> Dict[str, Any]:
    o = {k: v for k, v in opts.items() if not str(k).startswith("_")}
    key = instrument_key(str(o.get("address") or ""), o, owner_host or "x")
    if is_network_key(key):
        pass                                            # 網路儀器：兩台都載入（node: 若有寫就照抄）
    else:
        o["node"] = owner_node                          # 實體接在那台
    o[SYNC_KEY] = owner_node
    return o


def _cmp(o: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in o.items() if k not in LOCAL_KEYS}


def plan_sync(local: Dict[str, Dict[str, Any]], owner_node: str, owner_host: str,
              owner_conf: Dict[str, Dict[str, Any]], me_names=()) -> Dict[str, Any]:
    """算出這台要怎麼改（純函式，不寫檔）。

    回傳 {"add": {名稱: 選項}, "replace": {名稱: 選項}, "remove": [名稱], "rename": {舊: 新}, "skipped": [(名稱, 原因)]}。
    rename：那台改了名字（同一台實體 / 同樣的電源配對）——呼叫端用 instedit 改名，再套用其餘變更。
    """
    theirs_raw = own_entries(owner_conf, {owner_node, owner_host} - {""})
    theirs = {n: mirror_options(o, owner_node, owner_host) for n, o in theirs_raw.items()}
    me = {_low(x) for x in me_names} - {""}
    owner_names = {_low(owner_node), _low(owner_host)} - {""}
    # 那台把儀器標成「接在這台」（儀器伺服器拖到這台的群組）→ 這台接手，變成這台自己的
    handover = {n: {k: v for k, v in o.items() if k != SYNC_KEY} for n, o in owner_conf.items()
                if me and not o.get(SYNC_KEY) and ({_low(p) for p in pins(o)} & me)
                and not ({_low(p) for p in pins(o)} & owner_names)}
    mine_synced = {n: o for n, o in local.items() if _low(o.get(SYNC_KEY)) == _low(owner_node)}
    mine_other = {n: o for n, o in local.items() if n not in mine_synced}
    other_keys = {}
    for n, o in mine_other.items():
        k = portable_key(o, owner_host)
        if k:
            other_keys[k] = n

    # 改名：消失的舊名字 ↔ 新出現的名字，同一個位址（實體儀器），或同樣的電源配對（電磁鐵組）
    gone = [n for n in mine_synced if n not in theirs and n not in handover]
    new_names = [n for n in theirs if n not in mine_synced and n not in mine_other]
    rename: Dict[str, str] = {}
    for g in gone:
        kg = portable_key(mine_synced[g], owner_host) or str(mine_synced[g].get("address") or "")
        if not kg:
            continue
        for nn in new_names:
            if nn in rename.values():
                continue
            kn = portable_key(theirs[nn], owner_host) or str(theirs[nn].get("address") or "")
            if kn and kn == kg and theirs[nn].get("driver") == mine_synced[g].get("driver"):
                rename[g] = nn
                break

    def mapped_sources(o: Dict[str, Any]) -> List[str]:
        out = []
        for x in o.get("sources") or []:
            s = str(x)
            base, _, rest = s.partition(".")
            base = rename.get(base, base)
            out.append(base + ("." + rest if rest else ""))
        return out

    for g in gone:
        if g in rename or not mine_synced[g].get("sources"):
            continue
        for nn in new_names:
            if nn in rename.values() or not theirs[nn].get("sources"):
                continue
            if mapped_sources(mine_synced[g]) == [str(x) for x in theirs[nn]["sources"]] and \
                    theirs[nn].get("driver") == mine_synced[g].get("driver"):
                rename[g] = nn
                break

    add: Dict[str, Dict[str, Any]] = {}
    replace: Dict[str, Dict[str, Any]] = {}
    remove: List[str] = []
    skipped: List[Tuple[str, str]] = []
    renamed_to = {v: k for k, v in rename.items()}
    for n, mo in theirs.items():
        cur = mine_synced.get(n) or (mine_synced.get(renamed_to[n]) if n in renamed_to else None)
        if cur is not None:
            want = dict(mo)
            if "hidden" not in mo and "hidden" in cur:
                want["hidden"] = cur["hidden"]          # 那台沒設過 → 保留這台自己的隱藏設定
            if want != cur:
                replace[n] = want
            continue
        if n in mine_other:
            o = mine_other[n]
            k_mine, k_theirs = portable_key(o, owner_host), portable_key(mo, owner_host)
            same = bool(k_mine) and k_mine == k_theirs
            pinned_to_owner = _low(owner_node) in {_low(p) for p in pins(o)}
            if not same and not pinned_to_owner:
                skipped.append((n, f"這台已經有另一台叫 {n} 的儀器"))
            continue
        k = portable_key(mo, owner_host)
        if k and k in other_keys:
            continue                                    # 這台已經用別的名字設定了同一台儀器
        add[n] = mo
    for n, o in handover.items():
        cur = local.get(n)
        if cur is None:
            k = portable_key(o, owner_host)
            if not (k and k in other_keys):
                add[n] = o
        elif _low(cur.get(SYNC_KEY)) == _low(owner_node):
            want = dict(o)
            if "hidden" not in o and "hidden" in cur:
                want["hidden"] = cur["hidden"]
            replace[n] = want
    for g in gone:
        if g not in rename:
            remove.append(g)
    return {"add": add, "replace": replace, "remove": remove, "rename": rename, "skipped": skipped}


def text_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


class InstrumentSync:
    """量測節點用：定期把別台的儀器清單同步到這台（NodeService._loop 呼叫 tick）。"""

    def __init__(self, node) -> None:
        self.node = node
        self.seen: Dict[str, str] = {}            # 節點名稱 → 上次處理的文字 hash
        self.my_hash = ""
        self.last: Dict[str, Any] = {}

    def _path(self):
        return self.node._instruments_path()

    def publish_if_changed(self) -> bool:
        p = self._path()
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
        h = text_hash(text)
        if h == self.my_hash:
            return False
        self.node.write_instruments()
        self.my_hash = h
        return True

    def peers(self) -> List[Dict[str, Any]]:
        from ..settings import setting

        allowed = {_low(e.get("node")) for e in (setting("lab_nodes", None) or []) if isinstance(e, dict)}
        out = []
        for n in self.node.hub.nodes():
            name = str(n.get("name") or "")
            if not name or _low(name) == _low(self.node.name) or n.get("simulate"):
                continue
            if allowed and _low(name) not in allowed:
                continue
            out.append(n)
        return out

    def tick(self) -> Dict[str, Any]:
        """發佈這台的清單（有變才送）、讀別台的、套用。回傳這次做了什麼（測試 / log 用）。"""
        st = self.node.station
        if st.simulate:
            return {}
        self.publish_if_changed()
        done: Dict[str, Any] = {}
        for peer in self.peers():
            name = str(peer["name"])
            try:
                text = self.node.hub.instruments(name)
            except Exception as e:  # noqa: BLE001
                log.debug("讀 %s 的儀器清單失敗：%s", name, e)
                continue
            h = text_hash(text)
            if not text.strip() or self.seen.get(name) == h:
                continue
            try:
                res = self.apply(name, str(peer.get("host") or ""), parse_text(text))
            except Exception as e:  # noqa: BLE001
                log.warning("同步 %s 的儀器清單失敗：%s", name, e)
                continue
            if res.get("retry"):
                continue                               # 有儀器量測使用中，下次再同步
            self.seen[name] = h
            done[name] = res
        if done:
            self.publish_if_changed()
        self.last = done
        return done

    def _claim_new_network(self, added: Dict[str, Dict[str, Any]], owner_host: str) -> None:
        """新同步來的網路儀器：還沒有歸屬就先歸原本那台（兩台都看得到 = 共用，沒歸屬會兩台都不能連）。"""
        if not owner_host:
            return
        owners = dict(getattr(self.node, "owners", {}) or {})
        for o in added.values():
            k = instrument_key(str(o.get("address") or ""), o, owner_host)
            if not is_network_key(k) or k in owners:
                continue
            try:
                self.node.hub.claim_instrument(k, owner_host, by=f"同步（{self.node.name}）")
            except Exception as e:  # noqa: BLE001
                log.debug("歸屬 %s 失敗：%s", k, e)

    def apply(self, owner: str, owner_host: str, owner_conf: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        from ..core import instedit, labfile

        st = self.node.station
        path = self._path()
        local = dict(labfile.configured(path)) if path.is_file() else {}
        from ..core.nodes import my_names

        plan = plan_sync(local, owner, owner_host, owner_conf, my_names() | {_low(self.node.name)})
        busy = [n for n in [*plan["remove"], *plan["replace"], *plan["rename"]]
                if n in st.instruments and (st.lease_holder(n) or st.instruments[n].connected)]
        for old, new in plan["rename"].items():
            if old in busy:
                continue
            try:
                instedit.rename_instrument(st, path, old, new)
            except Exception as e:  # noqa: BLE001
                log.warning("同步改名 %s → %s 失敗：%s", old, new, e)
        remove = [n for n in plan["remove"] if n not in busy]
        replace = {n: o for n, o in plan["replace"].items() if n not in busy}
        for n in sorted([*remove, *replace], key=lambda x: not (local.get(x) or {}).get("sources")):
            if n in st.instruments:
                try:
                    st.remove(n)
                except Exception as e:  # noqa: BLE001
                    log.warning("同步：移出 %s 失敗：%s", n, e)
        changed = labfile.apply_changes(path, add=plan["add"], replace=replace, remove=remove)
        self._claim_new_network(plan["add"], owner_host)
        if changed or plan["rename"]:
            try:
                st.apply_ownership()
            except Exception:  # noqa: BLE001
                log.exception("同步後重新套用儀器歸屬失敗")
            st.bus.publish("station.changed", action="sync", node=owner)
            parts = []
            if plan["add"]:
                parts.append("新增 " + "、".join(plan["add"]))
            if plan["rename"]:
                parts.append("改名 " + "、".join(f"{a}→{b}" for a, b in plan["rename"].items()))
            if replace:
                parts.append("更新 " + "、".join(replace))
            if remove:
                parts.append("移除 " + "、".join(remove))
            st.bus.log(f"🔄 已同步 {owner} 的儀器：" + "；".join(parts))
        for n, why in plan["skipped"]:
            log.info("同步 %s：略過 %s（%s）", owner, n, why)
        return {**plan, "retry": bool(busy)}
