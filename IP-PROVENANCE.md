# IP Provenance

This document records the ownership and development provenance of Hydra.

## Ownership

Hydra is a personal project of Kyle Adomavicius ([rmeadomavic](https://github.com/rmeadomavic), kyle.adomavicius@gmail.com). Copyright 2026 Kyle Adomavicius. Released under the Apache License 2.0 (see [LICENSE](LICENSE)).

## Development context

- Developed outside employment scope, on personal time.
- Developed on personally owned hardware: NVIDIA Jetson Orin Nano, Pixhawk 6C, Alfa wireless adapter, NESDR software-defined radio, RFD 900x telemetry radio.
- The public commit history of this repository is the contemporaneous record of the work.

## Dependencies

Hydra is built on open-source libraries and public protocol specifications (MAVLink, MSP DisplayPort, Cursor-on-Target). License identifiers below are as published by each project on PyPI as of 2026-07-19.

Runtime, per [requirements.txt](requirements.txt):

| Package | License |
|---|---|
| opencv-python-headless | Apache-2.0 |
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| ultralytics | AGPL-3.0 |
| supervision | MIT |
| pymavlink | LGPL-3.0 |
| pyserial | BSD-3-Clause |
| mgrs | MIT |
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| jinja2 | BSD-3-Clause |
| requests | Apache-2.0 |
| ntplib | MIT |
| pytak | Apache-2.0 |

Optional, per [requirements-extra.txt](requirements-extra.txt):

| Package | License |
|---|---|
| boxmot | AGPL-3.0 |

Development-only tooling, per [requirements-dev.txt](requirements-dev.txt): pytest, pytest-cov, hypothesis, flake8, httpx, PyYAML. Used for test and lint; not part of the deployed software.

Container builds use the public `dustynv/l4t-pytorch:r36.4.0` base image, which bundles NVIDIA JetPack / L4T components under their own licenses. Optional external services (Kismet, MediaMTX, GStreamer) are separately installed and separately licensed.

## Model weights

Model weights are not distributed in this repository. [`models/manifest.json`](models/manifest.json) records filenames, class lists, and SHA-256 checksums only.
