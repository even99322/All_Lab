"""安裝 / 解除安裝：開機自動在背景啟動。

Windows（1.0.3 起）：安裝程式（要管理員）建立系統排程「QEL\\NetMeter」——以 SYSTEM 身分在開機時啟動
         （不用登入）、每 10 分鐘檢查一次沒在跑就啟動、當掉 1 分鐘後重新啟動、沒有執行時間上限。
         沒有用安裝程式（例如開發時）才退回 1.0.2 以前的做法：HKCU\\...\\Run（登入時啟動）。
macOS：~/Library/LaunchAgents/tw.qel.netmeter.plist（登入時啟動；當掉會自己重新啟動）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import APP_ID
from .config import data_dir

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TASK = r"QEL\NetMeter"
PLIST = Path.home() / "Library" / "LaunchAgents" / "tw.qel.netmeter.plist"
LABEL = "tw.qel.netmeter"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _onefile() -> bool:
    meipass = getattr(sys, "_MEIPASS", "")
    return bool(meipass) and Path(meipass).resolve() != Path(sys.executable).resolve().parent / "_internal" \
        and Path(meipass).resolve() != Path(sys.executable).resolve().parent


def installed_exe() -> Path:
    if sys.platform == "win32" and frozen() and _onefile():
        return data_dir() / f"{APP_ID}.exe"
    return Path(sys.executable)


def agent_command(exe: Optional[Path] = None) -> List[str]:
    if frozen():
        return [str(exe or installed_exe()), "--agent"]
    return [sys.executable, "-m", "netmeter", "--agent"]


def copy_self() -> Path:
    """1.0.1 起 Windows 由安裝程式裝好（資料夾版）：直接用自己。只有舊的單一 exe 版（_MEIPASS 在暫存資料夾）才複製。"""
    if sys.platform != "win32" or not frozen() or not _onefile():
        return Path(sys.executable)
    src, dst = Path(sys.executable).resolve(), installed_exe()
    if dst.exists() and src == dst.resolve():
        return dst
    for i in range(20):                                   # 舊的背景程式剛被叫結束，檔案可能還鎖著
        try:
            tmp = dst.with_suffix(".new")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            return dst
        except PermissionError:
            time.sleep(0.5)
    raise PermissionError(f"無法更新 {dst}（舊版還在執行？）")


def enable_autostart(exe: Optional[Path] = None) -> str:
    cmd = agent_command(exe)
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
        return "登入 Windows 時自動啟動"
    if sys.platform == "darwin":
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        args = "".join(f"<string>{a}</string>" for a in cmd)
        PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{LABEL}</string>
<key>ProgramArguments</key><array>{args}</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
<key>ThrottleInterval</key><integer>60</integer>
<key>ProcessType</key><string>Background</string>
</dict></plist>
""", encoding="utf-8")
        return "登入 macOS 時自動啟動"
    return "（這個系統不支援自動啟動）"


def disable_autostart() -> None:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, APP_ID)
        except OSError:
            pass
    elif sys.platform == "darwin":
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        try:
            PLIST.unlink()
        except OSError:
            pass


# ---- 1.0.3：Windows 系統排程（開機就啟動，不用登入）----
def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, creationflags=0x08000000)   # 不開主控台


def task_xml(exe: Path) -> str:
    """工作排程器的 XML：開機（延遲 30 秒）＋任何人登入＋每 10 分鐘看一次；SYSTEM；不限執行時間；當掉自己重來。"""
    from xml.sax.saxutils import escape

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>QEL 流量監看：在背景記錄這台電腦在實驗室網段的流量並回報 Hub</Description></RegistrationInfo>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled><Delay>PT30S</Delay></BootTrigger>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary><Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      <Repetition><Interval>PT10M</Interval><Duration>P1D</Duration><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(str(exe))}</Command><Arguments>--agent</Arguments><WorkingDirectory>{escape(str(exe.parent))}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def install_service(exe: Optional[Path] = None) -> str:
    """建立 / 更新系統排程並立刻啟動（要管理員；安裝程式會呼叫）。也拿掉 1.0.2 以前的「登入時啟動」。"""
    import tempfile

    exe = Path(exe or sys.executable).resolve()
    with tempfile.TemporaryDirectory() as td:
        x = Path(td) / "netmeter-task.xml"
        x.write_text(task_xml(exe), encoding="utf-16")
        r = _schtasks("/Create", "/TN", TASK, "/XML", str(x), "/F")
    if r.returncode != 0:
        raise RuntimeError(f"建立系統排程失敗（要用管理員身分）：{(r.stderr or r.stdout).strip()}")
    _remove_run_key()
    _SVC_CACHE.clear()
    _schtasks("/Run", "/TN", TASK)
    return "開機時自動在背景執行（系統排程，不用登入）"


def uninstall_service() -> None:
    _schtasks("/End", "/TN", TASK)
    _schtasks("/Delete", "/TN", TASK, "/F")
    _SVC_CACHE.clear()


_SVC_CACHE: List[float] = []


def service_installed(max_age: float = 30.0) -> bool:
    if sys.platform != "win32":
        return False
    if _SVC_CACHE and time.time() - _SVC_CACHE[0] < max_age:
        return bool(_SVC_CACHE[1])
    try:
        ok = _schtasks("/Query", "/TN", TASK).returncode == 0
    except OSError:
        ok = False
    _SVC_CACHE[:] = [time.time(), float(ok)]
    return ok


def run_service() -> bool:
    """叫系統排程現在啟動背景程式（一般使用者可能沒有權限 → False）。"""
    try:
        return _schtasks("/Run", "/TN", TASK).returncode == 0
    except OSError:
        return False


def _remove_run_key() -> None:
    if sys.platform != "win32":
        return
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_ID)
    except OSError:
        pass


def autostart_text() -> str:
    if service_installed():
        return "已設定（開機自動在背景執行，系統排程，不用登入）"
    return "已設定（登入時自動在背景執行）" if autostart_enabled() else "沒有設定"


def autostart_enabled() -> bool:
    if sys.platform == "win32":
        if service_installed():
            return True
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_ID)
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return PLIST.exists()
    return False


def start_agent(exe: Optional[Path] = None) -> None:
    """另開一個背景程式（不會跟著這個視窗關掉）。"""
    cmd = agent_command(exe)
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **kw)
