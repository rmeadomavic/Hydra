# Hydra Detect — developer convenience targets
# Run `make help` for the full list.

PY         ?= python3
PYTEST     ?= $(PY) -m pytest
FLAKE8     ?= $(PY) -m flake8
MYPY       ?= $(PY) -m mypy
DOCKER     ?= sudo docker
IMAGE      ?= hydra-detect:latest
SERVICE    ?= hydra-detect
CONTAINER  ?= hydra-detect
SMOKE_HOST ?= http://127.0.0.1:8080

# Quick subset: skip the slow integration wiring + explicit "integration" names.
FAST_IGNORES := --ignore=tests/test_integration_wiring.py
FAST_KEXPR   := -k "not integration"

# Endpoints probed by `make smoke`. Format: <path>:<json-key-to-grep>.
# Only GET routes that are auth-free (same-origin bypass list) so curl works
# without a bearer token. Extend as new public reads land.
SMOKE_PROBES := \
  /api/health:healthy \
  /api/stats:fps \
  /api/tracks:tracks \
  /api/servo/status:enabled \
  /api/autonomy/status:mode \
  /api/tak/peers:peers \
  /api/tak/type_counts:window_seconds \
  /api/rf/status:state \
  /api/approach/status:mode \
  /api/events:events \
  /api/detections:detections \
  /api/rtsp/status:enabled \
  /api/tak/status:enabled \
  /api/config/full:camera \
  /api/stream/quality:quality

.PHONY: help test test-all lint build up logs shell clean smoke

help:
	@echo "Hydra Detect — make targets"
	@echo "  test      Fast pytest subset (skips integration-wiring)"
	@echo "  test-all  Full pytest suite (verbose)"
	@echo "  lint      flake8 on hydra_detect/ + tests/ (+ mypy if installed)"
	@echo "  build     docker build -t $(IMAGE) ."
	@echo "  up        sudo systemctl restart $(SERVICE)"
	@echo "  logs      journalctl -u $(SERVICE) -f"
	@echo "  shell     docker exec -it $(CONTAINER) bash"
	@echo "  clean     docker image prune -f (dangling images)"
	@echo "  smoke     curl every public /api GET and grep expected keys"

test:
	$(PYTEST) tests/ -q --tb=short $(FAST_IGNORES) $(FAST_KEXPR)

test-all:
	$(PYTEST) tests/ -v

lint:
	$(FLAKE8) hydra_detect/ tests/ --count --show-source --statistics
	@if command -v mypy >/dev/null 2>&1; then \
	  echo "mypy hydra_detect/"; \
	  $(MYPY) hydra_detect/ || true; \
	else \
	  echo "mypy not installed — skipping"; \
	fi

build:
	$(DOCKER) build -t $(IMAGE) .

up:
	sudo systemctl restart $(SERVICE)
	@echo "restarted $(SERVICE); allow ~35s for YOLO model load"

logs:
	sudo journalctl -u $(SERVICE) -f

shell:
	$(DOCKER) exec -it $(CONTAINER) bash

clean:
	$(DOCKER) image prune -f

smoke:
	@fail=0; total=0; pass=0; \
	for p in $(SMOKE_PROBES); do \
	  total=$$((total+1)); \
	  ep=$${p%%:*}; key=$${p##*:}; \
	  body=$$(curl -sf --max-time 3 $(SMOKE_HOST)$$ep 2>/dev/null || true); \
	  if [ -n "$$body" ] && printf "%s" "$$body" | grep -q "\"$$key\""; then \
	    printf "  OK   %-28s (%s)\n" "$$ep" "$$key"; \
	    pass=$$((pass+1)); \
	  else \
	    printf "  FAIL %-28s (missing \"%s\")\n" "$$ep" "$$key"; \
	    fail=1; \
	  fi; \
	done; \
	printf "\nsmoke: %d/%d passed against %s\n" "$$pass" "$$total" "$(SMOKE_HOST)"; \
	exit $$fail
