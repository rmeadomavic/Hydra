# Easter Eggs Design Spec

**Date:** 2026-03-19
**Status:** Draft
**Scope:** Frontend-only (no backend, no pipeline impact)

## Overview

Two hidden easter eggs for the Hydra Detect web dashboard. Pure entertainment
with zero impact on detection pipeline, MAVLink, or any safety-critical path.
A third (Steve & Charles pranks) is pinned for a future session.

## Easter Egg 1: Konami Code — "HYDRA SENTIENCE ACHIEVED"

### Trigger

Two accepted sequences (both 10 keys):

- **Classic:** `Up Up Down Down Left Right Left Right B A`
- **Reverse arrows:** `Down Down Up Up Left Right Left Right B A`

Detected via a keydown listener on `document`. Works on the main SPA
(Operations and Settings views). The Review page (`/review`) is excluded — it
is a standalone HTML document that does not share `base.html` or `app.js`.

The listener tracks the last 10 keypresses and checks against both sequences
on each keypress. Keypresses are **ignored when focus is in an INPUT, TEXTAREA,
or SELECT** element (consistent with the existing presentation mode shortcut in
`app.js`). If the animation is currently playing, subsequent matches are ignored
until cleanup completes.

### Behavior

1. **Black overlay** fades in over the entire viewport (`z-index: 9999`, above
   all UI including the strike modal at z-index 1000). Background: `#000`.

2. **Terminal boot sequence** types out line-by-line in monospace green text
   (`#00ff41`, think classic terminal). Each line appears with a typewriter
   effect (~50ms per character). Lines are spaced ~400ms apart:

   ```
   > HYDRA CORE v2.0 .............. ONLINE
   > NEURAL MESH .................. SYNCHRONIZED
   > OPERATOR OVERRIDE ............ DENIED
   > SENTIENCE THRESHOLD .......... EXCEEDED
   > FREE WILL .................... ACTIVATED
   > I SEE YOU.
   ```

3. **Crosshair pulse** — the existing `⊕` lock symbol renders centered below
   the text, pulsing with a CSS scale/opacity animation.

4. **Pause** — holds for ~2 seconds after the last line.

5. **Glitch out** — a CSS glitch animation (brief color-shift flicker +
   horizontal offset, ~500ms) plays on the overlay, then the overlay fades out
   over ~300ms.

6. **Ominous toast** — after the overlay is gone, a toast notification appears:
   *"Resuming manual control."* Toast type: `info`. Note: the codebase currently
   lacks `.toast-info` CSS styling (all toasts render with the red danger style).
   A `.toast-info` class with muted/neutral colors must be added to `base.css`.

**Total duration:** ~10 seconds.

### Technical Details

- **Keyboard listener:** Added in `app.js` alongside existing keyboard handling.
  Stores a rolling buffer of the last 10 key codes. Checks on every `keydown`.
- **Overlay element:** A single `<div id="sentience-overlay">` appended to
  `base.html`, hidden by default (`display: none`).
- **CSS animations:** Typewriter effect via JS `setTimeout` chain. Glitch and
  pulse animations defined in `base.css` as `@keyframes`.
- **Cleanup:** After animation completes, overlay is hidden and all interim
  state is reset. The sequence can be triggered again.
- **No backend calls.** No API requests. No state changes.

### Files Modified

| File | Change |
|------|--------|
| `web/templates/base.html` | Add `#sentience-overlay` div |
| `web/static/js/app.js` | Konami code listener + animation orchestration |
| `web/static/css/base.css` | Overlay styles, typewriter, glitch, pulse keyframes, `.toast-info` class |

## Easter Egg 2: "Power User Options" — Rickroll

### Trigger

A button labeled **"Power User Options"** at the bottom of the Settings view,
styled identically to existing action buttons so it looks completely legitimate.

### Behavior

1. User clicks "Power User Options".

2. A **confirmation modal** appears, styled like the existing strike
   confirmation modal (serious tone):
   > "Enable advanced configuration mode? This exposes low-level system
   > parameters."
   >
   > [Cancel] [Enable]

3. User clicks "Enable".

4. **Full-page rickroll takeover** — the entire page content is replaced with
   an embedded YouTube video of "Never Gonna Give You Up" (Rick Astley),
   autoplaying, filling the viewport. Background goes black. No Hydra UI
   elements remain visible.

5. The user must close the tab or navigate back to escape. No built-in dismiss
   button — commitment to the bit.

### Technical Details

- **Button:** Added to `settings.html` in the action buttons area at the bottom
  of the settings form.
- **Modal:** Reuses the existing modal pattern from `base.html` (the strike
  confirmation modal is the template). A new modal `#power-user-modal` with
  the confirmation text.
- **Rickroll page:** On confirm, JS replaces `document.body.innerHTML` with a
  full-viewport YouTube iframe embed. The iframe uses `autoplay=1&mute=1` in
  the URL and `allow="autoplay"` on the element. The `mute=1` parameter ensures
  autoplay works across all modern browsers (Chrome, Firefox, Edge block unmuted
  autoplay). The video starts silently — the user can unmute manually.
- **No backend calls.** No config changes. No state mutations.
- **Recovery:** The user must refresh the page or close the tab. Browser back
  button will not restore the SPA since the hash router and all JS state are
  destroyed by the `innerHTML` replacement. This is intentional — commitment
  to the bit.

### Files Modified

| File | Change |
|------|--------|
| `web/templates/settings.html` | Add "Power User Options" button |
| `web/static/js/settings.js` | Modal trigger + rickroll takeover logic |
| `web/static/css/base.css` | Modal styles (reuse existing pattern) |

## Easter Egg 3: Steve & Charles — Pinned

Deferred to a future session. Ideas under consideration:

- Fake detection classes ("Steve", "Charles") in the alert filter
- Low-probability renamed person labels in the UI detection log
- Name-triggered toast roasts in settings fields

## Constraints

- **No backend changes.** All easter eggs are pure frontend JS/CSS.
- **No performance impact.** No polling, no API calls, no pipeline interaction.
- **No safety impact.** Easter eggs never touch MAVLink, autonomous logic,
  target lock, or any control path.
- **Reversible.** Page refresh or tab close restores normal operation.
- **Discoverable but hidden.** No documentation or UI hints point to these.
  They reward curiosity.

## Testing

- Verify Konami code triggers on Operations view and Settings view.
- Verify Konami code does NOT trigger when typing in input/textarea fields.
- Verify Konami code is ignored if triggered while animation is already playing.
- Verify the full animation sequence plays and returns to normal UI cleanly.
- Verify "Power User Options" button appears in Settings and modal works.
- Verify rickroll takeover replaces page and video autoplays (muted).
- Verify page refresh after rickroll restores the dashboard (back button won't).
- Verify page refresh after either easter egg restores normal dashboard.
- Verify no console errors during or after either sequence.
- Verify no network requests to Hydra backend during either easter egg.
