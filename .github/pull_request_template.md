## Summary

<!-- One paragraph: what changes, why. -->

## Test plan

- [ ] `python -m pytest tests/ -v` green locally
- [ ] `flake8 hydra_detect/ tests/` clean
- [ ] Manual verification (describe):

## Adversarial review

<!--
The `adversarial-required` CI job detects when a PR touches safety-critical
paths and flags missing reports. It is authoritative — do not self-attest
"not required" here; CI decides.

When flagged, run `/adversarial` locally and commit the report to
`docs/adversarial/<pr-number>.md`. CI requires the file to be > 500 bytes
and contain a `## Round 3` heading. See `docs/adversarial/README.md`.
-->

- [ ] R3 findings triaged (blocker / gate / follow-up / accepted)

## Risk

<!-- One line: what could go wrong, what mitigates it. -->
