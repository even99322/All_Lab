"""The pets LabLogViewer ships and which of them are hidden (licence-unlocked)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PACKS = Path(__file__).resolve().parent / "characters"
ASSETS = Path(__file__).resolve().parent / "assets"
MAX_LEVEL = 50          # a pet must reach this level before the user may adopt another one


@dataclass(frozen=True)
class PetInfo:
    id: str
    name_key: str       # localization key
    blurb_key: str
    hidden: bool = False
    feature: str | None = None   # licence feature that unlocks a hidden pet

    @property
    def pack(self) -> Path:
        return PACKS / f"{self.id}.json"

    @property
    def preview(self) -> Path:
        return ASSETS / self.id / "lv01" / "idle" / "frame_0001.webp"


PETS = (
    PetInfo("cute_cat", "pet.name.cute_cat", "pet.blurb.cute_cat"),
    PetInfo("shiba", "pet.name.shiba", "pet.blurb.shiba"),
    PetInfo("capybara", "pet.name.capybara", "pet.blurb.capybara"),
    PetInfo("duck_official", "pet.name.duck_official", "pet.blurb.duck_official", hidden=True, feature="hidden_pet_duck"),
)


def get(pet_id: str | None) -> PetInfo | None:
    return next((p for p in PETS if p.id == pet_id), None)


def hidden_unlocked(info: PetInfo) -> bool:
    """Hidden pets unlock permanently: once a valid licence granting them is seen on this
    computer the unlock is remembered in state/pet.json, even if the licence later expires or is removed."""
    if not info.hidden:
        return True
    from app.pet import store

    if info.id in store.unlocked_hidden():
        return True
    from app._guard import gate

    if gate.hidden_pet_allowed(info.feature) is not None:
        store.remember_unlock(info.id)
        return True
    return False
