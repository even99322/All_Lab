# Theme ripple, button spring, glass scroll bars
> The interface's three motion effects: what they do and why they never get in the way.

## Shortest procedure
1. **Theme ripple.** Click the theme button in the Browser toolbar (or **Settings → Appearance → Theme**). The new theme spreads as a circle from the button you pressed until it covers the farthest corner; other open windows spread from their centre. ![](motion_ripple.png)
2. **Two colours fight.** If the look does not actually change (e.g. System is already dark and you choose Dark), two colours push at the circle's edge, the edge is pushed back twice, and the theme colour wins.
3. **Button spring.** Holding any button shrinks it to 96 % and shades it along its own shape (darker on light surfaces, lighter in Dark); icon buttons on the glass toolbar show a soft round capsule. Releasing springs back with a little bounce, already showing the button's new state.
4. **Glass scroll bars.** Every vertical and horizontal scroll bar floats over the content; the handle is liquid glass that refracts what lies beneath, and it widens under the mouse for an easier grip. ![](motion_scroll.png)

## What the result means
- The new theme is fully applied before the ripple starts; the ripple is a picture of the old look on a layer that ignores mouse and keyboard, so everything works during the animation.
- The button spring is also drawn on a layer above the button; clicks and shortcuts work as usual.
- The glass uses **Settings → Appearance → Toolbar Glass** **Thickness** and **Frost**.
- Because scroll bars float over content, about 12 px at a table's right or bottom edge lies under the bar (as with native macOS scroll bars).

## If it does not work
- **Animations stutter on a slow computer**: they never block anything; to turn them off set the environment variable `LABLOGVIEWER_DISABLE_EFFECTS=1` (ripple, spring) or `LABLOGVIEWER_DISABLE_GLASS=1` (plain frosted handles).
- **A scroll bar covers the last column**: scroll a little or widen the window.
