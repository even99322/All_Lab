"""External, versioned application settings."""

from __future__ import annotations

from pathlib import Path

from app.core.external_state import atomic_write_json, default_state_path, load_json_state


SETTINGS_SCHEMA = 1
APPEARANCE_MODES = {"light", "dark", "system"}
PLOT_APPEARANCES = {"white", "dark"}
THREE_D_PROFILES = ("balanced", "quality", "economy")
THREE_D_EXPORT_STYLES = ("publication", "screen")


_listeners: list = []


def add_settings_listener(callback) -> None:
    """Called after any setting is saved (used to push changes to helper processes)."""
    _listeners.append(callback)


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
        icon = self._payload["general"].get("app_icon", 4)
        self._payload["general"]["app_icon"] = icon if isinstance(icon, int) and not isinstance(icon, bool) \
            and 1 <= icon <= 4 else 4
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

    def app_icon(self) -> int:
        """1-4: which of icons/app/app_icon_<n>.png is the application icon."""
        return int(self._payload["general"]["app_icon"])

    def set_app_icon(self, number: int) -> None:
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 4:
            raise ValueError(f"Unsupported app icon: {number}")
        if self.app_icon() == number and self.path.exists():
            return
        self._payload["general"]["app_icon"] = number
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
