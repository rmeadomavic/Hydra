# Hydra Detect — Test Conventions

## Running

```bash
make test           # fast subset (CI default — skips hardware, integration, slow)
make test-all       # full suite including hardware-marked tests
make test-cov       # fast subset with coverage report on hydra_detect/
python -m pytest tests/test_approach.py -v   # single file
```

## Markers

| Marker | Purpose | Default |
|---|---|---|
| `@pytest.mark.hardware` | Requires Jetson/Pixhawk/camera | Deselected |
| `@pytest.mark.slow` | Long-running (concurrency stress, full fuzz table) | Deselected in `make test` |
| `@pytest.mark.regression` | Pins a specific past defect | Run with the suite; searchable |

## House style (don't invent new conventions)

- **Mocking:** `unittest.mock.MagicMock` only — no `pytest-mock`.
- **No root `conftest.py`:** fixtures live in-module as `_make_*()` helpers or autouse state resets.
- **MAVLink:** see `tests/test_mavlink_commands.py::_make_mavlink_io()`.
- **ApproachController:** see `tests/test_drop_strike.py::_make_controller()`.
- **FastAPI:** `TestClient(app)` with `_reset_state` autouse — see `tests/test_web_api.py` top.
- **Real config objects:** pass actual dataclasses (`ApproachConfig`, `GuidanceConfig`); mock only hardware boundaries.

## Regression discipline

When a bug is fixed, add a test. Two options, pick whichever fits:

1. **Inline**: add a `test_regression_<short_name>` method in the relevant test class with `@pytest.mark.regression`. Include the commit SHA or issue number in the docstring.
2. **Standalone**: `tests/test_regression_<bug_slug>.py` for cross-cutting bugs that don't map cleanly to one module.

Example:

```python
@pytest.mark.regression
def test_regression_abort_restores_pre_approach_mode(self):
    """Fixed: abort used to hardcode LOITER instead of restoring captured mode.
    See commit a1b2c3d."""
    ...
```

## Coverage

CI runs `pytest --cov=hydra_detect --cov-report=xml` and uploads `coverage.xml`
as an artifact. No `--cov-fail-under` floor is enforced yet — we're in
baseline-collection mode. To inspect local coverage:

```bash
make test-cov
```

Raise failing module coverage by adding targeted tests rather than fighting the
threshold.
