# Dev loop

Fast iteration on the dashboard UI without a 5-10 min Docker rebuild.

`compose.dev.yml` spins up a second container that bind-mounts
`hydra_detect/` into the image, binds port **8081**, and runs
`uvicorn --reload`. Prod stays on :8080 untouched.

## When to use it

| Change | Use dev (:8081) | Use prod (:8080) |
|---|:-:|:-:|
| Template / Jinja edits (`hydra_detect/web/templates/*.html`) | ✓ | |
| CSS / JS under `hydra_detect/web/static/` | ✓ | |
| FastAPI route or schema tweaks in `web/server.py` | ✓ | |
| Pipeline, detector, tracker, MAVLink, RF | | ✓ |
| End-to-end detection smoke test | | ✓ |
| Anything touching the camera or GPU | | ✓ |

Dev mode boots only the FastAPI shell — no `Pipeline.start()`, no camera,
no YOLO model load, no MAVLink connection. Endpoints that surface
pipeline state (`/api/stats`, `/api/tracks`, `/stream.jpg`) will return
empty or placeholder shapes. That's fine for laying out HUD widgets,
styling panels, and wiring up new controls. For anything that needs real
detection data, hit :8080.

## Workflow

```bash
# First-time setup — one Docker build
make build

# Start the dev container
make dev

# In another shell: edit templates / JS / CSS / server.py
# Reload http://<jetson-ip>:8081/ — see the change
# .py edits auto-restart uvicorn; template + static edits take effect
# on the next request with no restart needed.

# When done
make dev-down
```

## Caveats

1. **Same image as prod.** `compose.dev.yml` uses `hydra-detect:latest`
   — it does not build. If you change `requirements.txt`, the Dockerfile,
   or anything baked in at image-build time (GStreamer bindings, apt
   packages), run `make build` first.

2. **config.ini is read-only in dev.** The prod container already writes
   to `config.ini` via `/api/config/*` endpoints. To avoid fighting over
   the file, dev mounts it `:ro`. Config writes from the :8081 dashboard
   will 500 — that's deliberate. Edit the file on the host if you need
   to change a knob for dev.

3. **Shared models/ and output_data/.** Both containers read from the
   same host paths. Logs land in `output_data/logs/hydra.log` from
   whichever container wrote them. That's usually what you want; if a
   dev run pollutes the log, rotate or truncate it.

4. **No GPU, no privileged, no nvidia runtime.** Dev mode runs on plain
   bridge networking with no device access. Zero chance of stomping on
   the prod container's camera or serial port.

5. **Port collision.** If something else is on :8081, edit the `ports:`
   line in `compose.dev.yml` and the `--port` flag in the `command:`
   block — keep them aligned.

6. **uvicorn reload scope.** `--reload-dir /app/hydra_detect` watches
   only that tree. Editing files outside (e.g. `config.ini`) does not
   trigger a reload — they're picked up on the next request anyway.

## Swapping back to prod

```bash
make dev-down               # stop the dev container
sudo systemctl status hydra-detect   # prod keeps running
# or, if you had stopped prod:
make up                     # restart the systemd service
```

Because the two stacks are isolated (different ports, different
container names, different networks), you can leave prod running while
you iterate in dev all day.
