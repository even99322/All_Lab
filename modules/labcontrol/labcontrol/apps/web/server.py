"""Web 前端：舊 web_yoko.py 的網頁（原封不動）＋ 新的 REST API，後端改成共用 Station。

差異：
  * 儀器清單來自 LAB/instruments.yaml（所有 Source，不限 Yokogawa）。
  * 與量測共用同一個 Station：同一台儀器只開一次 VISA，不再互相搶。
  * 量測進行中，被量測使用的儀器會被 lease：網頁仍可看到即時電流，但寫入會回 409。
  * 寫入經過 Source 的安全限制（上下限、超過 max_jump 自動斜坡）。

啟動：
    python -m labcontrol.apps.web.server [--lab instruments.yaml] [--sim] [--port 5000]
或在 Qt / CLI 程式內與量測共用 Station：
    serve_in_thread(station, port=5000)
"""
from __future__ import annotations

import argparse
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...core.capabilities import Source
from ...core.errors import InstrumentBusy, LabControlError
from ...core.instrument import Instrument
from ...core.station import Station

log = logging.getLogger(__name__)
TEMPLATE = Path(__file__).with_name("templates") / "yoko_panel.html"


class PanelState:
    """網頁面板的狀態：哪些儀器顯示在面板上、背景輪詢的快取值。"""

    def __init__(self, station: Station, poll_interval: Optional[float] = None) -> None:
        if poll_interval is None:
            from ...settings import setting
            poll_interval = float(setting("web.poll_interval_s", 1.5))
        self.station = station
        self.poll_interval = poll_interval
        self.polling = True
        self.active: List[str] = []
        self.values: Dict[str, float] = {}
        self.last_set: Dict[str, float] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="web-poll")
        self._thread.start()

    # 邏輯 id：單通道 = 儀器名；多通道 = 名稱_CHn（與舊網頁相同）
    def channels(self) -> List[Tuple[str, str, Source]]:
        out = []
        for name in self.active:
            inst = self.station.instruments.get(name)
            if inst is None or not inst.connected:
                continue
            srcs = [(k, ch) for k, ch in inst.channels.items() if isinstance(ch, Source)]
            if not srcs and isinstance(inst, Source):
                srcs = [("", inst)]
            for k, src in srcs:
                lid = f"{name}_CH{k[2:]}" if len(srcs) > 1 else name
                out.append((lid, name, src))
        return out

    def find(self, lid: str) -> Optional[Source]:
        return next((s for i, _, s in self.channels() if i == lid), None)

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            if self.polling:
                for lid, _, src in self.channels():
                    if time.time() - self.last_set.get(lid, 0) < 1.5:
                        continue
                    try:
                        self.values[lid] = src.get_level()
                    except Exception:  # noqa: BLE001
                        pass
            self._stop.wait(self.poll_interval)

    def close(self) -> None:
        self._stop.set()


def _sources_in_config(station: Station) -> Dict[str, Dict[str, str]]:
    out = {}
    for name, inst in station.instruments.items():
        if any(isinstance(ch, Source) for ch in inst.channels.values()) or isinstance(inst, Source):
            out[name] = {"address": inst.options.get("address", ""), "type": inst.driver_name.split(".")[-1].upper()}
    return out


def create_app(station: Station, panel: Optional[PanelState] = None):
    from flask import Flask, jsonify, render_template_string, request

    app = Flask(__name__)
    panel = panel or PanelState(station)
    app.config["panel"] = panel
    html = TEMPLATE.read_text(encoding="utf-8")

    def _range_of(src: Source) -> float:
        r = getattr(src, "range", None)
        return float(r) if r else (src.limits.hi if src.limits.hi != float("inf") else 0.2)

    # ---- 舊網頁相容路由 --------------------------------------------------------
    @app.route("/")
    def index():
        dcs = []
        for lid, name, src in panel.channels():
            inst: Instrument = station.instruments[name]
            dcs.append({"id": lid, "name": lid.replace("_CH", " (CH") + (")" if "_CH" in lid else ""),
                        "model": inst.driver_name.split(".")[-1].upper(), "range_a": _range_of(src),
                        "current_a": panel.values.get(lid, 0.0)})
        return render_template_string(html, dcs=dcs, known_devices=_sources_in_config(station),
                                      active_dcs=panel.active, global_polling=panel.polling)

    @app.route("/api/connect", methods=["POST"])
    def api_connect():
        selected = [n for n in request.json.get("devices", []) if n in station.instruments]
        errors = {}
        for n in selected:
            try:
                station.connect([n])
            except Exception as e:  # noqa: BLE001
                errors[n] = str(e)
        # 不關閉未勾選的儀器（可能正被量測使用），只是不顯示
        panel.active = [n for n in selected if n not in errors]
        for lid, _, src in panel.channels():
            try:
                panel.values[lid] = src.get_level()
            except Exception:  # noqa: BLE001
                pass
        return jsonify({"status": "success" if not errors else "partial", "errors": errors})

    @app.route("/api/set_polling", methods=["POST"])
    def api_set_polling():
        panel.polling = bool(request.json.get("enabled", True))
        return jsonify({"status": "ok"})

    @app.route("/api/set_current", methods=["POST"])
    def api_set_current():
        lid, val = request.json.get("dc_id"), float(request.json.get("value"))
        src = panel.find(lid)
        if src is None:
            return jsonify({"status": "error", "message": f"unknown {lid}"}), 404
        panel.last_set[lid] = time.time()
        try:
            src.set_level(val)
            panel.values[lid] = val
        except InstrumentBusy as e:
            return jsonify({"status": "busy", "message": str(e)}), 409
        except LabControlError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        return jsonify({"status": "ok"})

    @app.route("/api/get_currents")
    def api_get_currents():
        return jsonify({lid: panel.values.get(lid, 0.0) for lid, _, _ in panel.channels()})

    # ---- 新 REST API（給新網頁 / 其他程式 / logview 使用）----------------------------
    @app.route("/api/v1/instruments")
    def v1_instruments():
        return jsonify(station.describe())

    @app.route("/api/v1/param/<path:ref>", methods=["GET", "POST"])
    def v1_param(ref: str):
        try:
            p = station.parameter(ref)
            if request.method == "POST":
                p.set(request.json["value"])
            return jsonify({"ref": ref, "value": p.get(), "unit": p.unit})
        except InstrumentBusy as e:
            return jsonify({"error": str(e)}), 409
        except LabControlError as e:
            return jsonify({"error": str(e)}), 400

    return app


def serve_in_thread(station: Station, host: str = "0.0.0.0", port: int = 5000) -> threading.Thread:
    """在背景執行緒啟動網頁面板，與同一程式內的量測共用 Station。"""
    app = create_app(station)
    t = threading.Thread(target=lambda: app.run(host=host, port=port, debug=False, threaded=True,
                                                use_reloader=False), daemon=True, name="web-panel")
    t.start()
    return t


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    from ...settings import setting

    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", default=None, help="儀器清單（預設 LAB/instruments.yaml）")
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--host", default=setting("web.host", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(setting("web.port", 5000)))
    a = ap.parse_args(argv)
    st = Station.from_lab(a.lab, simulate=True if a.sim else None)
    print(f"\n🌐 網頁面板：http://<本機IP>:{a.port}")
    create_app(st).run(host=a.host, port=a.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
