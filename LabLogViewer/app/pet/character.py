"""Character packs: one shared event vocabulary, per-character clips.

A pack is a JSON file (characters/<id>.json):
  {"id": "cute_cat", "name": "...", "levels": {"1": "<sprite dir relative to the json>"},
   "events": {"idle": "idle", "click": "click", ...}, "display_scale": 0.5}
The sprite dir holds meta.json (written by the Blender exporter) and <clip>/frame_XXXX.png.
Missing clips fall back so every character answers every event (see FALLBACK).
"""
import json
from pathlib import Path

from app.pet.pack import resolve as _resolve

# Shared pet events. Function events (receive, recent, notify, dock, dashboard) are raised by
# LabLogViewer; interaction events by the pet window itself. Animations never award EXP.
EVENTS = ('idle', 'click', 'drag', 'land', 'receive', 'recent', 'notify', 'alert',
          'dock', 'dashboard', 'milestone', 'levelup', 'sleep', 'wake', 'trueform')
FALLBACK = {'receive': 'click', 'recent': 'click', 'notify': 'alert', 'alert': 'click',
            'dock': 'click', 'dashboard': 'click', 'milestone': 'dashboard', 'levelup': 'click',
            'wake': 'land', 'sleep': 'idle', 'trueform': 'click', 'land': 'idle', 'drag': 'idle', 'click': 'idle'}


class Clip:
    def __init__(self, name, frames, loop, eyes, fps, offset=(0, 0)):
        self.name = name
        self.frames = frames          # list of image paths
        self.loop = loop
        self.eyes = eyes              # per-frame eye ellipses (pixels in full-canvas sprite space)
        self.fps = fps
        self.offset = tuple(offset)   # where a cropped frame sits on the full canvas

    def __len__(self):
        return len(self.frames)


class Character:
    def __init__(self, pack_path, level=1):
        pack_path = Path(pack_path)
        self.pack = json.loads(pack_path.read_text(encoding='utf-8'))
        self.id = self.pack['id']
        self.name = self.pack.get('name', self.id)
        levels = {int(k): v for k, v in self.pack['levels'].items()}
        # use the highest unlocked level whose sprites are actually present
        candidates = [lv for lv in sorted(levels, reverse=True) if lv <= level] or [min(levels)]
        self.available_levels = [lv for lv in sorted(levels)
                                 if (_resolve(pack_path.parent, levels[lv]) / 'idle').is_dir()]
        for lv in candidates:
            root = _resolve(pack_path.parent, levels[lv]).resolve()
            if (root / 'meta.json').exists() and (root / 'idle').is_dir():
                break
        else:
            raise FileNotFoundError(f'{pack_path}: no rendered level at or below {level}')
        self.level = lv
        self.root = root
        self.meta = json.loads((self.root / 'meta.json').read_text(encoding='utf-8'))
        self.fps = self.meta['fps']
        self.size = tuple(self.meta['size'])
        self.anchor = tuple(self.meta.get('anchor') or (self.size[0] / 2, self.size[1]))
        self.pupil_ratio = self.pack.get('pupil_radius_ratio', self.meta.get('pupil_radius_ratio', 0.085))
        self.display_scale = self.pack.get('display_scale', 0.5)
        sp = self.pack.get('secret_painting')
        self.secret_painting = _resolve(pack_path.parent, sp) if sp else None
        self.eye_style = self.pack.get('eye_style', self.meta.get('eye_style', 'pupil'))
        self.clips = {}
        for name, info in self.meta['clips'].items():
            d = self.root / name
            ext = self.meta.get('ext', 'png')
            frames = [d / f'frame_{i + 1:04d}.{ext}' for i in range(info['frames'])]
            if all(f.exists() for f in frames):
                self.clips[name] = Clip(name, frames, info['loop'], info['eyes'], info.get('fps', self.fps),
                                        info.get('offset', (0, 0)))
        if 'idle' not in self.clips:
            raise FileNotFoundError(f'{self.root}: idle clip frames are missing')
        self.events = self.pack.get('events', {})

    def clip_for(self, event):
        """Resolve a shared event to one of this character's clips, following fallbacks."""
        seen = set()
        while event not in seen:
            seen.add(event)
            name = self.events.get(event, event)
            if name in self.clips:
                return self.clips[name]
            event = FALLBACK.get(event, 'idle')
        return self.clips['idle']
