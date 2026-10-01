"""v0.19E Help: Tips-style window, step-by-step articles in two languages, shared English pictures."""

from __future__ import annotations

import os
import re

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _entries():
    from app.gui.help_content import all_articles

    return all_articles()


def test_every_article_is_a_real_procedure_in_both_languages():
    from app.gui.help_content import help_directory, load_article

    folder = help_directory()
    for entry in _entries():
        en, zh = load_article(entry["id"], "en"), load_article(entry["id"], "zh")
        for article in (en, zh):
            assert article.summary, entry["id"]
            assert len(article.steps) >= 2, entry["id"]
            assert all(step.title for step in article.steps), entry["id"]
            assert any(step.image for step in article.steps), entry["id"]
            assert any(s.title for s in article.sections), entry["id"]      # options / meaning / problems
        assert len(en.steps) == len(zh.steps), entry["id"]
        assert [s.image for s in en.steps] == [s.image for s in zh.steps], entry["id"]   # shared pictures
        for step in en.steps:
            if step.image:
                assert (folder / "images" / step.image).is_file(), (entry["id"], step.image)


def test_no_unused_or_per_language_pictures():
    from app.gui.help_content import help_directory, load_article

    folder = help_directory() / "images"
    used = {s.image for e in _entries() for s in load_article(e["id"], "en").steps if s.image}
    present = {p.name for p in folder.glob("*.png")}
    assert present == used
    assert not (folder / "zh").exists() and not (folder / "en").exists()


def test_scientific_pages_answer_the_hard_questions():
    """YIG pages must say what results mean, when not to trust them and what to check."""
    from app.gui.help_content import load_article

    for article_id in ("yig-overview", "yig-continuous", "yig-phase-node", "browser-debackground", "viewer-formula"):
        for language in ("en", "zh"):
            article = load_article(article_id, language)
            titles = " ".join(s.title for s in article.sections)
            text = " ".join(s.body for s in article.sections)
            assert re.search("mean|show|trusted|代表|可不可信", titles), (article_id, language)
            assert re.search("not work|做不出來", titles), (article_id, language)
            assert len(text) > (600 if language == "zh" else 1200), (article_id, language)
    phase = load_article("yig-phase-node", "en")
    body = " ".join(s.title + s.body for s in phase.sections)
    for needle in ("Physical", "Coarse", "T Search Maximum", "κ_b", "Global", "trusted"):
        assert needle in body, needle


def test_window_home_article_search_and_context(qapp):
    from app.gui.help_window import CONTEXT_PAGES, ArticleRow, FoldingSection, HelpWindow, StepRow, Thumbnail
    from app.localization import initialize_localization

    localizer = initialize_localization(qapp)
    window = HelpWindow(localizer)
    window.resize(900, 800)
    window.show()
    assert window.mode == "folders"          # folders are the default look
    window.set_mode("list")
    rows = window.home.findChildren(ArticleRow)
    assert {r.article_id for r in rows} == {e["id"] for e in _entries()}
    window.show_article("yig-phase-node")
    qapp.processEvents()
    assert len(window.article_page.findChildren(StepRow)) == 5
    thumbs = window.article_page.findChildren(Thumbnail)
    assert thumbs and min(t.width() for t in thumbs) >= 400     # pictures readable in the list look
    sections = window.article_page.findChildren(FoldingSection)
    assert sections and not sections[0].body.isVisible()
    sections[0].toggle()
    assert sections[0].expanded
    window.show_home()
    window.search.edit.setText("Hampel")
    qapp.processEvents()
    assert {r.article_id for r in window.home.findChildren(ArticleRow) if r.isVisible()} >= {"yig-overview"}
    window.search.edit.clear()
    localizer.set_language("zh_TW")
    window.show_article("viewer-marks")
    assert window.current_article.title == "標記與局部分析"
    localizer.set_language("en")
    ids = {e["id"] for e in _entries()}
    assert set(CONTEXT_PAGES.values()) <= ids
    window.close()


def test_folder_mode_folders_cards_tabs_and_rail(qapp):
    from app.gui.help_folders import CARD_STATES, FLAP_ANGLE, CardStack, FolderWidget, Spring
    from app.gui.help_window import HelpWindow
    from app.localization import initialize_localization

    spring = Spring(0.0, 120, 13)                 # the folder component's spring settles on target
    spring.set(1.0)
    for _ in range(200):
        spring.step(1 / 60)
    assert spring.settled() and abs(spring.value - 1.0) < 0.01
    localizer = initialize_localization(qapp)
    localizer.set_language("en")
    window = HelpWindow(localizer)
    window.resize(1100, 820)
    window.show()
    qapp.processEvents()
    mode = window.folder_mode
    groups = [g["id"] for g in mode.index["groups"]]
    assert set(mode.home.folders) == set(groups)          # one folder per topic
    folder = mode.home.folders["yig"]
    assert isinstance(folder, FolderWidget)
    folder.set_state("open")
    for _ in range(120):
        folder._tick() if folder._timer.isActive() else None
        for item in folder._springs:
            item.step(1 / 60)
    assert abs(folder.flap.value - FLAP_ANGLE["open"]) < 1
    assert abs(folder.cards[1][1].value - CARD_STATES[1]["open"][1]) < 2
    # opening a folder shows its guides as cards: cover, one per step, one per section
    mode.open_group("yig")
    stack = mode.stack
    assert isinstance(stack, CardStack) and mode.body.currentWidget() is stack
    kinds = [c.kind for c in stack.cards]
    assert kinds[0] == "cover" and "step" in kinds and "section" in kinds
    assert {c.article_id for c in stack.cards} == {a["id"] for a in mode.index["groups"][groups.index("yig")]["articles"]}
    step = next(c for c in stack.cards if c.kind == "step" and c.image_rect is not None)
    assert step.image_rect.height() > step.height * 0.5          # the picture takes most of a step card
    assert mode.tabs.selected == groups.index("yig")
    # jumping (tabs / search) lands on that guide's cover card; later cards cover earlier ones
    mode.open_article("yig-phase-node")
    stack.scroll.jump(stack.scroll.target)
    assert stack.current_article() == "yig-phase-node"
    placed = stack.placements()
    covered = [p for p in placed if p[3] < 1.0]
    assert covered and all(p[4] > 0 for p in covered)          # covered cards shrink and dim
    mode.open_article("viewer-2d")                              # another folder
    stack.scroll.jump(stack.scroll.target)
    assert stack.group["id"] == "viewer" and stack.current_article() == "viewer-2d"
    mode.search.edit.setText("Hampel")
    assert mode.body.currentWidget() is mode.results
    mode.search.edit.clear()
    assert mode.body.currentWidget() is stack
    # the rail: switch looks keeping the place, copy everything for an AI assistant
    window.toggle_mode()
    assert window.mode == "list" and window._current == "viewer-2d"
    window.toggle_mode()
    assert window.mode == "folders" and stack.current_article() == "viewer-2d"
    window.copy_for_ai()
    text = qapp.clipboard().text()
    assert "Phase / Node" in text and window.toast.isVisible()
    window.go_home()
    assert mode.body.currentWidget() is mode.home
    window.show_topic("licenses")
    assert stack.group["id"] == "settings"
    window.close()


def test_ai_export_contains_every_procedure():
    from app.gui.help_content import load_article
    from app.gui.help_window import build_ai_export

    text = build_ai_export(include_state=False)
    for entry in _entries():
        article = load_article(entry["id"], "en")
        assert article.title in text
        assert article.steps[0].title in text


def test_continuous_fit_row_selection_redraws_all_parameters_plot(qapp):
    """Selecting a Continuous Fit row used to raise AttributeError (map_pane) in the All Parameters plot."""
    from app.analysis.yig_fitting.ui.plots import AllParamsPlotWidget

    widget = AllParamsPlotWidget()
    widget.set_selected(None)                 # must not raise
    widget.set_selected(3, draw=True)


def test_leaving_multi_pane_resets_the_active_pane_label(qapp):
    from tests.real_data import SMALL_FILE

    if not SMALL_FILE.exists():
        pytest.skip("fixture unavailable")
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.open_file(str(SMALL_FILE))
    qapp.processEvents()
    window.pane_layout_combo.setCurrentIndex(1)
    qapp.processEvents()
    window._activate_pane(2)
    assert window.active_pane_label.text().endswith("2")
    window.pane_layout_combo.setCurrentIndex(0)
    qapp.processEvents()
    assert window.active_pane_label.text().endswith("1")
    window.close()


def test_legacy_sources_stay_inside_the_home_in_use(qapp, tmp_path, monkeypatch):
    """A fake HOME (tests, sandboxes) must never import from the real user's Library."""
    from app.core import data_location

    monkeypatch.setenv("HOME", str(tmp_path))
    for _kind, folder in data_location.legacy_sources():
        assert Path(folder).resolve().is_relative_to(tmp_path.resolve()), folder
