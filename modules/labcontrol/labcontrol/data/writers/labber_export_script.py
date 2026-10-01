"""labcontrol 原生 HDF5 → Labber log 檔。由 LabberWriter 以 Labber 的 Python 3.8 環境呼叫：

    python38.exe labber_export_script.py <in.h5> <out.hdf5> [--project P] [--user U] [--tag T ...]

★ 此檔案必須維持「獨立、Python 3.8 相容」：只 import numpy / h5py / Labber，不 import labcontrol。

由舊 save_labber.py 一般化而來，輸出結構與舊檔相同（通道名稱、Instrument config/VNA、t0dt 修補），
所以既有的分析程式與 logview 不需修改：
    step channel   軸名稱（例如 'Average Current', 單位 mA）
    step channel   每個 readout 的固定設定（'Output power'、'IF bandwidth'…，各只有一個值）
    log channel    export_name（例如 'VNA - S21'），向量、複數、x=Frequency
"""
import argparse
import datetime
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

# Labber 對新版 numpy 的相容性處理（沿用 save_labber.py）
for _name, _val in (("bool", np.bool_), ("float", np.float64), ("int", np.int32), ("complex", np.complex128)):
    if not hasattr(np, _name):
        setattr(np, _name, _val)


def _s(v):
    return v.decode("utf-8") if isinstance(v, bytes) else str(v)


def build_labber_spec(h5_path):
    """讀取原生檔並轉成 Labber 需要的結構（純資料，不需要 Labber，可單元測試）。"""
    with h5py.File(h5_path, "r") as f:
        schema = _s(f.attrs.get("schema", ""))
        if not schema.startswith(("labcontrol/", "labmaster/")):   # labmaster/ = 開發版舊檔
            raise ValueError("不是 labcontrol 檔案：schema=%r" % schema)
        axes = []
        for k in sorted(f["axes"].keys(), key=int):
            d = f["axes"][k]
            axes.append(dict(name=_s(d.attrs["name"]), unit=_s(d.attrs["display_unit"]),
                             scale=float(d.attrs["display_scale"])))
            axes[-1]["grid"] = d[:]
        setpoints = f["setpoints"][:]
        n = setpoints.shape[0]
        meta = json.loads(_s(f.attrs.get("metadata", "{}")) or "{}")

        if len(axes) == 1:
            # 一維：與舊 save_labber.py 相同，step 值 = 實際量到的點
            order = list(range(n))
            step_channels = [dict(name=axes[0]["name"], unit=axes[0]["unit"],
                                  values=setpoints[:, 0] * axes[0]["scale"])]
        else:
            # 多維：依格點排序（外層為主），step channel 以「最內層在前」交給 Labber
            # ⚠ Labber 多維 step 順序尚未在實機 Labber 驗證（見 docs/MIGRATION.md）
            idx = np.array([[int(np.argmin(np.abs(ax["grid"] - setpoints[i, k]))) for k, ax in enumerate(axes)]
                            for i in range(n)]).reshape(n, len(axes))
            order = [int(i) for i in np.lexsort([idx[:, k] for k in reversed(range(len(axes)))])]
            step_channels = [dict(name=ax["name"], unit=ax["unit"], values=ax["grid"] * ax["scale"])
                             for ax in reversed(axes)]
        entry_axes = [(ax["name"], setpoints[order, k] * ax["scale"]) for k, ax in enumerate(axes)]

        # 分檔（每個外圈值一個檔）時，外圈的值以單一值 step channel 記錄
        for sname, sv in (meta.get("segment") or {}).items():
            step_channels.append(dict(name=sname, unit=sv.get("display_unit", ""),
                                      values=np.array([float(sv["value"]) * float(sv.get("display_scale", 1.0))])))
        log_channels = []
        channel_data = []
        instrument_config = {}
        seen_settings = set()
        for key in f["channels"].keys():
            g = f["channels"][key]
            a = g.attrs
            name = _s(a["name"])
            export = _s(a.get("export_name", "")) or name
            vector = bool(a["vector"])
            is_complex = bool(a["complex"])
            if vector:
                x = g["x"][:]
                log_channels.append(dict(name=export, unit="", complex=is_complex, vector=True,
                                         x_name=_s(a["x_name"]), x_unit=_s(a["x_unit"])))
            else:
                x = None
                log_channels.append(dict(name=export, unit=_s(a["unit"]), complex=is_complex, vector=False))
            channel_data.append(dict(name=export, data=g["data"][:n][order], x=x, vector=vector,
                                     x_name=_s(a["x_name"]), x_unit=_s(a["x_unit"])))

            group = _s(a.get("labber_group", "")) or "Instrument"
            settings = json.loads(_s(a.get("labber_settings", "[]")) or "[]")
            for sname, val, unit in settings:
                if sname in seen_settings:
                    continue
                seen_settings.add(sname)
                step_channels.append(dict(name=sname, unit=unit, values=np.array([float(val)])))
                instrument_config.setdefault(group, []).append((sname, float(val), unit))
    return dict(step_channels=step_channels, log_channels=log_channels, channel_data=channel_data,
                entry_axes=entry_axes, n=n, instrument_config=instrument_config)


def write_labber(spec, out_path, project=None, user="", tags=None, comment=""):
    labber_paths = [r"C:\Program Files\Keysight\Labber\Script", r"C:\Program Files\Labber\Script"]
    for p in labber_paths:
        if os.path.exists(p):
            sys.path.insert(0, p)
            break
    import Labber  # noqa: E402

    class MagicDict(dict):
        """繞過 Labber addEntry 以物件當 key 的 bug（沿用 save_labber.py）。"""

        def _k(self, key):
            if isinstance(key, str):
                return key
            for attr in ("name", "sName", "channel_name", "_name"):
                val = getattr(key, attr, None)
                if isinstance(val, str) and dict.__contains__(self, val):
                    return val
            return None

        def __getitem__(self, key):
            k = self._k(key)
            if k is None:
                raise KeyError("Labber Bug 繞過失敗，找不到 Key: %r" % (key,))
            return dict.__getitem__(self, k)

        def __contains__(self, key):
            k = self._k(key)
            return k is not None and dict.__contains__(self, k)

    if project is None or project == "auto":
        now = datetime.datetime.now()
        project = "%s/%s" % (now.strftime("%m"), now.strftime("%m%d"))

    f = Labber.createLogFile_ForData(out_path, spec["log_channels"], spec["step_channels"], use_database=False)
    f.setProject(project)
    f.setUser(user)
    f.setTags(list(tags or []))
    if comment:
        try:
            f.setComment(comment)
        except Exception:  # noqa: BLE001 - 舊版 Labber API 可能沒有 setComment
            pass

    for i in range(spec["n"]):
        entry = MagicDict({name: vals[i] for name, vals in spec["entry_axes"]})
        for ch in spec["channel_data"]:
            if ch["vector"]:
                x = ch["x"]
                entry[ch["name"]] = Labber.getTraceDict(ch["data"][i], x0=x[0], x1=x[-1])
            else:
                entry[ch["name"]] = ch["data"][i]
        f.addEntry(entry)
    del f

    with h5py.File(out_path, "r+") as h5:
        for ch in spec["channel_data"]:
            if not ch["vector"]:
                continue
            x = ch["x"]
            tp = "Traces/%s" % ch["name"]
            if tp in h5:
                h5[tp].attrs["x, name"] = (ch["x_name"] or "Frequency").encode("utf-8")
                h5[tp].attrs["x, unit"] = (ch["x_unit"] or "Hz").encode("utf-8")
                if tp + "_t0dt" not in h5:
                    step = (x[-1] - x[0]) / (len(x) - 1) if len(x) > 1 else 0.0
                    h5["Traces"].create_dataset(ch["name"] + "_t0dt",
                                                data=np.array([[x[0], step]] * spec["n"], dtype=float))
        inst = h5.require_group("Instrument config")
        for group, items in spec["instrument_config"].items():
            g = inst.require_group(group)
            for sname, val, unit in items:
                if sname in g:
                    del g[sname]
                d = g.create_dataset(sname, data=float(val))
                if unit:
                    d.attrs["unit"] = unit.encode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--project", default="auto")
    ap.add_argument("--user", default="")
    ap.add_argument("--tag", action="append", default=None)
    ap.add_argument("--comment", default="")
    args = ap.parse_args(argv)
    try:
        spec = build_labber_spec(args.src)
        write_labber(spec, args.dst, project=args.project, user=args.user, tags=args.tag,
                     comment=args.comment)
        print("SUCCESS", flush=True)
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print("ERROR: %s" % e, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
