import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import safe_io


@pytest.fixture(autouse=True)
def fast_stability_window(monkeypatch):
    # Production waits one second. Individual race tests inject a writer at this boundary.
    monkeypatch.setattr(safe_io,"STABILITY_SECONDS",0)
