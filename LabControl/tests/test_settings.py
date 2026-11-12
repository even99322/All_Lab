"""LAB 設定資料夾、settings.yaml 與「所有值皆可設定」。"""
import os
from pathlib import Path

import pytest
import yaml

from labcontrol import APP_NAME, Station, __version__
from labcontrol.paths import DEFAULTS_DIR, ensure_lab_home, lab_home
from labcontrol.settings import Settings, reload, setting


@pytest.fixture
def lab(tmp_path, monkeypatch):
    home = tmp_path / "LAB"
    monkeypatch.setenv("LAB_CONTROL_HOME", str(home))
    ensure_lab_home(home)
    reload()
    yield home
    monkeypatch.undo()
    reload()


def test_version_and_name():
    assert APP_NAME == "Lab Control" and __version__ == "1.0.35"


def test_hub_version_matches_app():
    import labhub
    import labmonitor

    assert labmonitor.__version__ == __version__                          # Monitor 跟 Lab Control 一起發佈
    # 1.0.36：Hub 自己發佈（hub-v<版本>），有自己的說明與更新紀錄
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs" / "releases" / "hub" / f"v{labhub.__version__}.md").exists()
    assert f"## {labhub.__version__}" in (root / "HUB_CHANGELOG.md").read_text(encoding="utf-8")


def test_make_release_separates_hub(tmp_path):
    import subprocess
    import sys
    import zipfile

    import labhub

    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / "tools" / "make_release.py"), str(tmp_path / "lc"), "--only", "labcontrol"],
                   check=True, capture_output=True)
    subprocess.run([sys.executable, str(root / "tools" / "make_release.py"), str(tmp_path / "hub"), "--only", "hub"],
                   check=True, capture_output=True)
    lc = sorted(p.name for p in (tmp_path / "lc").glob("*.zip"))
    hb = sorted(p.name for p in (tmp_path / "hub").glob("*.zip"))
    assert lc == [f"LabControlMonitor_v{__version__}.zip", f"LabControl_v{__version__}.zip"]
    assert hb == [f"LabControlHub_v{labhub.__version__}.zip", f"QELPlatform_NAS_v{labhub.__version__}.zip"]
    z = zipfile.ZipFile(tmp_path / "hub" / hb[0])
    import json

    m = json.loads(z.read("LabControlHub/qel-module.json"))
    assert m["id"] == "hub" and m["version"] == labhub.__version__ and m.get("notes")        # 說明取自 docs/releases/hub/v<版本>.md


def test_lab_home_created_with_defaults(lab):
    assert lab_home() == lab
    for f in ("settings.yaml", "instruments.yaml", "templates/04_two_layer_split.scheme.yaml",
              "experiments/dc_vna_sweep.yaml", "plugins/_driver_template.py"):
        assert (lab / f).exists(), f
    for d in ("schemes", "logs"):
        assert (lab / d).is_dir()


def test_user_files_never_overwritten(lab):
    (lab / "settings.yaml").write_text("labber: {user: ME}\n", encoding="utf-8")
    assert ensure_lab_home(lab) == []
    assert (lab / "settings.yaml").read_text(encoding="utf-8") == "labber: {user: ME}\n"


def test_deep_merge_keeps_new_defaults(lab):
    (lab / "settings.yaml").write_text("labber: {user: ME}\nrun_defaults: {retries: 5}\n", encoding="utf-8")
    reload()
    assert setting("labber.user") == "ME"
    assert setting("labber.project") == "auto"          # 使用者沒寫 → 預設
    assert setting("run_defaults.retries") == 5
    assert setting("run_defaults.on_error") == "pause"
    with pytest.raises(KeyError):
        setting("no.such.key")


def test_windows_default_location():
    assert r"C:\Users\even9\LAB".lower() in open(Path(DEFAULTS_DIR).parent / "paths.py", encoding="utf-8").read().lower()


def test_default_instruments_file_loads(lab):
    st = Station.from_lab(simulate=True)
    assert {"DC1", "VNA1", "SHFQC1", "magnet_A", "magnet_B"} <= set(st.instruments)
    assert type(st.instruments["VNA1"]).__name__ == "SimVNA"


def test_settings_drive_run_defaults_and_rules(lab):
    from labcontrol.measure.runner import RunOptions
    from labcontrol.scheme import Block, Scheme, build_catalog, compile_scheme

    (lab / "settings.yaml").write_text(
        "run_defaults: {retries: 3, approach_rate: 2 mA/s}\n"
        "rules: {exclusive_measure_kinds: []}\n"
        "labber: {user: TESTER, tags: [T1]}\n"
        "editor: {axis_name_suffix: {A: ' I'}}\n", encoding="utf-8")
    reload()
    o = RunOptions.from_config({"park": "none"})
    assert o.retries == 3 and o.approach_rate == pytest.approx(2e-3) and o.park == "none"

    st = Station.from_lab(simulate=True)
    cat = build_catalog(st)
    s = Scheme("x", [Block("set", target="magnet_A", mode="sweep", start=50, stop=51, step=0.5, unit="mA",
                           children=[Block("measure", instrument="VNA1", traces=["S21"]),
                                     Block("measure", instrument="SHFQC1", traces=["QA0"])]),
                     Block("save", file_name="a.hdf5", formats=["labber"])])
    r = compile_scheme(s, cat)
    assert not any("不同時量測" in i.message for i in r.issues)   # 互斥規則由 settings 關閉
    assert r.loops[0].axis_name.endswith(" I")
    exp = r.config["output"]["export"][0]
    assert exp["user"] == "TESTER" and exp["tags"][0] == "T1"

    (lab / "settings.yaml").write_text("", encoding="utf-8")
    reload()
    r = compile_scheme(s, cat)
    assert any("不同時量測" in i.message for i in r.issues)


def test_catalog_comes_from_driver_param_tables(lab):
    from labcontrol.scheme import build_catalog

    text = (lab / "instruments.yaml").read_text(encoding="utf-8")
    text = text.replace("    labber_name: VNA           #",
                        "    parameters: {power: {label: 泵浦功率, default: -30 dBm}, sweep_time: {measure_setting: true}}\n"
                        "    labber_name: VNA           #", 1)
    (lab / "instruments.yaml").write_text(text, encoding="utf-8")
    cat = build_catalog(Station.from_lab(simulate=True))
    m = cat.measurer("VNA1")
    assert m.kind == "vna" and m.head == "VNA"
    assert m.field_label("power") == "泵浦功率" and m.defaults["power"] == "-30 dBm"
    assert "sweep_time" in [f.key for f in m.fields]
    assert cat.target("VNA1.power").label.endswith("泵浦功率")
    assert cat.measurer("SHFQC1").kind == "shfqc"


def test_templates_come_from_lab_folder(lab):
    from labcontrol.scheme import TEMPLATES, Scheme

    before = set(TEMPLATES)
    Scheme("我的範本").save(lab / "templates" / "mine.scheme.yaml")
    assert set(TEMPLATES) == before | {"mine"}
    assert TEMPLATES["mine"][0] == "我的範本"


def test_default_yaml_files_are_valid():
    for f in DEFAULTS_DIR.rglob("*.yaml"):
        assert isinstance(yaml.safe_load(f.read_text(encoding="utf-8")), dict), f


def test_lab_app_metadata_files():
    root = Path(__file__).resolve().parents[1]
    assert (root / ".entry").read_text().strip() == "main.py"
    assert (root / ".readme").read_text().strip() == "README.md"
    icon = (root / ".icon").read_text().strip()
    assert (root / icon).exists()
    assert (root / ".python-version").read_text().strip().startswith("3.")
    assert (root / "requirements.txt").exists() and (root / "main.py").exists()
    assert (root / "docs" / "releases" / f"v{__version__}.md").exists()
    assert f"## {__version__}" in (root / "CHANGELOG.md").read_text(encoding="utf-8")


def test_gitignore_does_not_exclude_package_files():
    """0.0.1b 回歸測試：Lab APP 用 git 發佈，被 .gitignore 排除的檔案不會出現在發佈版本。"""
    import shutil
    import subprocess

    root = Path(__file__).resolve().parents[1]
    if shutil.which("git") is None:
        pytest.skip("沒有 git")
    files = [str(p.relative_to(root)) for p in (root / "labcontrol").rglob("*")
             if p.is_file() and "__pycache__" not in p.parts]
    files += [str(p.relative_to(root)) for d in ("labhub", "labmonitor", "deploy") for p in (root / d).rglob("*")
              if p.is_file() and "__pycache__" not in p.parts and "data" not in p.relative_to(root / d).parts]
    files += ["monitor_main.py", "main.py", "requirements.txt", ".entry", ".icon", ".readme", ".python-version", "assets/icon.ico"]
    r = subprocess.run(["git", "-c", "core.excludesFile=", "check-ignore", "--no-index", "--stdin"],
                       cwd=root, input="\n".join(files), capture_output=True, text=True)
    if r.returncode not in (0, 1) and "not a git repository" in r.stderr:
        tmp = Path(__import__("tempfile").mkdtemp())
        shutil.copy(root / ".gitignore", tmp / ".gitignore")
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        r = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=tmp,
                           input="\n".join(files), capture_output=True, text=True)
        shutil.rmtree(tmp, ignore_errors=True)
    assert r.stdout.strip() == "", f".gitignore 排除了程式檔：\n{r.stdout}"
