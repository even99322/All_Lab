"""
連續擬合（純函式，不依賴 Qt；在擬合子行程中執行）

流程（每個切片）：
    視窗 1 中心 = 上一片擬合出的「追蹤參數 1」 + 偏移 1，寬度 = 遮罩寬度
    視窗 2 中心 = 上一片擬合出的「追蹤參數 2」 + 偏移 2，寬度 = 視窗 2 寬度（選用）
    擬合數據 = 兩個視窗的聯集
    → 若成功且 R² ≥ 門檻，更新兩個追蹤頻率，視窗各自跟著移動
"""
import os
import csv
import json
import numpy as np

from .fitting import run_fit
from .formula import guess_params, freq_factor
from .cleaning import clean_mask, is_active

INIT_MODES = {
    "prev": "Previous Fit",
    "table": "Table Initial Guess (shift with window)",
    "guess": "Auto Guess per Slice",
}


def batch_indices(start, end, step):
    step = max(1, abs(int(step)))
    if end >= start:
        return list(range(start, end + 1, step))
    return list(range(start, end - 1, -step))


def default_moving(units, p0, fmin_hz, fmax_hz):
    """頻率單位且數值落在數據頻率範圍內的參數，視為共振頻率位置"""
    out = []
    for u, v in zip(units, p0):
        fac = freq_factor(u)
        out.append(bool(fac is not None and np.isfinite(v) and fmin_hz <= v * fac <= fmax_hz))
    return out


def window_mask(freq, f1, f2, f1b=None, f2b=None):
    """視窗 1（與選用的視窗 2）的聯集遮罩"""
    m = (freq >= f1) & (freq <= f2)
    if f1b is not None and f2b is not None and np.isfinite(f1b) and np.isfinite(f2b):
        m |= (freq >= f1b) & (freq <= f2b)
    return m


def run_batch(func, module, func_name, freq, S_cols, idxs, axis_vals,
              names, units, p0, lo, hi, fixed, opts, cfg, emit):
    """
    S_cols    : (N, K) 複數，第 k 欄對應切片 idxs[k]
    axis_vals : (K,) 掃描軸數值（已乘倍率）
    cfg       : track, width_hz, offset_hz, init_mode, bound_freq, r2_min, stop_on_fail,
                track2, width2_hz, offset2_hz（第二追蹤參數與其滾動視窗，track2=None 表示不用）,
                moving（list[bool]，哪些參數是「共振頻率位置」→ 隨視窗平移並以視窗為邊界）,
                roll（{參數名: {half, extrap}}，參數滾動追蹤：初值 = 上一片值或外插值，
                      邊界 = 初值 ± half；可用於 phi 等非頻率參數）,
                roll_clip（True → 滾動邊界不超出參數表邊界）,
                clean（去除雜訊設定，見 core/cleaning.clean_mask）,
                link（相位連結，None = 不用）：dict(phase, freq, T, phi_ref, f_ref, period,
                      mode="hard"|"soft", soft_half, guard, skip)
                      hard：φ = φ_ref + 2πT(f_m − f_ref)，不擬合
                      soft：φ 初值取直線預測值，邊界 ± soft_half
                      skip：預測 φ 距節點 nπ 小於 guard 的切片不擬合（訊號太弱），
                            追蹤頻率以線性外插穿過節點
    emit(rec) : 每完成一片呼叫一次
    """
    freq = np.asarray(freq, float)
    p0, lo, hi = (np.asarray(x, float).copy() for x in (p0, lo, hi))
    fixed = np.asarray(fixed, bool)
    names = list(names)
    ti = names.index(cfg["track"])
    tfac = freq_factor(units[ti])
    if tfac is None:
        raise ValueError(f"Tracking parameter {cfg['track']} has non-frequency unit '{units[ti]}'.")
    moving = cfg.get("moving")
    if moving is None:
        moving = default_moving(units, p0, freq.min(), freq.max())
    # 只有「位置型」頻率參數會隨視窗移動；線寬類（同樣是 MHz）不動
    ffac = [freq_factor(u) if mv else None for u, mv in zip(units, moving)]
    nfree = int((~fixed).sum())
    width, offset = float(cfg["width_hz"]), float(cfg["offset_hz"])
    mode = cfg.get("init_mode", "prev")
    r2_min = float(cfg.get("r2_min", -np.inf))

    ti2 = None
    if cfg.get("track2"):
        ti2 = names.index(cfg["track2"])
        if ti2 == ti:
            raise ValueError("Tracking parameter 2 must differ from tracking parameter 1.")
        tfac2 = freq_factor(units[ti2])
        if tfac2 is None:
            raise ValueError(f"Tracking parameter {cfg['track2']} has non-frequency unit '{units[ti2]}'.")
        width2, offset2 = float(cfg["width2_hz"]), float(cfg["offset2_hz"])

    roll = {names.index(n): v for n, v in (cfg.get("roll") or {}).items() if n in names}
    roll = {j: v for j, v in roll.items() if not fixed[j]}
    roll_clip = bool(cfg.get("roll_clip", False))
    clean = cfg.get("clean") if is_active(cfg.get("clean")) else None

    lk = cfg.get("link") or None
    run_link = None
    if lk:
        pj, fj = names.index(lk["phase"]), names.index(lk["freq"])
        lfac = freq_factor(units[fj])
        if lfac is None:
            raise ValueError(f"Phase-linked frequency parameter {lk['freq']} does not use a frequency unit.")
        period = float(lk.get("period", np.pi))
        guard = float(lk.get("guard", 0.0))
        lmode = lk.get("mode", "hard")
        roll.pop(pj, None)
        if lmode == "hard":
            run_link = dict(phase=pj, freq=fj, ffac=lfac, T=float(lk["T"]),
                            phi_ref=float(lk["phi_ref"]), f_ref=float(lk["f_ref"]))
            nfree = int((~fixed).sum()) - (0 if fixed[pj] else 1)

        def link_phase(fval):
            return float(lk["phi_ref"]) + 2 * np.pi * 1e-9 * float(lk["T"]) * (
                fval * lfac - float(lk["f_ref"]))
    skip_run = 0
    hist = []   # 成功切片的 (k, params)，給外插用

    def predict(j, k):
        """參數 j 在第 k 片的預測值：外插（需兩個成功點）或上一片值"""
        if roll[j]["extrap"] and len(hist) >= 2:
            (k1, p1), (k2, p2) = hist[-2], hist[-1]
            if k2 != k1:
                return p2[j] + (p2[j] - p1[j]) / (k2 - k1) * (k - k2)
        return hist[-1][1][j] if hist else p0[j]

    track_val = p0[ti]
    track_val2 = p0[ti2] if ti2 is not None else None
    cur = p0.copy()
    total = len(idxs)

    def extrap(j, k):
        if len(hist) >= 2:
            (k1, p1), (k2, p2) = hist[-2], hist[-1]
            if k2 != k1:
                return p2[j] + (p2[j] - p1[j]) / (k2 - k1) * (k - k2)
        return hist[-1][1][j] if hist else p0[j]

    for k, idx in enumerate(idxs):
        pred = {j: predict(j, k) for j in roll}
        # 追蹤頻率若也設定外插，視窗中心使用預測值
        if ti in pred and roll[ti]["extrap"]:
            track_val = pred[ti]
        if ti2 is not None and ti2 in pred and roll[ti2]["extrap"]:
            track_val2 = pred[ti2]
        phi_pred = node_d = np.nan
        if lk:
            if skip_run > 0:        # 剛穿過節點：以外插決定視窗位置
                track_val = extrap(ti, k)
                if ti2 is not None:
                    track_val2 = extrap(ti2, k)
            fpred = extrap(fj, k)
            phi_pred = link_phase(fpred)
            node_d = float(np.abs((phi_pred + period / 2) % period - period / 2))
        center = track_val * tfac + offset
        a, b = center - width / 2, center + width / 2
        a2 = b2 = np.nan
        if ti2 is not None:
            c2 = track_val2 * tfac2 + offset2
            a2, b2 = c2 - width2 / 2, c2 + width2 / 2
        m = window_mask(freq, a, b, a2, b2)
        nclean = 0
        if clean is not None:
            keep = clean_mask(freq, S_cols[:, k], clean)
            nclean = int((m & ~keep).sum())
            m &= keep
        rec = dict(k=k, total=total, idx=int(idx), axis_val=float(axis_vals[k]),
                   f1=float(a), f2=float(b), f1b=float(a2), f2b=float(b2),
                   npts=int(m.sum()), nclean=nclean, ok=False,
                   params=None, errors=None, r2=np.nan, r2_complex=np.nan,
                   chi2_red=np.nan, nfev=0, elapsed=0.0, msg="",
                   phi_pred=phi_pred, node_dist=node_d, skipped=False)
        if lk and lk.get("skip") and np.isfinite(node_d) and node_d < guard:
            rec["msg"] = f"Skipped near Node (|φ−nπ| = {node_d:.3f} rad)"
            rec["skipped"] = True
            skip_run += 1
            emit(rec)
            continue
        try:
            if m.sum() <= nfree + 1:
                raise ValueError("Too few points in the fit window (it may be outside the measured frequency range).")
            f = freq[m]
            s = S_cols[m, k]
            lo_k, hi_k = lo.copy(), hi.copy()

            if mode == "prev":
                init = cur.copy()
                init[ti] = track_val            # 視窗若以外插移動，追蹤參數初值跟著走
                if ti2 is not None:
                    init[ti2] = track_val2
            elif mode == "guess":
                g, _, _ = guess_params(module, func_name, names, f, s)
                init = p0.copy()
                for j, n in enumerate(names):
                    if not fixed[j]:
                        init[j], lo_k[j], hi_k[j] = g[n]
            else:  # table：頻率參數隨追蹤頻率平移（追蹤參數 2 用自己的位移）
                init = p0.copy()
                shift_hz = (track_val - p0[ti]) * tfac
                shift2_hz = (track_val2 - p0[ti2]) * tfac2 if ti2 is not None else shift_hz
                for j, fac in enumerate(ffac):
                    if fac is not None and not fixed[j]:
                        init[j] += (shift2_hz if j == ti2 else shift_hz) / fac

            if cfg.get("bound_freq", True):
                # 追蹤參數 1 → 視窗 1；追蹤參數 2 → 視窗 2；其他位置型參數 → 兩視窗的外包範圍
                ua, ub = (a, b) if ti2 is None else (min(a, a2), max(b, b2))
                for j, fac in enumerate(ffac):
                    if fac is None or fixed[j]:
                        continue
                    if j == ti:
                        lo_k[j], hi_k[j] = a / fac, b / fac
                    elif j == ti2:
                        lo_k[j], hi_k[j] = a2 / fac, b2 / fac
                    else:
                        lo_k[j], hi_k[j] = ua / fac, ub / fac

            for j, spec in roll.items():
                c0, h = pred[j], spec["half"]
                l, u = c0 - h, c0 + h
                if roll_clip:
                    l, u = max(l, lo[j]), min(u, hi[j])
                    if not l < u:
                        raise ValueError(f"The rolling range for {names[j]} exceeds its parameter-table bounds.")
                init[j] = c0
                lo_k[j], hi_k[j] = l, u

            if lk and lmode == "soft" and not fixed[pj]:
                h = float(lk.get("soft_half", 0.5))
                init[pj] = phi_pred
                lo_k[pj], hi_k[pj] = phi_pred - h, phi_pred + h

            res = run_fit(func, f, s, init, lo_k, hi_k, fixed, opts, link=run_link)
            rec.update(params=res["params"], errors=res["errors"], r2=res["r2"],
                       r2_complex=res["r2_complex"], chi2_red=res["chi2_red"],
                       nfev=res["nfev"], elapsed=res["elapsed"], ok=True, msg="OK")
            if not np.isfinite(res["r2"]) or res["r2"] < r2_min:
                rec["ok"] = False
                rec["msg"] = f"R² {res['r2']:.4f} is below the threshold"
        except Exception as e:
            rec["msg"] = str(e).splitlines()[0][:200]

        if lk and not rec["ok"]:
            skip_run += 1           # 相位連結：失敗片也以外插移動視窗（多半是靠近節點的弱訊號）
        if rec["ok"]:
            skip_run = 0
            track_val = rec["params"][ti]
            if ti2 is not None:
                track_val2 = rec["params"][ti2]
            cur = np.asarray(rec["params"], float).copy()
            hist.append((k, cur.copy()))
        emit(rec)
        if not rec["ok"] and cfg.get("stop_on_fail", False):
            break


# ============================================================================= CSV 存取
BASE_COLS = ["idx", "axis_val", "f1_hz", "f2_hz", "f1b_hz", "f2b_hz", "npts", "ok",
             "r2", "r2_complex", "chi2_red", "nfev", "msg", "phi_pred", "node_dist", "skipped"]


def _num(v):
    return "" if v is None or not np.isfinite(v) else v


def write_batch_csv(path, meta, records):
    """meta 需含 names, units；以暫存檔 + 取代的方式寫入，避免寫到一半損毀"""
    names, units = meta["names"], meta["units"]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
        fh.write("# meta: " + json.dumps(meta, ensure_ascii=False) + "\n")
        wr = csv.writer(fh)
        head = list(BASE_COLS)
        for n, u in zip(names, units):
            head += [f"{n} [{u}]" if u else n, f"{n}_err"]
        wr.writerow(head)
        for r in records:
            f1b, f2b = r.get("f1b", np.nan), r.get("f2b", np.nan)
            row = [r["idx"], r["axis_val"], r["f1"], r["f2"],
                   "" if not np.isfinite(f1b) else f1b, "" if not np.isfinite(f2b) else f2b,
                   r["npts"], int(bool(r["ok"])),
                   r["r2"], r["r2_complex"], r["chi2_red"], r["nfev"], r["msg"],
                   _num(r.get("phi_pred", np.nan)), _num(r.get("node_dist", np.nan)),
                   int(bool(r.get("skipped", False)))]
            if r["params"] is None:
                row += [""] * (2 * len(names))
            else:
                for v, e in zip(r["params"], r["errors"]):
                    row += [v, "" if not np.isfinite(e) else e]
            wr.writerow(row)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_batch_csv(path):
    """以表頭欄名解析（相容沒有視窗 2 欄位的舊檔）"""
    with open(path, "r", encoding="utf-8-sig") as fh:
        first = fh.readline()
        if not first.startswith("# meta: "):
            raise ValueError("This is not a Continuous Fit CSV produced by LabLogViewer (metadata row missing).")
        meta = json.loads(first[len("# meta: "):])
        rd = csv.reader(fh)
        header = next(rd)
        names = meta["names"]
        nb = 0
        while nb < len(header) and header[nb] in BASE_COLS:
            nb += 1
        col = {h: i for i, h in enumerate(header[:nb])}

        def fl(x):
            return float(x) if x not in ("", None) else np.nan

        def get(row, key, default=""):
            i = col.get(key)
            return row[i] if i is not None and i < len(row) else default

        records = []
        for row in rd:
            if not row:
                continue
            rec = dict(k=len(records), total=0, idx=int(get(row, "idx")),
                       axis_val=fl(get(row, "axis_val")),
                       f1=fl(get(row, "f1_hz")), f2=fl(get(row, "f2_hz")),
                       f1b=fl(get(row, "f1b_hz")), f2b=fl(get(row, "f2b_hz")),
                       npts=int(float(get(row, "npts", 0) or 0)),
                       ok=bool(int(float(get(row, "ok", 0) or 0))),
                       r2=fl(get(row, "r2")), r2_complex=fl(get(row, "r2_complex")),
                       chi2_red=fl(get(row, "chi2_red")),
                       nfev=int(float(get(row, "nfev", 0) or 0)), msg=get(row, "msg"), elapsed=0.0,
                       phi_pred=fl(get(row, "phi_pred")), node_dist=fl(get(row, "node_dist")),
                       skipped=bool(int(float(get(row, "skipped", 0) or 0))))
            vals = row[nb:nb + 2 * len(names)]
            if vals and vals[0] != "":
                rec["params"] = np.array([fl(v) for v in vals[0::2]])
                rec["errors"] = np.array([fl(v) for v in vals[1::2]])
            else:
                rec["params"] = rec["errors"] = None
            records.append(rec)
    for r in records:
        r["total"] = len(records)
    return meta, records
