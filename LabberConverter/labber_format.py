"""Labber 1.8.6 data-log layout, based on the local Generic/API log sample.

These are data logs for Log Browser / Log Viewer, not executable measurements.
Keep enum metadata and UTF-8 variable strings: plain NumPy object/int fields
alone do not reproduce the original on-disk channel/configuration schema.
"""
from __future__ import annotations

import h5py
import numpy as np

TEXT = h5py.string_dtype("utf-8")


def enum(values):
    return h5py.enum_dtype(values, basetype="<i2")


CHANNEL = np.dtype([
    ("name", TEXT), ("instrument", TEXT), ("quantity", TEXT),
    ("unitPhys", TEXT), ("unitInstr", TEXT), ("gain", "<f8"),
    ("offset", "<f8"), ("amp", "<f8"), ("highLim", "<f8"),
    ("lowLim", "<f8"), ("outputChannel", TEXT), ("limit_action", TEXT),
    ("limit_run_script", "?"), ("limit_script", TEXT),
    ("use_log_interval", "?"), ("log_interval", "<f8"),
    ("limit_run_always", "?"),
])
STEP = np.dtype([
    ("channel_name", TEXT),
    ("step_unit", enum({"Instrument": 0, "Physical": 1})),
    ("wait_after", "<f8"),
    ("after_last", enum({"Goto first point": 0, "Stay at final": 1, "Goto value...": 2})),
    ("final_value", "<f8"), ("use_relations", "?"), ("equation", TEXT),
    ("show_advanced", "?"),
    ("sweep_mode", enum({"Off": 0, "Between points": 1, "Continuous": 2})),
    ("use_outside_sweep_rate", "?"), ("sweep_rate_outside", "<f8"),
    ("alternate_direction", "?"),
])
STEP_ITEM = np.dtype([
    ("range_type", enum({"Single": 0, "Start - Stop": 1, "Center - Span": 2})),
    ("step_type", enum({"Fixed step": 0, "Fixed # of pts": 1})),
    ("single", "<f8"), ("start", "<f8"), ("stop", "<f8"),
    ("center", "<f8"), ("span", "<f8"), ("step", "<f8"),
    ("n_pts", "<i4"),
    ("interp", enum({"Linear": 0, "Log": 1, "Log, #/decade": 2, "Lorentzian": 3})),
    ("sweep_rate", "<f8"),
])
RELATION = np.dtype([("variable", TEXT), ("channel_name", TEXT), ("use_lookup", "?")])
CHANNEL_NAME = np.dtype([("name", TEXT), ("info", TEXT)])
LOG = np.dtype([("channel_name", TEXT)])
INSTRUMENT = np.dtype([
    ("hardware", TEXT), ("version", TEXT), ("id", TEXT), ("model", TEXT),
    ("name", TEXT),
    ("interface", enum({"GPIB": 0, "TCPIP": 1, "USB": 2, "PXI": 3,
                        "Serial": 4, "VISA": 5, "Other": 6, "None": 7})),
    ("address", TEXT), ("server", TEXT),
    ("startup", enum({"Set config": 0, "Get config": 1, "Do nothing": 2})),
    ("lock", "?"), ("show_advanced", "?"), ("Timeout", "<f8"),
    ("Term. character", TEXT), ("Send end on write", "?"),
    ("Lock VISA resource", "?"), ("Suppress end bit termination on read", "?"),
    ("Use specific TCP port", "?"), ("TCP port", "<f8"),
    ("Use VICP protocol", "?"), ("Baud rate", "<f8"), ("Data bits", "<f8"),
    ("Stop bits", "<f8"), ("Parity", TEXT), ("GPIB board number", "<f8"),
    ("Send GPIB go to local at close", "?"), ("PXI chassis", "<f8"),
    ("Run in 32-bit mode", "?"),
])


def rows(dtype, records):
    result = np.zeros(len(records), dtype=dtype)
    for field in dtype.names:
        if dtype[field].kind == "O":
            result[field] = ""
    for row, values in zip(result, records):
        for key, value in values.items():
            row[key] = value
    return result


def instrument_id(name):
    return f"Generic - GPIB: , {name} at localhost"


def write_configuration(file, axis_name, axis_unit, axis, trace_specs):
    step_names = ["Step index API", axis_name]
    channel_records = []
    for name, unit, group in [(n, u, "Step channels") for n, u in
                              zip(step_names, ["", axis_unit])] + [
                                  (t["name"], t["unit"], "Log channels") for t in trace_specs]:
        channel_records.append(dict(name=name, quantity=name, instrument=instrument_id(group),
                                    unitPhys=unit, unitInstr=unit, gain=1., amp=1.,
                                    highLim=np.inf if group == "Step channels" else 0.,
                                    lowLim=-np.inf if group == "Step channels" else 0.,
                                    limit_action="Nothing", log_interval=1.))
    file.create_dataset("Channels", data=rows(CHANNEL, channel_records))
    file.create_dataset("Step list", data=rows(STEP, [dict(channel_name=n, equation="x") for n in step_names]))
    file.create_dataset("Log list", data=rows(LOG, [dict(channel_name=t["name"]) for t in trace_specs]))
    file.create_dataset("Instruments", data=rows(INSTRUMENT, [
        dict(hardware="Generic", version="1.0", id=instrument_id(n), name=n,
             Timeout=10., **{"Term. character": "Auto", "Send end on write": True,
                             "Baud rate": 9600., "Data bits": 8., "Stop bits": 1.,
                             "Parity": "No parity", "PXI chassis": 1.})
        for n in ["Step channels", "Log channels"]
    ]))
    configs = file.create_group("Instrument config")
    step_inst = configs.create_group(instrument_id("Step channels"))
    log_inst = configs.create_group(instrument_id("Log channels"))
    for group in [step_inst, log_inst]:
        group.attrs["Installed options"] = np.array([], dtype=float)
    for name in step_names:
        step_inst.attrs[name] = 0.
    for trace in trace_specs:
        name = trace["name"]
        log_inst.attrs[name] = np.array([], dtype=complex if trace["complex"] else float)
        log_inst.attrs[f"___{name}___x_name"] = trace["x_name"]
        log_inst.attrs[f"___{name}___x_unit"] = trace["x_unit"]
    file.create_group("Settings")
    steps = file.create_group("Step config")
    for name, values in [("Step index API", np.array([1.])), (axis_name, axis)]:
        cfg = steps.create_group(name)
        values = np.asarray(values, dtype=float)
        finite = values[np.isfinite(values)]          # 0.4.2: point layout can hold unmeasured (NaN) rows
        values = finite if len(finite) else np.array([0.])
        start, stop = float(values[0]), float(values[-1])
        cfg.create_dataset("Step items", data=rows(STEP_ITEM, [dict(
            range_type=1 if len(values) > 1 else 0, step_type=1, single=start,
            start=start, stop=stop, center=(start + stop) / 2, span=stop - start,
            step=float(values[1]-values[0]) if len(values)>1 else 0., n_pts=len(values),
        )]))
        cfg.create_dataset("Relation parameters", data=rows(RELATION, [
            dict(variable="x", channel_name="Step values")]))
        opt = cfg.create_group("Optimizer")
        opt.attrs.update({"Enabled": False, "Initial step size": abs(stop-start)*.2,
                          "Max value": max(start, stop), "Min value": min(start, stop),
                          "Precision": max(abs(stop-start)*1e-4, 1e-12), "Start value": start})


def write_view(file, axis_name, axis, trace):
    views = file.create_group("Views")
    views.attrs["selected_view"] = "Current view"
    view = views.create_group("Current view")
    view.attrs.update({
        "auto_rotate": False, "autoscale_x": True, "autoscale_y": True, "autoscale_z": True,
        "bins": np.int32(50), "channel3": axis_name,
        "channel_enabled": np.array([True]*4),
        "channel_graph": np.array(["", "", "0", ""], dtype=TEXT),
        "colormap": "Red-White-Blue", "cross_section_x": False, "cross_section_y": False,
        "cursor_1_type": "Off", "cursor_1_x": 0., "cursor_1_y": 0.,
        "cursor_2_type": "Off", "cursor_2_x": 0., "cursor_2_y": 0.,
        "data_op": "None", "data_op3": "None", "dim3": np.int32(2), "dimX": np.int32(0),
        "fixed_rotation": 0., "ignore_x_image": False, "image_mode": len(axis)>1,
        "image_selection": np.array([0], dtype=np.int32), "instrument_units": False,
        "invert_colormap": True, "list_type": "Log list", "multi_graph": False,
        "multi_sel_dimensions": np.array([1, 1], dtype=np.int32),
        "multi_selection": np.array([0, 0], dtype=np.int32), "multi_x_index": np.int32(0),
        "plot_db": bool(trace["complex"]), "rotation_vs_x": 0.,
        "selected_items": np.array([[0, 0, 0, 0]], dtype=np.int32),
        "show_contrast": False, "smooth": np.int32(1), "smooth3": np.int32(1),
        "trace_op": "Show individual", "trace_selector": False, "transpose": False,
        "unwrap": False, "use_eq": False, "x_channel": trace["name"], "x_eq": "x",
        "x_lim": np.array([trace["x"][0], trace["x"][-1]]), "x_log": False, "x_opt": "vsX",
        "y_channel": trace["name"], "y_eq": "y",
        "y_lim": np.array([np.nanmin(axis), np.nanmax(axis)]) if np.isfinite(axis).any() else np.array([0., 1.]),
        "y_log": False, "y_opt": "Magnitude",
        "z_lim": np.array([-100., 0.]),
    })
