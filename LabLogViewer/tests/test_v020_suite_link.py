"""suite_link: link format and per-system registration (all backends faked)."""

import pytest

from suite_link import (APPS, LinkError, LinuxRegistrar, MacRegistrar, WindowsRegistrar, build_link,
                        launch_command, parse_link, plist_entry)


def test_link_round_trip_and_defaults():
    url = build_link("browser", "open", {"file": "/data/a b.h5", "trace": 3})
    assert url.startswith("labbrowser://open?v=1&")
    link = parse_link(url)
    assert (link.app, link.action, link.version) == ("browser", "open", 1)
    assert dict(link.params) == {"file": "/data/a b.h5", "trace": "3"}
    assert link.url() == build_link("browser", "open", {"file": "/data/a b.h5", "trace": "3"})
    assert {parse_link(build_link(app, "fill")).app for app in APPS} == set(APPS)


@pytest.mark.parametrize("bad", ["http://x/open", "labbrowser://start?v=1", "labbrowser://open?v=2",
                                 "labbrowser://open?v=x", "labbrowser://open?a=%00", "", "labmeasure:open"])
def test_unknown_or_unsafe_links_are_refused(bad):
    with pytest.raises(LinkError):
        parse_link(bad)


def test_no_action_can_start_a_measurement_and_building_is_validated():
    for action in ("start", "run", "measure", "exec"):
        with pytest.raises(LinkError):
            build_link("measurement", action)
    for args in (("nobody", "open"), ("browser", "open", {"v": 2}), ("browser", "open", {"a": "x\n"}),
                 ("browser", "open", {"a": "x" * 9000})):
        with pytest.raises(LinkError):
            build_link(*args)


def test_windows_registers_in_the_current_user_hive():
    class Fake:
        def __init__(self):
            self.values = {}

        def set_value(self, path, value, name=None):
            self.values[(path, name or "")] = value

        def has_key(self, path):
            return any(key[0] == path for key in self.values)

        def delete_tree(self, path):
            self.values = {k: v for k, v in self.values.items() if not k[0].startswith(path)}

    fake = Fake()
    registrar = WindowsRegistrar(fake)
    assert not registrar.is_registered("labbrowser")
    assert registrar.register("labbrowser", "LabLogViewer", [r"C:\Apps\LabLogViewer.exe"])
    assert fake.values[("Software\\Classes\\labbrowser\\shell\\open\\command", "")] == r'"C:\Apps\LabLogViewer.exe" "%1"'
    assert ("Software\\Classes\\labbrowser", "URL Protocol") in fake.values
    assert registrar.is_registered("labbrowser")
    registrar.unregister("labbrowser")
    assert not registrar.is_registered("labbrowser") and not fake.values


def test_linux_writes_a_desktop_file_and_calls_xdg_mime(tmp_path):
    calls = []
    registrar = LinuxRegistrar(tmp_path, run=lambda args: calls.append(args) or 0)
    assert registrar.register("labtime", "Time Domain", ["/opt/td/run"])
    text = (tmp_path / "labtime-handler.desktop").read_text()
    assert 'Exec="/opt/td/run" %u' in text and "x-scheme-handler/labtime" in text
    assert calls == [["xdg-mime", "default", "labtime-handler.desktop", "x-scheme-handler/labtime"]]
    assert registrar.is_registered("labtime")
    registrar.unregister("labtime")
    assert not registrar.is_registered("labtime")


def test_macos_is_registered_by_the_bundle_not_at_run_time():
    assert MacRegistrar().register("labbrowser", "x", ["y"]) is False
    assert MacRegistrar(lambda scheme: scheme == "labbrowser").is_registered("labbrowser")
    assert plist_entry("labbrowser", "LabLogViewer") == {
        "CFBundleURLName": "LabLogViewer", "CFBundleURLSchemes": ["labbrowser"]}
    assert launch_command("/x/python", "main.py") == ["/x/python", "main.py"]


def test_3d_process_gets_half_of_the_cpu_threads():
    from app.core.cpu_threads import three_d_threads

    assert [three_d_threads(n) for n in (1, 2, 3, 9, 12)] == [1, 1, 1, 4, 6]
