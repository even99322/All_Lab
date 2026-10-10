"""結果文字與匯出（純函式）"""
import csv
import numpy as np


def unit_label(unit):
    return unit if unit else "—"


def _angle_extra(unit, v):
    u = (unit or "").lower()
    if u == "rad":
        return f"  [{np.degrees(v):.2f}°, {v/np.pi:.4f}π]"
    return ""


def _freq_line(meta):
    txt = f"頻率   : {meta['f1']/1e9:.6f} ~ {meta['f2']/1e9:.6f} GHz"
    f1b, f2b = meta.get("f1b"), meta.get("f2b")
    if f1b is not None and f2b is not None and np.isfinite(f1b) and np.isfinite(f2b):
        txt += f"  ∪  {f1b/1e9:.6f} ~ {f2b/1e9:.6f} GHz"
    txt += f" ({meta['npts']} 點)"
    if meta.get("nclean"):
        txt += f"\n去雜訊 : 去除 {meta['nclean']} 點"
    return txt


def format_report(meta, names, units, res):
    """
    meta : dict(func, file, s_name, idx, axis, axis_val, f1, f2, npts)
    units: list[str]，與 names 對應
    """
    lines = [
        f"=== {meta['func']} ===",
        f"檔案   : {meta['file']}",
        f"S 參數 : {meta['s_name']}",
        f"切片   : #{meta['idx']}  ({meta['axis']} = {meta['axis_val']:.8g})",
        _freq_line(meta),
        f"模式   : {res['mode']}   nfev = {res['nfev']}   耗時 {res['elapsed']:.2f} s",
        "-" * 60,
    ]
    w = max(len(n) for n in names)
    uw = max(len(unit_label(u)) for u in units)
    for n, u, v, e in zip(names, units, res["params"], res["errors"]):
        ul = unit_label(u)
        es = "   (fixed)" if np.isnan(e) else f" ± {e:.3g}"
        lines.append(f"{n:<{w}} : {v:>14.8g}{es:<14} {ul:<{uw}}{_angle_extra(u, v)}")
    lines += [
        "-" * 60,
        f"R² (dB)      = {res['r2']:.6f}",
        f"R² (complex) = {res['r2_complex']:.6f}",
        f"χ²_red       = {res['chi2_red']:.4g}",
    ]
    return "\n".join(lines)


def write_csv(path, meta, names, units, res):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        wr = csv.writer(fh)
        wr.writerow(["# file", meta["file_path"]])
        wr.writerow(["# function", meta["func"], meta["formula_path"]])
        wr.writerow(["# S", meta["s_name"]])
        wr.writerow(["# slice", meta["idx"], meta["axis"], meta["axis_val"]])
        wr.writerow(["# freq range (Hz)", meta["f1"], meta["f2"]]
                    + ([meta["f1b"], meta["f2b"]] if meta.get("f1b") is not None else []))
        wr.writerow(["# R2_dB", res["r2"], "R2_complex", res["r2_complex"], "chi2_red", res["chi2_red"]])
        wr.writerow(["parameter", "unit", "value", "stderr"])
        for n, u, v, e in zip(names, units, res["params"], res["errors"]):
            wr.writerow([n, u, v, "" if np.isnan(e) else e])
