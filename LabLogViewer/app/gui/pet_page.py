"""Settings > Pet: show / hide the desktop pet and adopt one."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from app.pet import pack, progress, registry, store


def _preview_pixmap(info: registry.PetInfo):
    """Idle pose with the live-painted eyes, so dot-eyed pets do not look blank."""
    try:
        from app.pet.character import Character
        from app.pet.pet_window import PetWindow

        win = PetWindow(Character(info.pack, 1))
        win.timer.stop()
        pm = win.grab()
        win.deleteLater()
        return pm
    except Exception:
        return QPixmap(str(info.preview))


class PetCard(QFrame):
    def __init__(self, page: "PetPage", info: registry.PetInfo):
        super().__init__()
        self.page, self.info = page, info
        self.setObjectName("petCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        row = QHBoxLayout(self)
        self.preview = QLabel()
        self.preview.setFixedSize(84, 84)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = _preview_pixmap(info)
        if pm is not None and not pm.isNull():
            self.preview.setPixmap(pm.scaled(84, 84, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        row.addWidget(self.preview)
        text = QVBoxLayout()
        self.name = QLabel()
        font = self.name.font(); font.setBold(True); font.setPointSize(font.pointSize() + 1); self.name.setFont(font)
        self.blurb = QLabel(); self.blurb.setWordWrap(True)
        self.status = QLabel(); self.status.setWordWrap(True)
        self.status.setObjectName("petStatus")
        for w in (self.name, self.blurb, self.status):
            text.addWidget(w)
        row.addLayout(text, 1)
        self.button = QPushButton()
        self.button.setObjectName(f"adopt_{info.id}")
        self.button.clicked.connect(lambda: page.adopt(info.id))
        row.addWidget(self.button, 0, Qt.AlignmentFlag.AlignVCenter)

    def refresh(self, state: dict) -> None:
        t = self.page.t
        self.name.setText(t(self.info.name_key))
        self.blurb.setText(t(self.info.blurb_key))
        level = store.level_of(state, self.info.id)
        allowed, reason = store.can_choose(state, self.info.id)
        if state["current"] == self.info.id:
            self.status.setText(f"✓ {t('pet.chosen')} · Lv.{level}")
            self.button.setText(t("pet.chosen"))
            self.button.setEnabled(False)
        else:
            self.status.setText("" if allowed else t(reason).format(max=registry.MAX_LEVEL))
            self.button.setText(t("pet.choose"))
            self.button.setEnabled(allowed and pack.available())


class PetPage(QWidget):
    def __init__(self, localizer, parent=None):
        super().__init__(parent)
        self.localizer = localizer
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget(); scroll.setWidget(body)
        layout = QVBoxLayout(body)
        self.title = QLabel(); f = self.title.font(); f.setBold(True); f.setPointSize(f.pointSize() + 3); self.title.setFont(f)
        self.intro = QLabel(); self.intro.setWordWrap(True)
        self.show_box = QCheckBox(); self.show_box.setObjectName("petShow")
        self.show_box.toggled.connect(self._show_toggled)
        self.float_box = QCheckBox(); self.float_box.setObjectName("petFloat")
        self.float_box.toggled.connect(self._float_toggled)
        self.current = QLabel(); self.current.setObjectName("petCurrent")
        self.exp_bar = QProgressBar(); self.exp_bar.setObjectName("petExpBar"); self.exp_bar.setTextVisible(False)
        self.exp_bar.setFixedHeight(10)
        self.exp_label = QLabel(); self.exp_label.setObjectName("petExp"); self.exp_label.setWordWrap(True)
        # v2.1.5: the pictures are an optional download (app/pet/pack.py)
        self.pics_status = QLabel(); self.pics_status.setObjectName("petPictures"); self.pics_status.setWordWrap(True)
        self.pics_button = QPushButton(); self.pics_button.setObjectName("petDownload")
        self.pics_button.clicked.connect(self._download_pictures)
        pics_row = QHBoxLayout(); pics_row.addWidget(self.pics_status, 1); pics_row.addWidget(self.pics_button)
        for w in (self.title, self.intro):
            layout.addWidget(w)
        layout.addLayout(pics_row)
        for w in (self.show_box, self.float_box, self.current, self.exp_bar, self.exp_label):
            layout.addWidget(w)
        look_row = QHBoxLayout()
        self.look_label = QLabel()
        self.look_combo = QComboBox(); self.look_combo.setObjectName("petLook")
        self.look_combo.activated.connect(self._look_chosen)
        look_row.addWidget(self.look_label); look_row.addWidget(self.look_combo); look_row.addStretch(1)
        layout.addLayout(look_row)
        self.look_hint = QLabel(); self.look_hint.setWordWrap(True); self.look_hint.setObjectName("petLookHint")
        layout.addWidget(self.look_hint)
        self.tokens = QLabel(); self.tokens.setObjectName("petTokens"); self.tokens.setWordWrap(True)
        layout.addWidget(self.tokens)
        self.pets_group = QGroupBox(); pg = QVBoxLayout(self.pets_group)
        self.cards = []
        self.pets_layout = pg
        layout.addWidget(self.pets_group)
        self.rule = QLabel(); self.rule.setWordWrap(True); self.rule.setObjectName("petRule")
        layout.addWidget(self.rule)
        layout.addStretch(1)
        if hasattr(localizer, "language_changed"):
            localizer.language_changed.connect(self.refresh)
        self.refresh()

    def t(self, key: str) -> str:
        return self.localizer.text(key)

    def refresh(self) -> None:
        state = store.load()
        t = self.t
        self.title.setText(t("pet.page_title"))
        self.intro.setText(t("pet.page_intro"))
        have = pack.available()
        manifest = pack.installed_info()
        if not have:
            self.pics_status.setText("⚠ " + t("pet.pics.missing"))
        elif manifest:
            self.pics_status.setText(t("pet.pics.installed").format(version=manifest.get("version", "?")))
        else:
            self.pics_status.setText(t("pet.pics.bundled").format(folder=pack.assets_root()))
        self.pics_button.setText(t("pet.pics.download") if not have else t("pet.pics.redownload"))
        self.pics_button.setVisible(not have or pack.assets_root() != pack.BUNDLED)
        self.show_box.blockSignals(True)
        self.show_box.setText(t("pet.show"))
        self.show_box.setChecked(state["enabled"])
        self.show_box.setEnabled(state["current"] is not None)
        self.show_box.blockSignals(False)
        self.float_box.blockSignals(True)
        self.float_box.setText(t("pet.float"))
        self.float_box.setChecked(state["float_on_top"])
        self.float_box.blockSignals(False)
        info = registry.get(state["current"])
        self.current.setText(t("pet.current").format(name=t(info.name_key), level=store.level_of(state, info.id))
                             if info else t("pet.current_none"))
        self.exp_bar.setVisible(info is not None); self.exp_label.setVisible(info is not None)
        if info is not None:
            level = store.level_of(state, info.id)
            exp = int(state["exp"].get(info.id, 0))
            bonus = round((progress.streak_multiplier(state.get("streak", 0)) - 1) * 100)
            streak = t("pet.exp.streak").format(days=state.get("streak", 0), bonus=bonus)
            if level >= registry.MAX_LEVEL:
                self.exp_bar.setRange(0, 1); self.exp_bar.setValue(1)
                self.exp_label.setText(t("pet.panel.max").format(max=registry.MAX_LEVEL) + " · " + streak)
            else:
                lo, hi = progress.exp_for_level(level), progress.exp_for_level(level + 1)
                self.exp_bar.setRange(0, hi - lo); self.exp_bar.setValue(exp - lo)
                self.exp_label.setText(t("pet.panel.exp").format(exp=exp - lo, need=hi - lo, next=level + 1) + " · " + streak)
        self.look_label.setText(t("pet.look"))
        self.look_hint.setText(t("pet.look_hint"))
        self.look_combo.clear()
        for w in (self.look_label, self.look_combo, self.look_hint):
            w.setVisible(info is not None)
        if info is not None:
            top = progress.milestone(store.level_of(state, info.id))
            for m in progress.MILESTONES:
                if m <= top:
                    self.look_combo.addItem(t("pet.look_level").format(level=m), m)
            self.look_combo.setCurrentIndex(max(0, self.look_combo.findData(store.look_of(state, info.id))))
            self.look_combo.setEnabled(self.look_combo.count() > 1)
        tokens = int(state.get("reselect_tokens", 0))
        self.tokens.setText(t("pet.reselect.tokens").format(n=tokens) if tokens else "")
        self.tokens.setVisible(tokens > 0)
        self.pets_group.setTitle(t("pet.pets"))
        self._sync_cards(state)
        self.rule.setText(t("pet.rule").format(max=registry.MAX_LEVEL))

    def _sync_cards(self, state) -> None:
        """One list: the regular pets, plus a licence-unlocked pet only once it is unlocked."""
        shown = [p for p in registry.PETS if not p.hidden or registry.hidden_unlocked(p)]
        have = {card.info.id: card for card in self.cards}
        for pid, card in list(have.items()):
            if pid not in [p.id for p in shown]:
                card.setParent(None); card.deleteLater(); self.cards.remove(card); del have[pid]
        for p in shown:
            if p.id not in have:
                card = PetCard(self, p); self.cards.append(card); self.pets_layout.addWidget(card)
        for card in self.cards:
            card.refresh(state)

    def adopt(self, pet_id: str) -> None:
        info = registry.get(pet_id)
        state = store.load()
        name = self.t(info.name_key)
        if state["current"] is None or not info.hidden:
            answer = QMessageBox.question(self, self.t("pet.confirm_title").format(name=name),
                                          self.t("pet.confirm_text").format(name=name, max=registry.MAX_LEVEL))
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            store.choose(pet_id)
        except PermissionError as error:
            QMessageBox.information(self, name, self.t(str(error)).format(max=registry.MAX_LEVEL))
        self._apply()

    def _download_pictures(self) -> None:
        from app.gui import pet_download

        if pet_download.download(self, self.t):
            for card in self.cards:                       # previews were blank without the pictures
                card.setParent(None); card.deleteLater()
            self.cards = []
            self._apply()

    def _look_chosen(self, index: int) -> None:
        state = store.load()
        if state["current"] is None or index < 0:
            return
        store.set_look(state["current"], int(self.look_combo.itemData(index)))
        self._apply()

    def _float_toggled(self, checked: bool) -> None:
        store.set_float_on_top(checked)
        self._apply()

    def _show_toggled(self, checked: bool) -> None:
        store.set_enabled(checked)
        self._apply()

    def _apply(self) -> None:
        from app.pet.manager import manager

        mgr = manager()
        if mgr is not None:
            mgr.apply()
        self.refresh()

    def _licenses_changed(self, *_args) -> None:
        from app.pet.manager import manager

        mgr = manager()
        used = mgr.apply_potions() if mgr is not None else []
        if not used:
            from app._guard import gate

            used = store.apply_potions(gate.pet_potions())
        self.refresh()
        self._apply()
        if used:
            QMessageBox.information(self, self.t("pet.page_title"), self.t("pet.potion.applied").format(
                what=", ".join(self._potion_name(f) for f in used)))

    def _potion_name(self, f: str) -> str:
        if f.startswith("pet_exp_"):
            return self.t("license.potion_exp").format(n=f[8:])
        if f.startswith("pet_level_"):
            return self.t("license.potion_level").format(level=f[10:])
        return self.t("license.potion_reselect")
