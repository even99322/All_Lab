"""External, versioned application settings."""

from __future__ import annotations

from pathlib import Path

from app.core.external_state import atomic_write_json, default_state_path, load_json_state


SETTINGS_SCHEMA = 1
APPEARANCE_MODES = {"light", "dark", "system"}
PLOT_APPEARANCES = {"white", "dark"}
THREE_D_PROFILES = ("balanced", "quality", "economy")
APP_ICON_COUNT = 7          # icons/app/app_icon_<n>.png; 0 = the imported picture
APP_ICON_DEFAULT = 5
THREE_D_ICON_COUNT = 3      # icons/app/three_d_icon_<n>.png
THREE_D_EXPORT_STYLES = ("publication", "screen")


_listeners: list = []


def add_settings_listener(callback) -> None:
    """Called after any setting is saved (used to push changes to helper processes)."""
    _listeners.append(callback)


def remove_settings_listener(callback) -> None:
    try:
        _listeners.remove(callback)
    except ValueError:
        pass


class SettingsStore:
    def _save(self) -> None:
        atomic_write_json(self.path, self._payload)
        for callback in tuple(_listeners):
            try:
                callback()
            except Exception:
                pass

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_state_path("settings.json")
        loaded = load_json_state(self.path, {})
        raw = loaded.value if isinstance(loaded.value, dict) else {}
        general = raw.get("general", {})
        self._payload = dict(raw)
        self._payload["schema_version"] = SETTINGS_SCHEMA
        self._payload["general"] = dict(general) if isinstance(general, dict) else {}
        self._payload["general"].setdefault("language", "en")
        if self._payload["general"]["language"] not in {"en", "zh_TW"}:
            self._payload["general"]["language"] = "en"
        self._payload["general"].setdefault("appearance", "system")
        if self._payload["general"]["appearance"] not in APPEARANCE_MODES:
            self._payload["general"]["appearance"] = "system"
        self._payload["general"].setdefault("scientific_plot_appearance", "white")
        if self._payload["general"]["scientific_plot_appearance"] not in PLOT_APPEARANCES:
            self._payload["general"]["scientific_plot_appearance"] = "white"
        self._payload["general"].setdefault("export_plot_background", "white")
        if self._payload["general"]["export_plot_background"] not in PLOT_APPEARANCES:
            self._payload["general"]["export_plot_background"] = "white"
        if self._payload["general"].get("three_d_export_style") not in THREE_D_EXPORT_STYLES:
            self._payload["general"]["three_d_export_style"] = "publication"
        if self._payload["general"].get("three_d_profile") not in THREE_D_PROFILES:
            self._payload["general"]["three_d_profile"] = "balanced"
        icon = self._payload["general"].get("app_icon", self._installer_icon())
        self._payload["general"]["app_icon"] = icon if isinstance(icon, int) and not isinstance(icon, bool) \
            and 0 <= icon <= APP_ICON_COUNT else APP_ICON_DEFAULT
        three_d = self._payload["general"].get("three_d_icon", 3)
        self._payload["general"]["three_d_icon"] = three_d if isinstance(three_d, int) \
            and not isinstance(three_d, bool) and 0 <= three_d <= THREE_D_ICON_COUNT else 3
        sync = self._payload["general"].get("three_d_icon_sync", False)
        self._payload["general"]["three_d_icon_sync"] = sync if isinstance(sync, bool) else False
        for key, default in (("glass_thickness", 0.5), ("glass_frost", 0.25)):
            value = self._payload["general"].get(key, default)
            valid = isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0
            self._payload["general"][key] = float(value) if valid else default

    # -- Network Workspace ------------------------------------------------------
    def _network(self) -> dict:
        network = self._payload.get("network")
        if not isinstance(network, dict):
            network = self._payload["network"] = {}
        return network

    def auto_refresh_seconds(self) -> int:
        """How often the opened database is checked for new / changed files (0 = off)."""
        value = self._payload.get("auto_refresh_seconds", 2)
        return value if value in (0, 1, 2, 5) and not isinstance(value, bool) else 2

    def set_auto_refresh_seconds(self, seconds: int) -> None:
        if isinstance(seconds, bool) or seconds not in (0, 1, 2, 5):
            raise ValueError(f"Unsupported auto refresh interval: {seconds}")
        self._payload["auto_refresh_seconds"] = seconds
        self._save()

    def tag_search_root(self) -> str:
        """The largest data folder searched by Retrieve by Tags ("" = not set)."""
        value = self._payload.get("tag_search_root")
        return value if isinstance(value, str) else ""

    def set_tag_search_root(self, folder: str) -> None:
        folder = str(folder or "").strip()
        if folder:
            self._payload["tag_search_root"] = folder
        else:
            self._payload.pop("tag_search_root", None)
        self._save()

    def network_user_name(self) -> str:
        """This computer's name in the Network Workspace (any language)."""
        value = self._network().get("user_name")
        return value.strip() if isinstance(value, str) else ""

    def set_network_user_name(self, name: str) -> None:
        name = " ".join(str(name).split())[:40]
        if not name:
            raise ValueError("The name cannot be empty.")
        self._network()["user_name"] = name
        self._save()

    def network_shared_folders(self) -> list[str]:
        """Folders this computer can read that may hold the Host's files (e.g. shared storage)."""
        value = self._network().get("shared_folders")
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    def set_network_shared_folders(self, folders: list[str]) -> None:
        self._network()["shared_folders"] = [str(folder) for folder in folders]
        self._save()

    def network_flag(self, key: str) -> bool:
        return bool(self._network().get(key, False))

    def set_network_flag(self, key: str, value: bool) -> None:
        self._network()[key] = bool(value)
        self._save()

    def network_session_name(self) -> str:
        value = self._network().get("session_name")
        return value if isinstance(value, str) else ""

    def set_network_session_name(self, name: str) -> None:
        self._network()["session_name"] = " ".join(str(name).split())[:60]
        self._save()

    def language(self) -> str:
        return str(self._payload["general"]["language"])

    def set_language(self, language: str) -> None:
        if language not in {"en", "zh_TW"}:
            raise ValueError(f"Unsupported language: {language}")
        if self.language() == language and self.path.exists():
            return
        self._payload["general"]["language"] = language
        self._save()

    def appearance(self) -> str:
        return str(self._payload["general"]["appearance"])

    def set_appearance(self, appearance: str) -> None:
        if appearance not in APPEARANCE_MODES:
            raise ValueError(f"Unsupported appearance mode: {appearance}")
        if self.appearance() == appearance and self.path.exists():
            return
        self._payload["general"]["appearance"] = appearance
        self._save()

    def scientific_plot_appearance(self) -> str:
        return str(self._payload["general"]["scientific_plot_appearance"])

    def set_scientific_plot_appearance(self, appearance: str) -> None:
        if appearance not in PLOT_APPEARANCES:
            raise ValueError(f"Unsupported scientific plot appearance: {appearance}")
        if self.scientific_plot_appearance() == appearance and self.path.exists():
            return
        self._payload["general"]["scientific_plot_appearance"] = appearance
        self._save()

    def export_plot_background(self) -> str:
        return str(self._payload["general"]["export_plot_background"])

    def set_export_plot_background(self, appearance: str) -> None:
        if appearance not in PLOT_APPEARANCES:
            raise ValueError(f"Unsupported export plot background: {appearance}")
        if self.export_plot_background() == appearance and self.path.exists():
            return
        self._payload["general"]["export_plot_background"] = appearance
        self._save()

    def three_d_export_style(self) -> str:
        return str(self._payload["general"]["three_d_export_style"])

    def set_three_d_export_style(self, style: str) -> None:
        if style not in THREE_D_EXPORT_STYLES:
            raise ValueError(f"Unsupported 3D export style: {style}")
        if self.three_d_export_style() == style and self.path.exists():
            return
        self._payload["general"]["three_d_export_style"] = style
        self._save()

    @staticmethod
    def _installer_icon() -> int:
        """The icon picked in the Windows installer (app_icon.txt beside the program), else the default."""
        try:
            import sys
            from pathlib import Path

            number = int((Path(sys.executable).parent / "app_icon.txt").read_text(encoding="ascii").strip())
            return number if 1 <= number <= APP_ICON_COUNT else APP_ICON_DEFAULT
        except (OSError, ValueError):
            return APP_ICON_DEFAULT

    def app_icon(self) -> int:
        """1-7: which of icons/app/app_icon_<n>.png is the application icon; 0 = the imported picture."""
        return int(self._payload["general"]["app_icon"])

    def set_app_icon(self, number: int) -> None:
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= APP_ICON_COUNT:
            raise ValueError(f"Unsupported app icon: {number}")
        if self.app_icon() == number and self.path.exists():
            return
        self._payload["general"]["app_icon"] = number
        self._save()

    def three_d_icon(self) -> int:
        """1-3 (3 is the default): which of icons/app/three_d_icon_<n>.png the 3D Surface window uses; 0 = the imported picture."""
        return int(self._payload["general"]["three_d_icon"])

    def set_three_d_icon(self, number: int) -> None:
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= THREE_D_ICON_COUNT:
            raise ValueError(f"Unsupported 3D icon: {number}")
        if self.three_d_icon() == number and self.path.exists():
            return
        self._payload["general"]["three_d_icon"] = number
        self._save()

    def three_d_icon_sync(self) -> bool:
        """True: the 3D window uses the same icon as the app."""
        return bool(self._payload["general"]["three_d_icon_sync"])

    def set_three_d_icon_sync(self, sync: bool) -> None:
        if not isinstance(sync, bool):
            raise ValueError(f"Unsupported 3D icon sync value: {sync!r}")
        if self.three_d_icon_sync() == sync and self.path.exists():
            return
        self._payload["general"]["three_d_icon_sync"] = sync
        self._save()

    def three_d_profile(self) -> str:
        return str(self._payload["general"]["three_d_profile"])

    def set_three_d_profile(self, profile: str) -> None:
        if profile not in THREE_D_PROFILES:
            raise ValueError(f"Unsupported 3D performance profile: {profile}")
        if self.three_d_profile() == profile and self.path.exists():
            return
        self._payload["general"]["three_d_profile"] = profile
        self._save()

    @staticmethod
    def available_cpu_threads() -> int:
        import os
        return max(1, os.cpu_count() or 1)

    @classmethod
    def default_cpu_threads(cls) -> int:
        """Default: 75 % of the logical CPUs (at least 1), so the computer stays responsive."""
        return max(1, int(cls.available_cpu_threads() * 0.75))

    def cpu_threads(self) -> int:
        """Worker threads LabLogViewer may use for parallel background work (default: 75 % of the logical CPUs)."""
        value = self._payload["general"].get("cpu_threads")
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= self.available_cpu_threads():
            return value
        return self.default_cpu_threads()

    def set_cpu_threads(self, count: int) -> None:
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= self.available_cpu_threads():
            raise ValueError(f"Unsupported CPU thread count: {count}")
        self._payload["general"]["cpu_threads"] = count
        self._save()

    def glass_material(self) -> tuple[float, float]:
        general = self._payload["general"]
        return float(general["glass_thickness"]), float(general["glass_frost"])

    def set_glass_material(self, thickness: float, frost: float) -> None:
        values = (float(thickness), float(frost))
        if not all(0.0 <= value <= 1.0 for value in values):
            raise ValueError("Glass thickness and frost must be between 0 and 1.")
        if self.glass_material() == values and self.path.exists():
            return
        self._payload["general"]["glass_thickness"], self._payload["general"]["glass_frost"] = values
        self._save()
