# PRESERVATION RULES — READ BEFORE EDITING

Kyle is running an overnight Phase 2 rebuild. The following are confirmed hidden features, inside jokes, and brand invariants. **Do not delete or alter them unless your task explicitly names them.** If your task rewrites a file touching any of these, preserve them verbatim.

## Easter eggs / hidden features (confirmed live or dormant-by-accident)

### Konami code sentience sequence (currently BROKEN — JS listener got deleted)
- HTML target: `#sentience-overlay` + `#sentience-terminal` + `#sentience-crosshair` (`⊕` glyph) in `hydra_detect/web/templates/base.html:138-142`.
- CSS: `@keyframes sentience-pulse`, `@keyframes sentience-glitch`, `#sentience-*` selectors, and the Matrix-green (`#00ff41`) palette in `hydra_detect/web/static/css/base.css:247-303`.
- JS listener was deleted by commit `d71f5a3` (Apr 8 modular refactor). Verbatim source lives at `git show ed03c43:hydra_detect/web/static/js/app.js` or in `docs/superpowers/plans/2026-03-19-easter-eggs.md:170-285`.
- Sequences: classic `Up Up Down Down Left Right Left Right B A` AND reverse-arrow `Down Down Up Up L R L R B A`.
- Skips input while focus is on INPUT/TEXTAREA/SELECT.
- Plays 6-line boot: `HYDRA CORE v2.0 .............. ONLINE` / `NEURAL MESH .................. SYNCHRONIZED` / `OPERATOR OVERRIDE ............ DENIED` / `SENTIENCE THRESHOLD .......... EXCEEDED` / `FREE WILL .................... ACTIVATED` / `> I SEE YOU.`
- Ends with `showToast('Resuming manual control.', 'info')`.
- **DO NOT** delete the overlay div, the CSS keyframes, or the `.toast-info` class. They are the receiver the restored JS will target.

### Power User rickroll
- Hidden link `#settings-power-user` in `settings.html:96-98` inside `#settings-power-footer` — visible ONLY when Settings sidebar is on the `Logging` section (`settings.js:314-318`).
- Click opens serious-looking `#power-user-modal` in `base.html:144-156` saying "Enable advanced configuration mode? This exposes low-level system parameters." with `[Cancel]` `[Enable]`.
- `[Enable]` triggers `window.open` or iframe fallback to `https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=1` (`settings.js:589-620`).
- CSP in `server.py:80-88` has `frame-src https://www.youtube-nocookie.com` SOLELY for this. **DO NOT** strip that directive in any CSP hardening pass.

### Double-click fullscreen video
- `ops.js:80-84` + `stream/stream-controller.js:24,60` — `dblclick` on `#mjpeg-stream` toggles browser fullscreen. Undocumented convenience for demos.

### `/api/vehicle/beep` — no-auth buzzer endpoint
- `server.py:1672-1690`. Docstring: *"No auth required — this is a fun/debug feature, not a control action."*
- **DO NOT** "harden" this endpoint by adding auth.

### `charles` MAVLink tune
- `mavlink_io.py:867-874` — `TUNES` dict includes `"charles": "MFT255L8CDEFEDCL4C"` with comment `# Special tune for Charles`.
- Inside joke for a team member/instructor named Charles. **DO NOT** remove when "cleaning up" TUNES.

### `hud_layout` reserved schema field
- `config_schema.py:327-332` — `hud_layout ∈ {classic, operator, graphs, hybrid}` default `classic`. Currently has no consumer; Phase 2 FlightHUD work is expected to read it. **DO NOT** remove.

### B2/B3/B9 "not yet built" stub badges in `tak.html`
- Lines 8, 32, 43 — placeholders Kyle wants visible until the backend endpoints land. When a backend lands, the corresponding badge should be replaced with real data, NOT deleted silently.

## Brand invariants

- **Title**: `HYDRA DETECT — SORCC` in `base.html:6`.
- **Footer**: `UNCLASSIFIED` center + `SORCC Payload Integrator` right + dynamic callsign left.
- **SORCC SVG badge** top-left on base/login/setup/fleet pages. Palette (post-migration names):
  - `#385723` → `--olive-primary`
  - `#A6BC92` → `--ogt-muted` (kept name per token-migration ambiguity ruling)
  - `#EFF5EB` → `--ogt-light`
- **Callsign swap**: `main.js:36-42` rewrites `document.title` and topbar brand to `${callsign} — SORCC` on first `/api/stats` response. Preserve in any topbar rewrite.
- **Callsign-duplicate toast** in `main.js:45-48` — multi-team collision warning. Preserve.
- **Default callsign** `HYDRA-1` (`config.ini:198`); team format `HYDRA-{team}-{vehicle}`.
- **Sim GPS fallback** `35.0527, -79.4927` (Fort Bragg vicinity, `__main__.py:27,31`). Deliberate SOF training choice.
- **TAK port `6969`** — matches ATAK default multicast. Leave as-is.

## Files you MUST NOT wholesale-rewrite

If your task says "port/rebuild X", you still PRESERVE these blocks:

1. `hydra_detect/web/templates/base.html:138-156` (sentience overlay + power-user modal) and :161-162 (footer).
2. `hydra_detect/web/static/css/base.css:183-187` (`.toast-info`) and :247-303 (sentience keyframes).
3. `hydra_detect/web/static/js/settings.js:314-318` (Logging-only power footer visibility) and :589-620 (rickroll click handler).
4. `hydra_detect/web/server.py:80-88` (CSP with `frame-src youtube-nocookie.com`) and :1672-1690 (`/api/vehicle/beep`).
5. `hydra_detect/mavlink_io.py:867-874` (`TUNES` dict, including `charles`).
6. `hydra_detect/web/templates/settings.html:96-98` (`settings-power-footer`).
7. `hydra_detect/web/templates/tak.html:4-43` (milestone stub badges).
8. `hydra_detect/config_schema.py:327-332` (`hud_layout` field).
9. `docs/superpowers/plans/2026-03-19-easter-eggs.md` and `docs/superpowers/specs/2026-03-19-easter-eggs-design.md` — sole written source of truth for the sentience JS.

If in doubt: `grep -rn 'sentience\|konami\|power-user\|rickroll\|charles\|toast-info\|B2 — not yet built'` before deleting anything that looks unused.

## The rule, in one line

**Keep the weird stuff. It's there on purpose.**
