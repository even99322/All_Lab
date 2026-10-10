"""Frameless, transparent, always-on-top pet window (PySide6).

It is an ordinary top-level window owned by the host application, so it can be dragged
anywhere on any monitor but closes together with the program. Sprite frames come from the
character pack (loaded lazily, current clip + idle kept decoded); the pupils are painted live
so the pet can glance at the cursor. Small overlay effects (heart, "!", sparkles, ...) are
drawn here too. No animation or interaction awards EXP.
"""
import math
import random
import time

from PySide6.QtCore import Qt, QTimer, QPointF, QPoint, QRectF, Signal
from PySide6.QtGui import QPainter, QPixmap, QColor, QPainterPath, QFont, QGuiApplication, QAction, QPen
from PySide6.QtWidgets import QWidget, QMenu

from .gaze import Gaze
from .eyes import pupil_positions

DRAG_THRESHOLD = 6           # px before a press becomes a drag
BAR_H = 44                   # transparent strip under the pet for the hover tool bar (Codex-style)
AUTO_SLEEP_SECONDS = 180     # cursor still this long -> nap (preview behaviour)
SECRET_CLICKS = (7, 3.0)     # easter egg: 7 clicks within 3 s
SECRET_RANDOM_PER_SEC = 1.5 / (8 * 3600)   # ~1-2 times per working day while idle


class Floater:
    """Short-lived overlay effect drawn above the pet."""

    def __init__(self, kind, x, y, life=1.2, seed=0.0):
        self.kind, self.x, self.y, self.life, self.age, self.seed = kind, x, y, life, 0.0, seed


class PetWindow(QWidget):
    event_played = Signal(str)          # shared event name, for the host app / tests
    level_changed = Signal(int)
    settings_requested = Signal()
    hide_requested = Signal()
    bar_clicked = Signal(str)            # "panel" or a quick-action id
    activate_requested = Signal()        # a plain click: bring LabLogViewer to the front
    dropped = Signal(object)             # QMimeData dropped on the pet (backpack, Lv.10+)

    def __init__(self, character, parent=None, rng=None, loader=None, text=None, developer=False):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)     # stay visible while another program is in front
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.rng = rng or random.Random()
        self.loader = loader                # callable(level) -> Character, for the level menu
        self.text = text or (lambda key, default: default)
        self.developer = developer          # test events / level preview only for developers
        self.gaze = Gaze(self.rng)
        self.awake = True
        self.floaters = []
        self._press = None
        self._dragging = False
        self._grab_offset = QPoint()
        self._last = time.monotonic()
        self._still_since = time.monotonic()
        self._last_cursor = None
        self._hover = False
        self._bar_alpha = 0.0
        self._bar_items = []             # [(id, glyph, tooltip)]
        self._bar_rects = []
        self._bar_press = None
        self._clicks = []
        self._flags_seen = set()
        self._set_character(character)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    def set_float_on_top(self, on: bool) -> None:
        """Keep the pet above every program (default) or let ordinary windows cover it."""
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) == on:
            return
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()

    # ------------------------------------------------------------------ character / assets
    def _set_character(self, character, keep_feet=False):
        old_anchor = None
        if keep_feet and hasattr(self, 'char'):
            old_anchor = self.mapToGlobal(QPoint(int(self.char.anchor[0] * self.scale), int(self.char.anchor[1] * self.scale)))
        self.char = character
        self.scale = character.display_scale
        w, h = character.size
        self.sprite_h = int(h * self.scale)
        # the hover bar sits just under the feet (sprites have empty space below the shadow)
        self.bar_top = int(character.anchor[1] * self.scale) + 2
        self.setFixedSize(int(w * self.scale), max(self.sprite_h, self.bar_top + BAR_H))
        self.cache = {}
        idle0 = character.clips['idle'].eyes[0]
        self.pupil_r = idle0['right']['rx'] * character.pupil_ratio
        self.clip = character.clip_for('idle')
        self.frame, self.acc, self.after = 0, 0.0, None
        if old_anchor is not None:
            self.move(old_anchor - QPoint(int(character.anchor[0] * self.scale), int(character.anchor[1] * self.scale)))

    def _pixmap(self, clip, i):
        frames = self.cache.get(clip.name)
        if frames is None:
            frames = self.cache[clip.name] = [None] * len(clip)
        if frames[i] is None:
            pm = QPixmap(str(clip.frames[i]))
            pm.setDevicePixelRatio(1.0 / self.scale)    # 2x sprites shown at logical half size
            frames[i] = pm
        return frames[i]

    def _trim_cache(self):
        for name in list(self.cache):
            if name not in ('idle', self.clip.name):
                del self.cache[name]

    # ------------------------------------------------------------------ public API (host app)
    def trigger(self, event):
        """Play the character's clip for a shared pet event (receive, notify, dock, ...)."""
        if not self.awake and event not in ('wake', 'alert'):
            return False
        if not self.awake:
            self.awake = True
        clip = self.char.clip_for(event)
        then = None if clip.loop else self.char.clip_for('idle')
        if event == 'milestone' and self.has_secret() and self.rng.random() < 0.15:
            then = self.char.clips['trueform']          # rare: the true form follows the ceremony
        self._play(clip, then=then)
        self._effect(event)
        self.event_played.emit(event)
        return True

    def set_awake(self, awake):
        if awake == self.awake:
            return
        if awake:
            self.awake = True
            self.trigger('wake')
        else:
            self.awake = False
            self._play(self.char.clip_for('sleep'))
            self.event_played.emit('sleep')

    def set_level(self, level):
        """Swap to another unlocked level's look; moving up plays the level-up celebration."""
        if self.loader is None or level == self.char.level:
            return
        up = level > self.char.level
        self._set_character(self.loader(level), keep_feet=True)
        self.awake = True
        if up:
            self.trigger('levelup')
        self.level_changed.emit(self.char.level)

    def say(self, text: str) -> None:
        """Short speech bubble (e.g. why a quick action could not run)."""
        f = Floater('say', self.width() * 0.5, self.sprite_h * 0.12, 2.6)
        f.text = text
        self.floaters.append(f)

    def set_bar_items(self, items) -> None:
        """Buttons of the hover bar: [(id, glyph, tooltip)], shown only while the mouse is over the pet."""
        self._bar_items = list(items)
        self.update()

    def place_bottom_right(self):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - self.width() - 40, scr.bottom() - self.height() - 20)

    # ------------------------------------------------------------------ playback
    def _play(self, clip, then=None):
        self.clip, self.frame, self.acc, self.after = clip, 0, 0.0, then
        self._flags_seen = set()
        self._trim_cache()

    def has_secret(self):
        return 'trueform' in self.char.clips

    def _tick(self):
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        self.acc += dt * self.clip.fps
        while self.acc >= 1.0:
            self.acc -= 1.0
            self.frame += 1
            if self.frame >= len(self.clip):
                if self.clip.loop:
                    self.frame = 0
                else:
                    nxt = self.after or self.char.clip_for('idle' if self.awake else 'sleep')
                    self._play(nxt)
        gpos = self.cursor().pos()
        cur = (gpos.x(), gpos.y())
        if cur != self._last_cursor:
            if self._last_cursor is not None and not self.awake and not self._dragging:
                self.set_awake(True)               # user is back
            self._last_cursor = cur
            self._still_since = now
        elif self.awake and self.clip.name == 'idle' and now - self._still_since > AUTO_SLEEP_SECONDS:
            self.set_awake(False)
        eyes = self.clip.eyes[self.frame]
        for flag, kind in (('quack', 'quack'), ('stamp', 'stamp'), ('bark', 'bark')):
            if eyes.get(flag) and flag not in self._flags_seen:
                self._flags_seen.add(flag)
                self.floaters.append(Floater(kind, self.width() * 0.62, self.height() * 0.30, 1.0))
        if (self.has_secret() and self.awake and self.clip.name == 'idle'
                and self.rng.random() < SECRET_RANDOM_PER_SEC * dt):
            self.trigger('trueform')
        vis = [eyes[k] for k in ('right', 'left') if k in eyes]
        mid = QPointF(sum(e['cx'] for e in vis) / len(vis) * self.scale, sum(e['cy'] for e in vis) / len(vis) * self.scale)
        centre = self.mapToGlobal(mid.toPoint())
        usable = self.awake and not self._dragging and self.clip.name == 'idle'
        self.gaze.update(dt, (centre.x(), centre.y()), cur, awake=usable)
        target = 1.0 if (self._hover and self._bar_items and not self._dragging) else 0.0
        self._bar_alpha += (target - self._bar_alpha) * min(1.0, dt * 12)
        for f in self.floaters:
            f.age += dt
        self.floaters = [f for f in self.floaters if f.age < f.life]
        if not self.awake and self.rng.random() < dt * 0.6:
            self.floaters.append(Floater('z', self.width() * 0.68, self.height() * 0.30, 2.4, self.rng.random()))
        self.update()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        eyes = self.clip.eyes[self.frame]
        if eyes.get('tf_on') and self._painting() is not None:
            self._paint_portrait(p)
            p.end()
            return
        ox, oy = self.clip.offset
        p.drawPixmap(QPointF(ox * self.scale, oy * self.scale), self._pixmap(self.clip, self.frame))
        if self.char.eye_style == 'dot':
            self._paint_dot_eyes(p, eyes)
        elif self.awake or self.clip.name != 'sleep':
            gaze = eyes.get('gaze') or self.gaze.offset
            p.setPen(Qt.NoPen)
            for px, py, r in pupil_positions(eyes, gaze, self.pupil_r, self.scale):
                p.setBrush(QColor(28, 26, 26))
                p.drawEllipse(QPointF(px, py), r, r * 1.05)
                p.setBrush(QColor(255, 255, 255, 170))
                p.drawEllipse(QPointF(px + r * 0.35, py - r * 0.4), r * 0.28, r * 0.28)
        if eyes.get('tf_on'):
            self._paint_peeker(p)
        for f in self.floaters:
            self._paint_floater(p, f)
        self._paint_bar(p)
        p.end()

    def _paint_dot_eyes(self, p, eyes):
        """Shiba-style eyes: the whole eye is a painted black dot with expressions."""
        s = self.scale
        rest = self.char.clips['idle'].eyes[0]
        expr = eyes.get('expr')
        if not self.awake and self.clip.name == 'sleep':
            expr = 'closed'
        gx, gy = eyes.get('gaze') or self.gaze.offset
        ink = QColor(30, 26, 24)
        for key in ('right', 'left'):
            if key not in eyes or key not in rest:
                continue                      # e.g. a three-quarter view shows one eye
            e, r0 = eyes[key], rest[key]
            cx, cy = e['cx'] * s, e['cy'] * s
            rx, ry0 = r0['rx'] * s, r0['ry'] * s
            open_ = e['ry'] / max(r0['ry'], 1e-6)
            pen = QPen(ink); pen.setWidthF(max(1.6, rx * 0.35)); pen.setCapStyle(Qt.RoundCap); p.setPen(pen); p.setBrush(Qt.NoBrush)
            side = -1 if key == 'right' else 1
            if expr == 'happy':
                path = QPainterPath(); path.moveTo(cx - rx * 1.1, cy + ry0 * 0.3)
                path.quadTo(cx, cy - ry0 * 1.2, cx + rx * 1.1, cy + ry0 * 0.3); p.drawPath(path)
            elif expr == 'closed' or open_ < 0.35:
                path = QPainterPath(); path.moveTo(cx - rx * 1.1, cy)
                path.quadTo(cx, cy + ry0 * 0.7, cx + rx * 1.1, cy); p.drawPath(path)
            elif expr == 'squint':
                path = QPainterPath()
                path.moveTo(cx - side * rx, cy - ry0 * 0.8); path.lineTo(cx + side * rx * 0.9, cy); path.lineTo(cx - side * rx, cy + ry0 * 0.8)
                p.drawPath(path)
            else:
                ox, oy = gx * rx * 0.45, gy * ry0 * 0.40
                h = ry0 * min(1.15, open_)
                p.setPen(Qt.NoPen); p.setBrush(ink)
                if expr == 'smug':
                    path = QPainterPath(); path.moveTo(cx - rx + ox, cy + oy)
                    path.arcTo(QRectF(cx - rx + ox, cy - h + oy, 2 * rx, 2 * h), 180, 180); path.closeSubpath(); p.drawPath(path)
                    pen = QPen(ink); pen.setWidthF(max(1.6, rx * 0.3)); p.setPen(pen)
                    p.drawLine(QPointF(cx - rx * 1.2, cy - h * 0.2 - side * 2), QPointF(cx + rx * 1.2, cy - h * 0.2 + side * 2))
                else:
                    p.drawEllipse(QPointF(cx + ox, cy + oy), rx, h)
                    p.setBrush(QColor(255, 255, 255, 230))
                    p.drawEllipse(QPointF(cx + ox + rx * 0.35, cy + oy - h * 0.38), rx * 0.30, rx * 0.30)

    def _paint_bar(self, p):
        self._bar_rects = []
        if self._bar_alpha < 0.03 or not self._bar_items:
            return
        bw, gap, bh, pad = 26, 3, 26, 6
        fit = max(1, int((self.width() - 2 * pad + gap) // (bw + gap)))
        items = self._bar_items[:fit]                       # the bar never grows wider than the pet
        n = len(items)
        width = n * bw + (n - 1) * gap + 2 * pad
        x0 = (self.width() - width) / 2
        y0 = self.bar_top
        p.setOpacity(self._bar_alpha)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(28, 28, 32, 235))
        p.drawRoundedRect(QRectF(x0, y0, width, bh + 8), (bh + 8) / 2, (bh + 8) / 2)
        font = QFont('Helvetica', 9, QFont.Bold)
        p.setFont(font)
        cursor = self.mapFromGlobal(self.cursor().pos())
        for i, (item_id, glyph, _tip) in enumerate(items):
            r = QRectF(x0 + pad + i * (bw + gap), y0 + 4, bw, bh)
            self._bar_rects.append((r, item_id))
            if r.contains(QPointF(cursor)):
                p.setBrush(QColor(255, 255, 255, 40)); p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor(235, 235, 240))
            p.drawText(r, Qt.AlignCenter, glyph)
            p.setPen(Qt.NoPen)
        p.setOpacity(1.0)

    def _bar_item_at(self, pos):
        for r, item_id in self._bar_rects:
            if r.contains(QPointF(pos)):
                return item_id
        return None

    def dragEnterEvent(self, ev):
        md = ev.mimeData()
        if md.hasUrls() or md.hasImage() or md.hasText():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        ev.acceptProposedAction()
        self.dropped.emit(ev.mimeData())

    def enterEvent(self, _ev):
        self._hover = True

    def leaveEvent(self, _ev):
        self._hover = False

    def _painting(self):
        path = getattr(self.char, 'secret_painting', None)
        if path is None or not path.exists():
            return None
        if getattr(self, '_painting_pm', None) is None or self._painting_path != path:
            self._painting_pm, self._painting_path = QPixmap(str(path)), path
        return self._painting_pm

    def _paint_portrait(self, p):
        """Easter egg: the true form shown as a framed oil painting, fading in and out."""
        eyes_all = self.clip.eyes
        on = [i for i, e in enumerate(eyes_all) if e.get('tf_on')]
        first, last = on[0], on[-1]
        fade = min(1.0, (self.frame - first + self.acc) / 5.0, (last - self.frame + 1 - self.acc) / 5.0)
        pm = self._painting()
        W, H = self.width(), self.height()
        k = min(W * 0.86 / pm.width(), H * 0.96 / pm.height()) * (0.92 + 0.08 * fade)
        w, h = pm.width() * k, pm.height() * k
        p.setOpacity(max(0.0, fade))
        p.drawPixmap(QRectF((W - w) / 2, (H - h) / 2, w, h).toRect(), pm)
        p.setOpacity(1.0)
        self._paint_peeker(p)

    def _paint_peeker(self, p):
        # the tiny chibi peeking from the corner while the true form shows off
        idle = self.char.clips['idle']
        pm = self._pixmap(idle, 0)
        k = 0.30
        w, h = pm.width() / pm.devicePixelRatio() * k, pm.height() / pm.devicePixelRatio() * k
        bob = 3 * math.sin(time.monotonic() * 4)
        p.save()
        p.setClipRect(0, int(self.height() * 0.70), int(w * 1.2), int(self.height() * 0.30))
        p.drawPixmap(QRectF(-w * 0.12, self.height() - h * 0.62 + bob, w, h).toRect(), pm)
        p.restore()

    def _effect(self, event):
        w, h = self.width(), self.height()
        if event == 'click':
            self.floaters.append(Floater('heart', w * 0.5, h * 0.20))
        elif event == 'alert':
            self.floaters.append(Floater('alert', w * 0.5, h * 0.14, 1.6))
        elif event == 'notify':
            self.floaters.append(Floater('ring', w * 0.5, h * 0.18, 1.3))
        elif event == 'receive':
            self.floaters.append(Floater('item', w * 0.86, h * 0.10, 0.6))
        elif event == 'recent':
            self.floaters.append(Floater('think', w * 0.80, h * 0.20, 1.5))
        elif event == 'trueform':
            pass
        elif event in ('levelup', 'dashboard', 'milestone'):
            n = {'levelup': 18, 'dashboard': 10, 'milestone': 26}[event]
            for _ in range(n):
                self.floaters.append(Floater('spark', w * 0.5, h * 0.55, 1.1 + 0.6 * self.rng.random(), self.rng.random() * math.tau))

    def _paint_floater(self, p, f):
        k = f.age / f.life
        alpha = int(255 * (1 - k) ** 0.7)
        if f.kind == 'heart':
            y = f.y - 40 * k
            sz = 9 + 5 * math.sin(min(1.0, k * 3) * math.pi / 2)
            path = QPainterPath()
            path.moveTo(f.x, y + sz * 0.9)
            path.cubicTo(f.x - sz * 1.6, y - sz * 0.2, f.x - sz * 0.5, y - sz * 1.3, f.x, y - sz * 0.35)
            path.cubicTo(f.x + sz * 0.5, y - sz * 1.3, f.x + sz * 1.6, y - sz * 0.2, f.x, y + sz * 0.9)
            p.setPen(Qt.NoPen); p.setBrush(QColor(255, 105, 120, alpha)); p.drawPath(path)
        elif f.kind == 'alert':
            p.setPen(QColor(230, 70, 50, alpha)); p.setFont(QFont('Helvetica', 26, QFont.Black))
            p.drawText(QPointF(f.x - 6, f.y - 20 * min(1.0, k * 4)), '!')
        elif f.kind == 'z':
            p.setPen(QColor(90, 110, 150, alpha)); p.setFont(QFont('Helvetica', int(11 + 8 * k), QFont.Bold))
            p.drawText(QPointF(f.x + 18 * k + 6 * math.sin(f.seed * 6 + k * 5), f.y - 45 * k), 'z')
        elif f.kind == 'ring':
            p.setBrush(Qt.NoBrush)
            for j in range(3):
                kk = k * 1.6 - j * 0.25
                if 0 < kk < 1:
                    pen = QPen(QColor(240, 180, 40, int(220 * (1 - kk)))); pen.setWidthF(2.2); p.setPen(pen)
                    r = 10 + 34 * kk
                    p.drawArc(int(f.x - r), int(f.y - r), int(2 * r), int(2 * r), 30 * 16, 120 * 16)
        elif f.kind == 'item':
            # a little document flies into the pet's mouth
            x = f.x + (self.width() * 0.5 - f.x) * k
            y = f.y + (self.height() * 0.55 - f.y) * k - 30 * math.sin(k * math.pi)
            s = 14 * (1 - 0.6 * k)
            p.setPen(QPen(QColor(120, 130, 150, alpha), 1.2)); p.setBrush(QColor(255, 255, 255, alpha))
            p.drawRoundedRect(int(x - s / 2), int(y - s * 0.65), int(s), int(s * 1.3), 2, 2)
        elif f.kind == 'think':
            p.setPen(QPen(QColor(150, 160, 175, alpha), 1.4)); p.setBrush(QColor(255, 255, 255, alpha))
            grow = min(1.0, k * 3)
            for j, (dx, dy, r) in enumerate(((-16, 20, 3), (-9, 11, 5), (4, -4, 15))):
                if grow > j / 3:
                    p.drawEllipse(QPointF(f.x + dx, f.y + dy), r, r * 0.85)
            if grow >= 1:
                p.setPen(QColor(110, 120, 140, alpha)); p.setFont(QFont('Helvetica', 11, QFont.Bold))
                p.drawText(QPointF(f.x - 8, f.y), '...')
        elif f.kind == 'quack':
            p.setPen(QPen(QColor(120, 120, 130, alpha), 1.4)); p.setBrush(QColor(255, 255, 255, alpha))
            p.drawRoundedRect(int(f.x), int(f.y - 34 - 10 * k), 52, 26, 10, 10)
            p.setPen(QColor(60, 60, 70, alpha)); p.setFont(QFont('PingFang TC', 13, QFont.Bold))
            p.drawText(QPointF(f.x + 8, f.y - 15 - 10 * k), '呱！')
        elif f.kind == 'say':
            font = QFont('PingFang TC', 11, QFont.Bold)
            p.setFont(font)
            fm = p.fontMetrics()
            tw = min(self.width() - 8, fm.horizontalAdvance(f.text) + 18)
            rect = QRectF((self.width() - tw) / 2, f.y - 10 * k, tw, 26)
            p.setPen(QPen(QColor(120, 120, 130, alpha), 1.2)); p.setBrush(QColor(255, 255, 255, alpha))
            p.drawRoundedRect(rect, 10, 10)
            p.setPen(QColor(50, 50, 60, alpha))
            p.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, f.text)
        elif f.kind == 'bark':
            p.setPen(QPen(QColor(120, 120, 130, alpha), 1.4)); p.setBrush(QColor(255, 255, 255, alpha))
            p.drawRoundedRect(int(f.x), int(f.y - 34 - 10 * k), 52, 26, 10, 10)
            p.setPen(QColor(200, 90, 40, alpha)); p.setFont(QFont('PingFang TC', 13, QFont.Bold))
            p.drawText(QPointF(f.x + 8, f.y - 15 - 10 * k), '汪！')
        elif f.kind == 'stamp':
            p.setPen(QPen(QColor(200, 30, 30, alpha), 3)); p.setBrush(Qt.NoBrush)
            r = 18 + 6 * (1 - k)
            p.drawRect(int(self.width() * 0.5 - r), int(self.height() * 0.62 - r), int(2 * r), int(2 * r))
            p.setFont(QFont('PingFang TC', 14, QFont.Black))
            p.drawText(QPointF(self.width() * 0.5 - 8, self.height() * 0.62 + 6), '准')
        elif f.kind == 'spark':
            d = 20 + 90 * (1 - (1 - k) ** 2)
            x = f.x + math.cos(f.seed) * d
            y = f.y + math.sin(f.seed) * d * 0.8 - 30 * k
            r = 4.5 * (1 - k) + 1
            p.setPen(Qt.NoPen); p.setBrush(QColor(255, 205, 70, alpha))
            star = QPainterPath()
            for j in range(8):
                rr = r if j % 2 == 0 else r * 0.38
                a = j * math.pi / 4 + k * 3
                pt = QPointF(x + math.cos(a) * rr, y + math.sin(a) * rr)
                if j == 0:
                    star.moveTo(pt)
                else:
                    star.lineTo(pt)
            star.closeSubpath()
            p.drawPath(star)

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, ev):
        item = self._bar_item_at(ev.position()) if self._bar_alpha > 0.5 else None
        if ev.button() == Qt.LeftButton and item is not None:
            self._bar_press = item
            return
        if ev.button() == Qt.LeftButton:
            self._press = ev.globalPosition().toPoint()
            self._grab_offset = self._press - self.frameGeometry().topLeft()
            self._dragging = False
        elif ev.button() == Qt.RightButton:
            self._menu(ev.globalPosition().toPoint())

    def mouseMoveEvent(self, ev):
        item = self._bar_item_at(ev.position()) if self._bar_alpha > 0.5 else None
        if item is not None:
            tip = next((t for i, _g, t in self._bar_items if i == item), '')
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(ev.globalPosition().toPoint(), tip, self)
        if self._press is None:
            return
        gp = ev.globalPosition().toPoint()
        if not self._dragging and (gp - self._press).manhattanLength() > DRAG_THRESHOLD:
            self._dragging = True
            self.awake = True
            self._play(self.char.clip_for('drag'))
            self.event_played.emit('drag')
        if self._dragging:
            self.move(gp - self._grab_offset)

    def mouseReleaseEvent(self, ev):
        if self._bar_press is not None:
            item = self._bar_item_at(ev.position())
            pressed, self._bar_press = self._bar_press, None
            if item == pressed:
                self.bar_clicked.emit(item)
            return
        if ev.button() != Qt.LeftButton or self._press is None:
            return
        if self._dragging:
            self._dragging = False
            self.trigger('land')
        elif not self.awake:
            self.set_awake(True)
        else:
            now = time.monotonic()
            n, window = SECRET_CLICKS
            self._clicks = [t for t in self._clicks if now - t < window] + [now]
            if self.has_secret() and len(self._clicks) >= n:
                self._clicks = []
                self.trigger('trueform')
            elif self.clip.name != 'trueform':
                self.trigger('click')
                self.activate_requested.emit()
        self._press = None

    def _menu(self, pos):
        t = self.text
        m = QMenu(self)
        a_sleep = QAction(t("pet.menu.wake", "Wake up") if not self.awake else t("pet.menu.sleep", "Sleep"), m)
        a_sleep.triggered.connect(lambda: self.set_awake(not self.awake))
        m.addAction(a_sleep)
        a_set = QAction(t("pet.menu.settings", "Pet settings…"), m)
        a_set.triggered.connect(self.settings_requested.emit)
        m.addAction(a_set)
        if self.developer:
            test = m.addMenu("Test pet events")
            evs = ['receive', 'recent', 'notify', 'alert', 'dock', 'dashboard', 'milestone']
            if self.has_secret():
                evs.append('trueform')
            for ev in evs:
                a = QAction(ev, test)
                a.triggered.connect(lambda _=False, e=ev: self.trigger(e))
                test.addAction(a)
            if self.loader is not None:
                lv = m.addMenu(f'Level (now {self.char.level})')
                for level in self.char.available_levels:
                    a = QAction(f'Lv.{level}', lv)
                    a.triggered.connect(lambda _=False, L=level: self.set_level(L))
                    lv.addAction(a)
        m.addSeparator()
        q = QAction(t("pet.menu.hide", "Hide pet"), m)
        q.triggered.connect(self.hide_requested.emit)
        m.addAction(q)
        m.exec(pos)
