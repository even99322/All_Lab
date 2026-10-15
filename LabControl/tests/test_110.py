"""1.0.10：子專案（巢狀）、量測監控的 trace 跟即時數據一致、VNA 只顯示有接線的 port。"""
from __future__ import annotations

import json

from tests.test_0031 import _pump
from tests.test_106 import qapp, window  # noqa: F401

ITEMS = [
    {"id": "1", "name": "YIG", "parent": ""},
    {"id": "2", "name": "Magnon JPA", "parent": ""},
    {"id": "3", "name": "mirror", "parent": "1"},
    {"id": "4", "name": "single sphere", "parent": "1"},
    {"id": "5", "name": "2026 秋 量測", "parent": "3"},
]


def test_subproject_cascade_and_nested_folder(window):
    w = window
    f = w.files
    w.show()
    f._fill_projects(ITEMS)
    cb = f.lab_project
    # 第一層只有最上層的專案（有子專案的標出個數）
    texts = [cb.itemText(i) for i in range(cb.count())]
    assert texts[1].startswith("YIG") and "2 個子專案" in texts[1] and texts[2].startswith("Magnon JPA")
    assert not any("mirror" in t for t in texts)
    assert not f.sub_projects
    # 選 YIG → 右邊多一個選單（只選 YIG / mirror / single sphere / 新增）
    cb.setCurrentIndex(1)
    f._project_chosen(1)
    assert w.doc.scheme.output["lab_project"] == {"id": "1", "name": "YIG"}
    assert len(f.sub_projects) == 1
    sub = f.sub_projects[0]
    assert [sub.itemText(i) for i in range(sub.count())][:3] == ["（只選「YIG」）", "mirror　▸ 1", "single sphere"]
    # 選 mirror → 再多一層；記下整條路徑
    sub.setCurrentIndex(1)
    f._sub_chosen(sub)
    lp = w.doc.scheme.output["lab_project"]
    assert lp == {"id": "3", "name": "mirror", "path": ["YIG", "mirror"], "path_ids": ["1", "3"]}
    assert len(f.sub_projects) == 2 and f.sub_projects[0].currentData()["id"] == "3"
    f.sub_projects[1].setCurrentIndex(1)
    f._sub_chosen(f.sub_projects[1])
    assert w.doc.scheme.output["lab_project"]["path"] == ["YIG", "mirror", "2026 秋 量測"]
    assert len(f.sub_projects) == 2                         # 最底層沒有子專案：不再多一個
    # 存檔資料夾：數據集散地/YIG/mirror/2026 秋 量測/…
    from labcontrol.data.naming import project_folder

    assert project_folder(w.doc.scheme.output) == "YIG/mirror/2026 秋 量測"
    # 重新讀清單（例如重開方案）→ 三層照原本選的
    f._fill_projects(ITEMS)
    assert cb.currentData()["id"] == "1" and [c.currentData()["id"] for c in f.sub_projects] == ["3", "5"]
    # 回到「只選 YIG」
    f.sub_projects[0].setCurrentIndex(0)
    f._sub_chosen(f.sub_projects[0])
    assert w.doc.scheme.output["lab_project"] == {"id": "1", "name": "YIG"} and len(f.sub_projects) == 1
    # 空間不夠：選單換到下一行
    f._fill_projects(ITEMS + [{"id": str(9 + i), "name": "很長的子專案名稱" * 3, "parent": "1"} for i in range(2)])
    f.sub_projects[0].setCurrentIndex(1)
    f._sub_chosen(f.sub_projects[0])
    lay = f.proj_flow_layout
    assert len(lay.rows(10_000)) == 1 and len(lay.rows(200)) >= 2


def test_markers_mark_project_folders(tmp_path):
    from labcontrol.data.naming import MARKER, project_markers, write_markers

    m = project_markers(tmp_path, {"id": "5", "name": "秋", "path": ["YIG", "mirror", "秋"], "path_ids": ["1", "3", "5"]})
    assert [p.relative_to(tmp_path).as_posix() for p, _i in m] == ["YIG", "YIG/mirror", "YIG/mirror/秋"]
    write_markers(m)
    info = json.loads((tmp_path / "YIG" / "mirror" / MARKER).read_text(encoding="utf-8"))
    assert info == {"id": "3", "name": "mirror", "path": ["YIG", "mirror"]}


def test_monitor_uses_the_same_traces(window):
    """量測監控的曲線：跟即時數據一樣的 trace（通道 ＋ 格式），影像畫目前的 trace。"""
    import numpy as np

    from labcontrol.apps.qt.live.traces import TracePlots

    mon = window.monitor
    assert isinstance(mon.traces, TracePlots) and mon.fmtbar.buttons["smith"]
    mon.vec_names = ["S21", "S11"]
    mon.show_ch = "S21"
    mon._restore_traces()
    assert mon.traces.views and all(k.split(":")[0] in mon.vec_names for k in mon.traces.views)
    mon.traces.set_groups([["S21:db"]])
    mon.traces.add_trace("S11", "phase", new_cell=True)
    x = np.linspace(5e9, 5.01e9, 21)
    z = np.exp(1j * np.linspace(0, 3, 21)) * 0.5
    mon.x = x
    mon.traces.update_data("", x, {"S21": z, "S11": z * 0.1}, "Hz")
    xx, yy = mon.traces.cell_for("S11:phase").canvas.data["S11:phase"]
    assert np.allclose(yy, np.degrees(np.angle(z)))
    mon.traces.set_active("S11:phase")
    assert np.allclose(mon._heat_fn()(z), np.degrees(np.angle(z)))
    mon.traces.retype("S11:phase", fmt="iq")                  # Polar 不能畫成影像 → dB
    assert np.allclose(mon._heat_fn()(z), 20 * np.log10(0.5))


# ---- Hub：論文庫的子專案（parent_id）→ parent / path / 巢狀資料夾 -------------------------------
from tests.test_platform_lc import gw  # noqa: E402,F401


def test_hub_sub_projects_from_paperlib(gw):
    _url, _state, s = gw
    top = next(p for p in s.projects(refresh=True) if p["name"] == "YIG mirror coupling")
    a = s.create_project("mirror v2", parent=top["id"])
    b = s.create_project("2026 秋", parent=a["id"])
    assert a["parent"] == top["id"] and b["parent"] == a["id"]
    c = s.create_project("2026 秋", parent=top["id"])                   # 不同上層可以同名
    ps = {p["id"]: p for p in s.projects(refresh=True)}
    assert ps[b["id"]]["path"] == ["YIG mirror coupling", "mirror v2", "2026 秋"]
    assert ps[b["id"]]["folder"] == "YIG mirror coupling/mirror v2/2026 秋"
    assert ps[c["id"]]["folder"] == "YIG mirror coupling/2026 秋" and ps[top["id"]]["parent"] == ""
