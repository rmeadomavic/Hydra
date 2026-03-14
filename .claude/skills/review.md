# /review — Robotics Codebase Review

Review the Hydra Detect codebase for correctness, safety, and performance
following the **discover → review → fix** process.

## Instructions

When invoked, perform a structured code review using parallel sub-agents:

### Phase 1: Discover (parallel)
Spawn agents to review these areas simultaneously:

1. **Detection & Tracking** — Review `hydra_detect/detectors/`, `tracker.py`
   - Model loading safety (ImportError handling, memory)
   - Detection result correctness (bbox normalization, label mapping)
   - Tracker ID consistency across frames

2. **Pipeline & Threading** — Review `pipeline.py`, `camera.py`
   - Thread safety: all shared state protected by locks?
   - No blocking I/O in the detection hot loop?
   - Clean shutdown on SIGTERM/SIGINT?
   - Frame rate sustainability (≥5 FPS on Jetson)

3. **MAVLink & Safety** — Review `mavlink_io.py`
   - Fail-safe behavior: what happens on GPS loss, connection drop?
   - Vehicle command validation (mode changes, strike authorization)
   - Alert throttling working correctly?
   - Serial/UDP connection resilience

4. **Web API & Security** — Review `web/server.py`, `web/templates/index.html`
   - Auth enforcement on all control endpoints
   - Input validation (prompt length, threshold bounds)
   - XSS prevention in template rendering
   - CORS and origin checking

### Phase 2: Review
For each area, check against these criteria:

- **Memory:** Bounded allocations? No leaks in long-running loops?
- **Thread safety:** Locks held briefly? No deadlock potential?
- **Fail-safe:** Vehicle defaults to safe state on any error?
- **Jetson compatibility:** ARM64, 4-8GB RAM, TensorRT engines?
- **Input validation:** All external input sanitized?

### Phase 3: Report
Present findings organized by severity:

1. **Critical** — Safety issues, crash bugs, security vulnerabilities
2. **Warning** — Performance problems, thread safety concerns, resource leaks
3. **Info** — Style issues, minor improvements, documentation gaps

For each finding include:
- File and line number
- Description of the issue
- Suggested fix (code snippet if applicable)

### Phase 4: Fix (if requested)
If the user says `/review --fix` or asks to fix issues:
- Apply fixes for Critical and Warning items
- Run `python -m pytest tests/ -v` after each fix
- Run `flake8 hydra_detect/ tests/` to check style
- Commit each fix separately with a descriptive message
