"""數據讀取（純函式，不依賴 Qt，可在任何執行緒/行程呼叫）"""
import inspect
import numpy as np


def _decode(x):
    return x.decode() if isinstance(x, (bytes, np.bytes_)) else str(x)


def load_labber_h5(path):
    """直接用 h5py 讀 Labber HDF5（labber_toolkit 不可用時的備援）"""
    import h5py
    out = {"frequency": None, "s_params": {}, "step_channels": {}}
    with h5py.File(path, "r") as f:
        if "Traces" not in f:
            raise ValueError("檔案內沒有 Traces 群組，不像 Labber VNA 資料")
        tr = f["Traces"]
        for key in tr.keys():
            if key.endswith("_N") or key.endswith("_t0dt") or key.lower().startswith("time stamp"):
                continue
            arr = tr[key][()]
            if arr.ndim != 3:
                continue
            if arr.shape[1] >= 2:
                cplx = arr[:, 0, :] + 1j * arr[:, 1, :]
            else:
                cplx = arr[:, 0, :].astype(complex)
            out["s_params"][key.split(" - ")[-1]] = cplx  # (npts, nsteps)
            if out["frequency"] is None and key + "_t0dt" in tr:
                t0dt = tr[key + "_t0dt"][()]
                t0, dt = float(t0dt[0][0]), float(t0dt[0][1])
                out["frequency"] = t0 + dt * np.arange(arr.shape[0])
        if not out["s_params"]:
            raise ValueError("找不到任何 trace 資料")
        nsteps = next(iter(out["s_params"].values())).shape[1]

        if "Data" in f and "Channel names" in f["Data"] and "Data" in f["Data"]:
            names = [_decode(n) if isinstance(n, (bytes, str, np.bytes_)) else _decode(n[0])
                     for n in f["Data"]["Channel names"][()]]
            dat = f["Data"]["Data"][()]
            for i, nm in enumerate(names):
                if i >= dat.shape[1]:
                    break
                v = dat[0, i, :] if dat.shape[0] == 1 else dat[:, i, :].ravel(order="F")
                v = np.real(v).astype(float)
                if len(v) == nsteps:
                    out["step_channels"][nm] = v
    if out["frequency"] is None:
        out["frequency"] = np.arange(next(iter(out["s_params"].values())).shape[0], dtype=float)
    return out


def load_labber(path):
    """優先使用 labber_toolkit（若能接收路徑），否則用 h5py 備援"""
    try:
        import labber_toolkit as LT
        if len(inspect.signature(LT.load_labber_data).parameters) > 0:
            d = LT.load_labber_data(path)
            if isinstance(d, dict) and "frequency" in d and "s_params" in d:
                d.setdefault("step_channels", {})
                return d
    except Exception:
        pass
    return load_labber_h5(path)


def load_csv(path):
    """CSV/TXT：欄位 = 頻率, 實部, 虛部（頻率 < 1e3 視為 GHz）"""
    with open(path, "r", encoding="utf-8-sig") as fh:
        sample = fh.read(2048)
    delim = "," if "," in sample else ("\t" if "\t" in sample else None)
    raw = np.genfromtxt(path, delimiter=delim, comments="#", invalid_raise=False)
    raw = raw[~np.isnan(raw).any(axis=1)]
    if raw.ndim != 2 or raw.shape[1] < 3:
        raise ValueError("CSV 需至少三欄：頻率, 實部, 虛部")
    freq = raw[:, 0]
    if np.nanmax(np.abs(freq)) < 1e3:
        freq = freq * 1e9
    s = raw[:, 1] + 1j * raw[:, 2]
    return {"frequency": freq, "s_params": {"S": s[:, None]}, "step_channels": {}}


def load_dataset(path):
    """
    讀檔並整理成統一格式，同時預先算好 dB（耗時工作都在這裡，交給背景執行緒）
    回傳:
        frequency : (N,) Hz
        s_params  : {name: (N, M) complex}
        s_db      : {name: (N, M) float}
        step_channels : {name: (M,) float}  （只保留長度等於 M 的軸，會變動的排前面）
        n_steps   : M
    """
    if path.lower().endswith((".hdf5", ".h5")):
        d = load_labber(path)
    else:
        d = load_csv(path)

    sp = {}
    for k, v in d["s_params"].items():
        v = np.asarray(v)
        sp[k] = v[:, None] if v.ndim == 1 else v
    n_steps = next(iter(sp.values())).shape[1]

    s_db = {}
    with np.errstate(divide="ignore"):
        for k, v in sp.items():
            s_db[k] = (20 * np.log10(np.abs(v))).astype(np.float32)

    axes = {k: np.asarray(v, float) for k, v in d.get("step_channels", {}).items()
            if len(np.asarray(v)) == n_steps}
    axes = dict(sorted(axes.items(),
                       key=lambda kv: (np.ptp(kv[1]) == 0, "current" not in kv[0].lower())))

    return {
        "path": path,
        "frequency": np.asarray(d["frequency"], dtype=float),
        "s_params": sp,
        "s_db": s_db,
        "step_channels": axes,
        "n_steps": n_steps,
    }
