---
name: hydra-ui
description: Design-system drift guard for the Hydra Detect web UI. MUST be read before touching anything in hydra_detect/web/ — templates, CSS, or UI-facing JS. Trigger on web UI work, dashboard styling, CSS changes, new panels/views/components, theme work, or any PR that renders pixels. Encodes the token law, radius law, motion ceiling, typography roles, and theme integrity rules that keep the UI tactical instead of drifting toward generic web defaults.
---

# Hydra UI — Design System Guard

Hydra Detect's dashboard is a **tactical operator console** (reference class:
Anduril Lattice, mission systems), not a SaaS product. AI agents trained on
millions of average websites will drift it toward the mean — rounded cards,
blue accents, Inter, generous whitespace, entry animations. Every rule below
exists to stop that. When your instinct and this file disagree, this file wins.

## The Laws

### 1. Token law
`hydra_detect/web/static/css/variables.css` is the single authority for
color, spacing, type scale, radius, and transitions. **Never hardcode a hex,
px-spacing, or easing that a token already covers.** If a value you need is
missing, add a token, don't inline it.

### 2. Theme integrity
Three themes: `ops` (dark olive), `nvg` (monochrome green phosphor),
`lattice` (light-dark). They work by token override on `:root[data-theme]`.
Any NEW color must either derive from existing tokens or be defined in **all
three** theme blocks. A color that only exists in ops-theme is a bug — check
it under NVG, where everything must collapse to green-on-black.

### 3. Radius law
`--radius` is **2px, everywhere, flat**. Near-square, military. No
`rounded-full`, no pills, no 8/12/16 radius language. If a component looks
like it belongs on a marketing site, the radius is usually why.

### 4. Typography roles
- **Barlow Condensed** — headings, labels, buttons: uppercase, `--ls-condensed` tracking
- **Barlow** — body prose only
- **JetBrains Mono** — every data value, coordinate, ID, readout — with
  `font-variant-numeric: tabular-nums` (gold.css applies it; keep new data
  classes on that list)

Never introduce another font. Never render telemetry in the body font.

### 5. Motion ceiling
This is a live operator console. An operator watching a strike-gate feed
does not want theater between them and the data.

- **340ms absolute ceiling** on any transition/animation
- Easings come from tokens (`--transition-fast/normal/slow`) — never CSS
  keyword easings (`ease`, `ease-in-out`, `linear` for UI motion)
- **No entry animations on data** — telemetry, tracks, detections, and log
  lines appear instantly. Panel-level fades (240ms, gold.css) are the max.
- **No entrance animations on polled lists.** panels.js and ops.js rebuild
  DOM nodes on poll ticks; per-row animations replay on every rebuild and
  read as flicker. Verified constraint, do not retry it.
- Infinite/looping animation is reserved for *live-state* indicators
  (healthy dots, scanline) at low amplitude. Warning/danger states stay
  steady — blinking reads as a new event.
- Every animation this repo ships respects `prefers-reduced-motion`.

### 6. Layer law
- Structural/layout CSS → the per-view stylesheet (`ops.css`, `topbar.css`, …)
- Aesthetic polish (depth, motion, glow, numeric discipline) → **`gold.css`**,
  which loads LAST and must stay removable as a single unit
- Load order in `base.html` is part of the design. `gold.css` stays last
  among first-party sheets.

### 7. Sacred vs slop
JS state, polling, stream control, API calls, and event handlers are
**sacred** — visual work never touches them. Class *values* in templates are
fair game **only after** grepping `static/js/` for selector use
(`querySelector`, `classList`, `getElementById` — main.js and panels.js
query many of them). `id` attributes are load-bearing; never rename.

## Pre-PR checklist (PASS required on all)

| # | Check |
|---|---|
| 1 | No hardcoded colors/spacing/easing a token covers |
| 2 | Verified under all three themes, including NVG collapse |
| 3 | All radii 2px; no new radius language |
| 4 | Data values in mono + tabular-nums; labels in Condensed caps |
| 5 | No animation over 340ms; no keyword easings; no entry theater on data |
| 6 | `prefers-reduced-motion` covered for anything that moves |
| 7 | No JS logic touched by visual changes; selector grep done for renamed classes |
| 8 | Polish landed in gold.css, not scattered across structural sheets |

## Verification without the backend

The dashboard renders without FastAPI for CSS QA — flatten the Jinja
templates and static-serve:

```bash
python - <<'EOF'
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('hydra_detect/web/templates'))
html = env.get_template('base.html').render(
    morale_features_enabled=False, remote_abort_reachable=False)
open('/tmp/hydra-flat/index.html', 'w', encoding='utf-8').write(
    html.replace('/static/', 'static/'))
EOF
# copy hydra_detect/web/static → /tmp/hydra-flat/static, then:
python -m http.server 8778 --bind 127.0.0.1 -d /tmp/hydra-flat
```

JS will error against missing `/api/*` — irrelevant for visual QA.
Screenshot all three themes (`document.documentElement.dataset.theme`).
