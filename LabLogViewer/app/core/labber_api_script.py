"""Neutral spec (.spec.h5 written by app/core/labber_convert.py) → Labber log file, via the Labber API.

    <Labber python> labber_api_script.py <spec.h5> <out.hdf5>

Run by LabLogViewer in the lab's Labber Python (3.8) environment, the same way Lab Control exports.
★ Stand-alone and Python 3.8 compatible: imports only numpy, h5py and Labber — never `app`.
"""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

import h5py  # noqa: E402
import numpy as np  # noqa: E402

for _name, _val in (("bool", np.bool_), ("float", np.float64), ("int", np.int32), ("complex", np.complex128)):
    if not hasattr(np, _name):          # Labber with newer numpy
        setattr(np, _name, _val)


def read_spec(path):
    with h5py.File(path, "r") as f:
        meta = json.loads(f.attrs["meta"])
        steps = []
        for k, s in enumerate(meta["steps"]):
            s = dict(s)
            s["values"] = f["step_values/%d" % k][()]
            s["entries"] = f["step_entries/%d" % k][()]
            steps.append(s)
        logs = []
        for k, c in enumerate(meta["logs"]):
            c = dict(c)
            c["data"] = f["log_data/%d" % k][()]
            c["x"] = f["log_x/%d" % k][()] if c["vector"] else None
            logs.append(c)
    return meta, steps, logs


class MagicDict(dict):
    """Works around Labber's addEntry looking entries up by channel objects (same as Lab Control)."""

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
            raise KeyError("Labber key not found: %r" % (key,))
        return dict.__getitem__(self, k)

    def __contains__(self, key):
        k = self._k(key)
        return k is not None and dict.__contains__(self, k)


def main(argv):
    spec, out = argv[1], argv[2]
    for p in (r"C:\Program Files\Keysight\Labber\Script", r"C:\Program Files\Labber\Script"):
        if os.path.exists(p):
            sys.path.insert(0, p)
            break
    import Labber  # noqa: E402

    meta, steps, logs = read_spec(spec)
    step_channels = [dict(name=s["name"], unit=s["unit"], values=s["values"]) for s in steps]
    log_channels = []
    for c in logs:
        d = dict(name=c["name"], unit=c["unit"], complex=bool(c["complex"]), vector=bool(c["vector"]))
        if c["vector"]:
            d.update(x_name=c["x_name"] or "x", x_unit=c["x_unit"] or "")
        log_channels.append(d)
    f = Labber.createLogFile_ForData(out, log_channels, step_channels, use_database=False)
    if meta.get("project"):
        f.setProject(meta["project"])
    f.setUser(meta.get("user") or "")
    f.setTags(list(meta.get("tags") or []))
    if meta.get("comment"):
        try:
            f.setComment(meta["comment"])
        except Exception:  # noqa: BLE001 - older Labber API
            pass
    for i in range(int(meta["n"])):
        entry = MagicDict({s["name"]: float(s["entries"][i]) for s in steps})
        for c in logs:
            if c["vector"]:
                x = c["x"]
                entry[c["name"]] = Labber.getTraceDict(c["data"][i], x0=float(x[0]), x1=float(x[-1]))
            else:
                entry[c["name"]] = float(c["data"][i])
        f.addEntry(entry)
    del f

    with h5py.File(out, "r+") as h5:
        h5.attrs["log_name"] = meta.get("log_name") or h5.attrs.get("log_name", "")
        h5.attrs["star"] = bool(meta.get("star"))
        for c in logs:
            if not c["vector"]:
                continue
            x = np.asarray(c["x"], dtype=float)
            tp = "Traces/%s" % c["name"]
            if tp in h5:
                h5[tp].attrs["x, name"] = c["x_name"] or "x"
                h5[tp].attrs["x, unit"] = c["x_unit"] or ""
                if len(x) > 2 and not np.allclose(np.diff(x), x[1] - x[0], rtol=1e-6):
                    h5["Traces"].create_dataset(c["name"] + "_xvalues", data=x)
        inst = h5.require_group("Instrument config")
        for group, items in (meta.get("instrument_config") or {}).items():
            name = group.replace("/", "\u2215")
            if name in inst:
                continue
            g = inst.create_group(name)
            for k, v in items.items():
                try:
                    g.attrs[k.replace("/", "\u2215")] = v
                except Exception:  # noqa: BLE001
                    pass
    print("SUCCESS", flush=True)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print("ERROR: %s" % e, flush=True)
        sys.exit(1)
