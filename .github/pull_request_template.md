## Summary

<!-- One paragraph: what changes, why. -->

## Test plan

- [ ] `python -m pytest tests/ -v` green locally
- [ ] `flake8 hydra_detect/ tests/` clean
- [ ] Manual verification (describe):

## Adversarial review

<!--
Required only if this PR touches a safety-critical / physics / control path:
  hydra_detect/pipeline.py  autonomous.py  approach.py  guidance.py
  mavlink_io.py  rf/hunt.py  rf/navigator.py  rf/signal.py
  servo_tracker.py  dogleg_rtl.py  geo_tracking.py

The `adversarial-required` CI job will flag missing reports on those paths.
Run `/adversarial` locally, then either:
  - commit the report to `docs/adversarial/<pr-number>.md`, or
  - paste the report path / link below.

See `docs/adversarial/README.md` for the discipline.
-->

- [ ] No safety-critical paths touched — not required
- [ ] Adversarial report: `docs/adversarial/___.md` or `/tmp/reports/adversarial_*.md`
- [ ] R3 findings triaged (blocker / gate / follow-up / accepted)

## Risk

<!-- One line: what could go wrong, what mitigates it. -->
