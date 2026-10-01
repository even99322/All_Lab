"""命令列前端。

    python -m labcontrol list
    python -m labcontrol check  [--sim]
    python -m labcontrol run LAB/experiments/dc_vna_sweep.yaml [--sim] [--auto-resume]
    python -m labcontrol run LAB/templates/2d_file_per_outer.scheme.yaml --sim   （量測方案檔也可以）
    python -m labcontrol get DC3.level
    python -m labcontrol set DC3.level 0.1586        （超過 max_jump 會自動走斜坡）
    python -m labcontrol unlock VNA1                  （舊 unlock_vna.py）
    python -m labcontrol resources                    （列出 VISA 資源並用 *IDN? 辨識）
    python -m labcontrol test DC1 [--write-same] [--acquire] [--step 1e-6]   （驅動測試，報告存到 LAB/logs）
    python -m labcontrol node [--name QEL-PC]         （量測節點，不開視窗；Ctrl+C 結束）
    python -m labcontrol nodes [--update]             （列出 Hub 上的節點與電腦；--update 請比較舊的節點更新）
    python -m labcontrol hub [--port 8765]            （在這台電腦執行 Lab Control Hub）

執行中可輸入：p=暫停  r=繼續  b=退回上一點  s=中斷（按 Enter 送出）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from pathlib import Path

from ..core.registry import DRIVERS, HOOKS, PROCEDURES, READERS, WRITERS
from ..core.station import Station
from ..core.units import parse_quantity
from ..measure.experiment import Experiment
from ..measure.runner import State

DEFAULT_LAB = os.environ.get("LABCONTROL_LAB")   # None → LAB/instruments.yaml


def _station(args) -> Station:
    return Station.from_lab(args.lab, simulate=True if args.sim else None)


def _console(bus, quiet_progress: bool = False) -> None:
    def on_log(topic, p):
        print(p["message"], flush=True)

    def on_progress(topic, p):
        if quiet_progress:
            return
        i, n, eta = p["index"], p["total"], p["eta"]
        if n and (i % max(1, n // 20) == 0 or i == n):
            eta_s = "--:--" if eta is None else f"{int(eta // 60):02d}:{int(eta % 60):02d}"
            sp = ", ".join(f"{k}={v}" for k, v in p.get("display", p["setpoints"]).items())
            print(f"  [{i}/{n}] {sp}  ETA {eta_s}", flush=True)

    bus.subscribe("log", on_log)
    bus.subscribe("run.progress", on_progress)


def cmd_init(args) -> int:
    """建立 / 補齊 LAB 設定資料夾（不覆寫既有檔案）。"""
    from ..paths import ensure_lab_home, lab_home

    created = ensure_lab_home()
    print(f"LAB 資料夾：{lab_home()}")
    for p in created:
        print(f"  + {p}")
    if not created:
        print("  （所有預設檔都已存在，沒有變更）")
    return 0


def cmd_resources(args) -> int:
    from ..diagnostics import scan_resources

    def show(r):
        sug = f"  → 建議驅動：{', '.join(r.suggested)}" if r.suggested else ""
        print(f"{r.address:45s} {r.idn or ('（' + r.error + '）' if r.error else '')}{sug}", flush=True)
    try:
        res = scan_resources(identify=not args.no_idn, include_serial=args.serial, progress=show)
    except Exception as e:  # noqa: BLE001
        print(f"❌ 無法列出 VISA 資源：{e}\n   請安裝 NI-VISA / Keysight IO Libraries；或 pip install pyvisa-py，"
              "並在 settings.yaml 設 server.visa_backend: \"@py\"")
        return 1
    if not res:
        print("（沒有找到 VISA 資源）")
    return 0


def cmd_test(args) -> int:
    from ..diagnostics import ICON, run_driver_test
    from ..paths import lab_path
    from ..core.units import parse_quantity

    st = _station(args)
    names = list(st.instruments) if args.name == "all" else [args.name]
    step = parse_quantity(args.step) if args.step else None
    rc = 0
    for n in names:
        print(f"== {n} ==", flush=True)
        rep = run_driver_test(st, n, write_same=args.write_same, acquire=args.acquire, output_step=step,
                              progress=lambda it: print(f"  {ICON[it.status]} [{it.section}] {it.name}  {it.value}"
                                                         f"{'  ← ' + it.detail if it.detail else ''}", flush=True))
        path = rep.save(lab_path("logs"))
        print(f"  {rep.summary()}\n  報告：{path}", flush=True)
        rc |= 0 if rep.ok else 1
    st.close()
    return rc


def cmd_node(args) -> int:
    import time

    from ..remote import NodeService, find_hub

    st = _station(args)
    hub = find_hub()
    done = threading.Event()
    node = NodeService(st, hub, name=args.name, on_restart=done.set)
    _console(st.bus, quiet_progress=False)
    node.start()
    print(f"🛰 節點 {node.name} 上線：{hub.url}（Ctrl+C 結束）", flush=True)
    try:
        while not done.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    from ..measure.runner import Runner
    Runner.stop_all()
    node.stop()
    st.close()
    return 0


def cmd_nodes(args) -> int:
    from ..remote import auto_update_nodes, find_hub

    hub = find_hub()
    s = hub.state()
    print(f"Hub {hub.url}（v{s.get('version')}）　網頁：{hub.dashboard_url}")
    print("量測節點：")
    for n in s.get("nodes") or []:
        run = n.get("run") or {}
        prog = f"  {run.get('name')} {run.get('index')}/{run.get('total')}（{run.get('by')}）" if run else ""
        print(f"  {'●' if n.get('online') else '○'} {n.get('name'):20s} v{n.get('version')}  {n.get('state'):9s} "
              f"{n.get('user')}@{n.get('host')}{prog}")
    print("所有電腦：")
    for d in s.get("devices") or []:
        print(f"  {'●' if d.get('online') else '○'} {d.get('id'):28s} v{d.get('version')}  "
              f"{'節點 ' + str(d.get('node')) if d.get('role') == 'node' else '控制端'}")
    if args.update:
        for k, v in auto_update_nodes(hub).items():
            print(f"更新要求 → {k}：{v}")
    return 0


def cmd_hub(args) -> int:
    """在這台電腦執行 Lab Control Hub（通常放在 NAS 的 Docker；沒有 Docker 時可放在常開的電腦）。"""
    import sys as _sys
    from pathlib import Path as _P

    root = _P(__file__).resolve().parents[2]
    if str(root) not in _sys.path:
        _sys.path.insert(0, str(root))
    from labhub.server import main as hub_main

    from ..paths import lab_path

    argv = ["--port", str(args.port), "--data", args.data or str(lab_path("hub"))]
    for k in ("releases", "token"):
        if getattr(args, k):
            argv += [f"--{k}", getattr(args, k)]
    return hub_main(argv)


def cmd_list(args) -> int:
    for title, reg in (("drivers", DRIVERS), ("procedures", PROCEDURES), ("hooks", HOOKS),
                       ("writers", WRITERS), ("readers", READERS)):
        print(f"{title:11s}: {', '.join(reg.names())}")
    return 0


def cmd_check(args) -> int:
    st = _station(args)
    ok = True
    for name in st.instruments:
        try:
            st.connect([name])
            inst = st.instruments[name]
            extra = ""
            src = Station._sole_source(inst) or (inst if hasattr(inst, "get_level") else None)
            if src is not None:
                extra = f" level={src.get_level():.7g} {src.unit}"
            print(f"✓ {name:10s} {inst._idn}{extra}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"✗ {name:10s} {e}")
    st.close()
    return 0 if ok else 1


def cmd_get(args) -> int:
    st = _station(args)
    st.ensure_connected([args.ref])
    p = st.parameter(args.ref)
    print(f"{args.ref} = {p.get()} {p.unit}")
    st.close()
    return 0


def cmd_set(args) -> int:
    st = _station(args)
    st.ensure_connected([args.ref])
    p = st.parameter(args.ref)
    p.set(parse_quantity(args.value, p.unit) if p.unit else parse_quantity(args.value))
    print(f"{args.ref} → {p.get()} {p.unit}")
    st.close()
    return 0


def cmd_unlock(args) -> int:
    st = _station(args)
    st.connect([args.name])
    inst = st.instruments[args.name]
    if not hasattr(inst, "recover"):
        print(f"{args.name} 不支援 recover")
        return 1
    inst.recover()
    print(f"✅ {args.name} 已解鎖，面板應恢復連續掃描")
    st.close()
    return 0


def cmd_run(args) -> int:
    st = _station(args)
    _console(st.bus)
    from ..core.config import load_config

    cfg = load_config(args.experiment)
    if "blocks" in cfg:          # 量測方案檔（流程圖編輯器存的 .scheme.yaml）→ 先編譯
        from ..scheme import Scheme, build_catalog, compile_scheme

        res = compile_scheme(Scheme.from_dict(cfg), build_catalog(st))
        for i in res.issues:
            print({"error": "✖", "warning": "⚠", "info": "ℹ"}[i.level], i.message)
        if not res.ok:
            return 2
        exp = res.experiment(st)
    else:
        exp = Experiment(st, cfg)
    for h in args.enable_hook or []:
        for hc in exp.hook_configs:
            if hc.get("type") == h:
                hc["enabled"] = True
    out = exp.plan_output(root=args.root, file_name=args.file)
    runner = exp.create_runner(out)

    if args.auto_resume:
        def auto(topic, p):
            if p["state"] == State.PAUSED.value:
                threading.Timer(0.2, runner.resume).start()
        st.bus.subscribe("run.state", auto)
    elif sys.stdin and sys.stdin.isatty():
        def keyboard():
            keys = {"p": runner.pause, "r": runner.resume, "b": runner.rollback, "s": runner.stop}
            for line in sys.stdin:
                fn = keys.get(line.strip().lower()[:1])
                if fn:
                    fn()
                if not runner.is_active:
                    break
        threading.Thread(target=keyboard, daemon=True).start()
        print("指令：p=暫停 r=繼續 b=退回上一點 s=中斷（Enter 送出）")

    try:
        runner.start()
        while not runner.wait(0.2):
            pass
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，安全中斷中（斜坡回停靠點）…")
        runner.stop()
        runner.wait()
    ds = runner.dataset
    if ds is not None:
        print(ds.summary())
        if out.raw_path:
            print(f"raw: {out.raw_path}")
        if not args.no_export and len(ds):
            try:
                exp.export(ds, out)
            except Exception as e:  # noqa: BLE001
                print(f"❌ 匯出失敗：{e}")
                return 2
    st.close()
    return 0 if runner.state == State.FINISHED else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="labcontrol", description="Lab Control 命令列工具")
    ap.add_argument("--lab", default=DEFAULT_LAB, help="儀器清單（預設 LAB/instruments.yaml 或 $LABCONTROL_LAB）")
    ap.add_argument("--sim", action="store_true", help="使用模擬儀器")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="建立 / 補齊 LAB 設定資料夾").set_defaults(fn=cmd_init)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    p = sub.add_parser("get"); p.add_argument("ref"); p.set_defaults(fn=cmd_get)
    p = sub.add_parser("set"); p.add_argument("ref"); p.add_argument("value"); p.set_defaults(fn=cmd_set)
    p = sub.add_parser("unlock"); p.add_argument("name"); p.set_defaults(fn=cmd_unlock)
    p = sub.add_parser("resources", help="列出 VISA 資源並用 *IDN? 辨識")
    p.add_argument("--no-idn", action="store_true", help="只列位址，不送 *IDN?")
    p.add_argument("--serial", action="store_true", help="也對序列埠送 *IDN?")
    p.set_defaults(fn=cmd_resources)
    p = sub.add_parser("node", help="以量測節點執行（不開視窗）")
    p.add_argument("--name", help="節點名稱（預設 settings.yaml remote.node.name 或電腦名稱）")
    p.set_defaults(fn=cmd_node)
    p = sub.add_parser("nodes", help="列出 Hub 上的量測節點與電腦")
    p.add_argument("--update", action="store_true", help="請版本比較舊的節點更新到這台的版本")
    p.set_defaults(fn=cmd_nodes)
    p = sub.add_parser("hub", help="在這台電腦執行 Lab Control Hub（NAS 中繼網站）")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--data", help="資料夾（預設 LAB/hub）")
    p.add_argument("--releases", help="Lab APP 發佈資料夾（節點更新用）")
    p.add_argument("--token", help="存取 token（預設讀 data/token.txt，沒有就產生）")
    p.set_defaults(fn=cmd_hub)
    p = sub.add_parser("test", help="驅動測試（預設只讀取；報告存到 LAB/logs）")
    p.add_argument("name", help="儀器名稱，或 all")
    p.add_argument("--write-same", action="store_true", help="把每個可寫參數設成目前的值（驗證設定指令，不改變狀態）")
    p.add_argument("--acquire", action="store_true", help="量測儀器觸發一次量測")
    p.add_argument("--step", help="電源小幅度輸出測試的步進（例如 1uA）；輸出關閉時略過")
    p.set_defaults(fn=cmd_test)
    p = sub.add_parser("run")
    p.add_argument("experiment")
    p.add_argument("--root", help="覆寫資料根目錄")
    p.add_argument("--file", help="覆寫檔名")
    p.add_argument("--no-export", action="store_true")
    p.add_argument("--auto-resume", action="store_true", help="被 hook 暫停時自動繼續（無人值守/測試用）")
    p.add_argument("--enable-hook", action="append", help="啟用指定 hook（可重複）")
    p.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    bus_logger = logging.getLogger("labcontrol.bus")   # 已由 _console 印出，避免重複
    bus_logger.addHandler(logging.NullHandler())
    bus_logger.propagate = False
    if args.fn is not cmd_init:
        if args.lab is None:
            from ..paths import ensure_lab_home
            ensure_lab_home()          # 第一次使用：建立 LAB 資料夾與預設檔
        elif not Path(args.lab).exists():
            print(f"找不到 {args.lab}（用 --lab 指定）")
            return 2
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
