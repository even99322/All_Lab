"""Occasional cursor-watching for the pet's eyes (pure Python, no Qt).

Behaviour
- Asleep: never watches; pupils ease back to their rest position.
- Awake: mostly "wander" (rest pose with a slow, small drift). Every so often, or when the
  cursor moves near the pet, it switches to "watch" for a few seconds and the pupils follow
  the cursor, then looks away again and waits out a cooldown. It is a glance, not a stare.
- Output is a smoothed, normalised offset (x, y) in [-1, 1]; +x is screen right, +y is down.
"""
import math
import random


class Gaze:
    def __init__(self, rng=None, watch_every=(7.0, 18.0), watch_for=(2.0, 4.5), cooldown=4.0,
                 near_px=320.0, near_chance=0.35, reach_px=260.0, ease=9.0):
        self.rng = rng or random.Random()
        self.watch_every = watch_every
        self.watch_for = watch_for
        self.cooldown = cooldown
        self.near_px = near_px
        self.near_chance = near_chance
        self.reach_px = reach_px
        self.ease = ease
        self.state = 'wander'
        self.timer = self._next_wait()
        self.cool = 0.0
        self.offset = [0.0, 0.0]
        self._wander_phase = self.rng.uniform(0, math.tau)
        self._last_cursor = None

    def _next_wait(self):
        return self.rng.uniform(*self.watch_every)

    def _start_watch(self):
        self.state = 'watch'
        self.timer = self.rng.uniform(*self.watch_for)

    def update(self, dt, eye_centre, cursor, awake=True):
        """eye_centre, cursor: global screen points (x, y). Returns the smoothed offset."""
        target = (0.0, 0.0)
        moved = 0.0
        if cursor is not None and self._last_cursor is not None:
            moved = math.hypot(cursor[0] - self._last_cursor[0], cursor[1] - self._last_cursor[1])
        self._last_cursor = cursor
        if not awake:
            self.state = 'wander'
            self.timer = max(self.timer, 1.0)
        else:
            self.cool = max(0.0, self.cool - dt)
            self.timer -= dt
            if self.state == 'wander':
                near = (cursor is not None and math.hypot(cursor[0] - eye_centre[0], cursor[1] - eye_centre[1]) < self.near_px)
                poke = near and moved > 3.0 and self.cool <= 0.0 and self.rng.random() < self.near_chance * dt * 4
                if self.timer <= 0.0 or poke:
                    self._start_watch()
            elif self.timer <= 0.0:
                self.state = 'wander'
                self.timer = self._next_wait()
                self.cool = self.cooldown
            if self.state == 'watch' and cursor is not None:
                dx, dy = cursor[0] - eye_centre[0], cursor[1] - eye_centre[1]
                d = math.hypot(dx, dy)
                if d > 1e-6:
                    k = min(1.0, d / self.reach_px)
                    target = (dx / d * k, dy / d * k)
            else:
                self._wander_phase += dt * 0.35
                target = (0.10 * math.sin(self._wander_phase), 0.05 * math.sin(self._wander_phase * 1.7))
        a = 1.0 - math.exp(-self.ease * dt)
        self.offset[0] += (target[0] - self.offset[0]) * a
        self.offset[1] += (target[1] - self.offset[1]) * a
        return tuple(self.offset)
