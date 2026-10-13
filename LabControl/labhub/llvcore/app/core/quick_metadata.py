"""What was measured, at a glance (v2.1.3): the Browser's Metadata pane next to the Comment.

``summarize(experiment)`` returns sections of (label, value) rows, built only from the parsed model:

* Sweep:   every swept step channel — start → stop, number of points, step;
* VNA:     frequency range (start–stop or center ± span), points, IF bandwidth, averages, power,
           for every instrument that has frequency settings (Labber names "Start frequency",
           "# of points", "IF bandwidth", "# of averages"; Lab Control .nc snapshot names
           start_freq, points, if_bw, averages ...). Without instrument settings, the trace's own
           frequency axis is used;
* Sources: DC sources that are NOT swept — their level and output state (swept ones are in Sweep);
* Data:    traces × entries, creation time.

Labels are keys of the localizer (``meta.*``); values are already formatted strings.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

import numpy as np

Section = tuple[str, list[tuple[str, str]]]

_PREFIX = {12: "T", 9: "G", 6: "M", 3: "k", 0: "", -3: "m", -6: "µ", -9: "n"}


def si(value: float, unit: str = "", digits: int = 6) -> str:
    """5.0198e9, "Hz" → "5.0198 GHz"; 0.0015, "A" → "1.5 mA"; plain numbers without a unit."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(value):
        return str(value)
    if not unit or unit in ("dBm", "dB", "deg", "°", "%"):
        return f"{value:.{digits}g}{(' ' + unit) if unit else ''}"
    if value == 0:
        return f"0 {unit}"
    exponent = int(math.floor(math.log10(abs(value)) / 3) * 3)
    exponent = max(-9, min(12, exponent))
    return f"{value / 10 ** exponent:.{digits}g} {_PREFIX[exponent]}{unit}"


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9#]+", " ", str(key).lower()).strip()


def _parameters(config: dict) -> dict[str, tuple[Any, str]]:
    """{normalised name: (value, unit)} for one instrument (Labber attrs or Lab Control snapshot)."""
    out: dict[str, tuple[Any, str]] = {}
    units: dict[str, str] = {}
    for key, value in config.items():
        parts = [p.strip() for p in str(key).split(" / ")]
        if len(parts) == 3 and parts[0] == "parameters":            # Lab Control: parameters / name / value|unit
            if parts[2] == "value":
                out[_norm(parts[1])] = (value, units.get(_norm(parts[1]), ""))
            elif parts[2] == "unit":
                units[_norm(parts[1])] = str(value or "")
                if _norm(parts[1]) in out:
                    out[_norm(parts[1])] = (out[_norm(parts[1])][0], str(value or ""))
        elif len(parts) == 1:
            out.setdefault(_norm(key), (value, ""))
    return out


_VNA_KEYS = {
    "start": ("start freq", "start frequency"),
    "stop": ("stop freq", "stop frequency"),
    "center": ("center freq", "center frequency", "centre frequency"),
    "span": ("span", "frequency span"),
    "points": ("points", "# of points", "number of points", "npts", "sweep points"),
    "ifbw": ("if bw", "if bandwidth", "ifbw", "resolution bandwidth"),
    "averages": ("averages", "# of averages", "average count", "averaging count"),
    "average_on": ("average", "averaging"),
    "power": ("power", "output power", "source power"),
}
_SOURCE_KEYS = ("level", "current", "voltage", "output level", "source level", "dc current", "dc voltage")


def _pick(params: dict, names: tuple[str, ...]):
    for name in names:
        if name in params:
            return params[name]
    return None


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _on(value) -> bool | None:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    number = _number(value)
    if number is not None:
        return number != 0
    text = str(value).strip().lower()
    return True if text in ("on", "true") else False if text in ("off", "false") else None


def _sweep_rows(experiment) -> list[tuple[str, str]]:
    rows = []
    for axis in getattr(experiment, "step_axes", None) or []:
        values = np.asarray(axis.values, dtype=float).ravel()
        values = values[np.isfinite(values)]
        if not values.size:
            continue
        unit = axis.channel.unit or ""
        text = f"{values[0]:.8g} → {values[-1]:.8g} {unit}".rstrip()
        parts = [text, f"{values.size} pts"]
        if values.size > 1:
            steps = np.diff(values)
            if np.allclose(steps, steps[0], rtol=1e-6, atol=0):
                parts.append(f"step {steps[0]:.6g}")
        rows.append((axis.channel.name, " · ".join(parts)))
    if any(a.channel.name == "Point" for a in getattr(experiment, "step_axes", None) or []):
        # point layout: what was set at each point is recorded per point (one scalar channel per axis)
        for channel in (getattr(experiment, "channels", None) or {}).values():
            if not (getattr(channel, "is_log", False) and not getattr(channel, "is_vector", False)
                    and channel.instrument):
                continue
            try:
                values = np.asarray(experiment.get_data(channel.name), dtype=float).ravel()
            except Exception:
                continue
            values = values[np.isfinite(values)]
            if values.size:
                rows.append((channel.name, f"{values[0]:.8g} → {values[-1]:.8g} {channel.unit or ''}".rstrip()
                             + f" · {values.size} pts"))
    return rows


def _swept_instruments(experiment) -> set[str]:
    swept = {str(a.channel.instrument) for a in getattr(experiment, "step_axes", None) or [] if a.channel.instrument}
    # point layout (Lab Control .nc / .lm.h5): the swept values are recorded per point as scalar log channels
    for channel in (getattr(experiment, "channels", None) or {}).values():
        if getattr(channel, "is_log", False) and not getattr(channel, "is_vector", False) and channel.instrument:
            swept.add(str(channel.instrument))
    return swept


def _vna_rows(experiment, label) -> list[Section]:
    sections: list[Section] = []
    traced = {}
    for name, trace in (getattr(experiment, "vector_traces", None) or {}).items():
        traced.setdefault(str(trace.channel.instrument or ""), []).append((name, trace))
    configs = getattr(experiment, "instrument_config", None) or {}
    done = set()
    for inst, config in configs.items():
        params = _parameters(config)
        found = {key: _pick(params, names) for key, names in _VNA_KEYS.items()}
        if not any(found[k] for k in ("start", "stop", "center", "span")):
            continue
        rows = []
        start, stop = _number((found["start"] or (None,))[0]), _number((found["stop"] or (None,))[0])
        center, span = _number((found["center"] or (None,))[0]), _number((found["span"] or (None,))[0])
        if start is not None and stop is not None:
            if center is None:
                center, span = (start + stop) / 2, stop - start
            rows.append((label("meta.frequency"), f"{si(start, 'Hz')} – {si(stop, 'Hz')}"))
            rows.append((label("meta.center_span"), f"{si(center, 'Hz')} / {si(span, 'Hz')}"))
        elif center is not None:
            rows.append((label("meta.center_span"), f"{si(center, 'Hz')} / {si(span or 0, 'Hz')}"))
        if found["points"] is not None and _number(found["points"][0]) is not None:
            rows.append((label("meta.points"), f"{int(_number(found['points'][0]))}"))
        if found["ifbw"] is not None and _number(found["ifbw"][0]) is not None:
            rows.append((label("meta.ifbw"), si(_number(found["ifbw"][0]), "Hz")))
        averages = _number(found["averages"][0]) if found["averages"] is not None else None
        average_on = _on(found["average_on"][0]) if found["average_on"] is not None else None
        if averages is not None:
            off = average_on is False or averages < 1
            rows.append((label("meta.averages"), label("meta.off") if off else f"{int(averages)}"))
        elif average_on is not None:
            rows.append((label("meta.averages"), label("meta.on") if average_on else label("meta.off")))
        if found["power"] is not None and _number(found["power"][0]) is not None:
            rows.append((label("meta.power"), si(_number(found["power"][0]), found["power"][1] or "dBm")))
        names = [n for n, _t in traced.get(str(inst), [])]
        title = f"{inst}" + (f" ({', '.join(names)})" if names else "")
        sections.append((title, rows))
        done.add(str(inst))
    for inst, traces in traced.items():                      # no settings stored: the trace's own axis
        if inst in done:
            continue
        name, trace = traces[0]
        x = np.asarray(getattr(trace, "x_values", None) if getattr(trace, "x_values", None) is not None else [],
                       dtype=float).ravel()
        if x.size and (trace.x_unit or "").lower() == "hz":
            sections.append((inst or name, [(label("meta.frequency"), f"{si(x[0], 'Hz')} – {si(x[-1], 'Hz')}"),
                                            (label("meta.points"), str(x.size))]))
    return sections


def _source_rows(experiment, label) -> list[tuple[str, str]]:
    rows = []
    swept = _swept_instruments(experiment)
    for inst, config in (getattr(experiment, "instrument_config", None) or {}).items():
        if str(inst) in swept:
            continue
        params = _parameters(config)
        if any(_pick(params, _VNA_KEYS[k]) is not None for k in ("start", "stop", "center")):
            continue                                            # a VNA, already shown
        level = _pick(params, _SOURCE_KEYS)
        if level is None or _number(level[0]) is None:
            continue
        unit = level[1] or ""
        if not unit:
            mode = str((_pick(params, ("source mode", "mode", "function")) or ("",))[0]).lower()
            unit = "V" if "volt" in mode else "A" if ("curr" in mode or "current" in params) else ""
        text = si(_number(level[0]), unit) if unit else f"{_number(level[0]):.6g}"
        output = _pick(params, ("output", "output on", "output state"))
        if output is not None and _on(output[0]) is not None:
            text += f" · {label('meta.on') if _on(output[0]) else label('meta.off')}"
        rows.append((str(inst), text))
    return rows


def summarize(experiment, label=lambda key: key.split(".", 1)[-1]) -> list[Section]:
    sections: list[Section] = []
    sweep = _sweep_rows(experiment)
    sections.append((label("meta.sweep"), sweep or [(label("meta.single"), "—")]))
    sections.extend(_vna_rows(experiment, label))
    sources = _source_rows(experiment, label)
    if sources:
        sections.append((label("meta.sources"), sources))
    data = []
    traces = getattr(experiment, "vector_traces", None) or {}
    if traces:
        trace = next(iter(traces.values()))
        data.append((label("meta.traces"), f"{len(traces)} × {trace.n_entries} × {trace.n_points}"))
    created = getattr(experiment, "creation_time", None)
    if created:
        try:
            data.append((label("meta.created"), datetime.fromtimestamp(float(created)).strftime("%Y-%m-%d %H:%M:%S")))
        except (TypeError, ValueError, OSError):
            pass
    if data:
        sections.append((label("meta.data"), data))
    return sections


def _tr(text: str, translate) -> str:
    return translate(text) if isinstance(text, str) and text.startswith("meta.") else text


def to_html(sections: list[Section], translate=lambda key: key) -> str:
    from html import escape

    parts = ["<table cellspacing='0' cellpadding='1' style='border-collapse:collapse'>"]
    for title, rows in sections:
        parts.append(f"<tr><td colspan='2' style='padding-top:4px'><b>{escape(_tr(title, translate))}</b></td></tr>")
        for key, value in rows:
            value = " · ".join(_tr(v, translate) for v in str(value).split(" · "))
            parts.append(f"<tr><td style='padding-left:8px;padding-right:10px'>{escape(_tr(key, translate))}</td>"
                         f"<td>{escape(value)}</td></tr>")
    parts.append("</table>")
    return "".join(parts)


def to_text(sections: list[Section], translate=lambda key: key) -> str:
    lines = []
    for title, rows in sections:
        lines.append(_tr(title, translate))
        lines += [f"  {_tr(k, translate)}: {' · '.join(_tr(x, translate) for x in str(v).split(' · '))}"
                  for k, v in rows]
    return "\n".join(lines)
