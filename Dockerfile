# Hydra Detect v2.0 — Dockerfile for Jetson Orin Nano
# Base: l4t-pytorch with CUDA-enabled OpenCV, PyTorch, and TensorRT
FROM dustynv/l4t-pytorch:r36.4.0

# OTA version stamp (issue #152, PR-A). CI sets this to the git SHA at
# build time (`docker build --build-arg HYDRA_VERSION=$GITHUB_SHA ...`),
# which then surfaces as ``body["version"]`` on ``GET /api/health``.
# Default "dev" so local builds still work without --build-arg.
ARG HYDRA_VERSION=dev
ENV HYDRA_VERSION=${HYDRA_VERSION}

ENV PYTHONUNBUFFERED=1
# Override the base image's PIP_INDEX_URL which points to an unreachable
# Jetson AI Lab index (pypi.jetson-ai-lab.dev) that fails DNS during build.
ENV PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_EXTRA_INDEX_URL=https://pypi.ngc.nvidia.com
WORKDIR /app

# Install Python dependencies
# The base l4t-pytorch image already provides opencv-contrib-python (CUDA),
# numpy 1.x, and torch. We must NOT let pip install opencv-python or
# opencv-python-headless from PyPI — they overwrite the base image's
# CUDA-enabled OpenCV and break cv2 imports.
#
# Strategy: install ultralytics and supervision with --no-deps (they both
# pull opencv-python transitively), then install everything else normally
# with numpy pinned to <2.
COPY requirements.txt requirements-extra.txt ./
RUN pip3 install --no-cache-dir --no-deps ultralytics supervision && \
    grep -v "opencv-python\|ultralytics\|supervision" requirements.txt | \
    sed 's/numpy>=1.24,<3.0/numpy>=1.24,<2.0/' > /tmp/reqs-filtered.txt && \
    pip3 install --no-cache-dir -r /tmp/reqs-filtered.txt && \
    pip3 install --no-cache-dir scipy polars ultralytics-thop defusedxml pyDeprecate matplotlib tqdm && \
    rm /tmp/reqs-filtered.txt

# Optional dependencies — installed unconditionally because the runtime
# imports them lazily (boxmot is only imported inside ReIDTracker.init(),
# guarded by [tracker] reid_enabled). Cost on units that leave the flag
# off is disk space only; runtime cost is zero. Closes adversarial finding
# R3-1 from PR #184: requirements-extra.txt was previously installed by
# no deploy or CI path, making the reid_enabled=true feature flag
# permanently unreachable in any deployed image.
RUN pip3 install --no-cache-dir -r requirements-extra.txt

# GStreamer RTSP server + RTL-SDR debug tools. Kismet runs on the host;
# rtl_test / rtl_fm / rtl_power let you debug the dongle from inside the
# container via `docker exec` without a host-side install.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gstreamer1.0-rtsp \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gir1.2-gst-rtsp-server-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    gir1.2-gstreamer-1.0 \
    python3-gi \
    librtlsdr0 \
    rtl-sdr \
    && rm -rf /var/lib/apt/lists/*

# torch on the l4t-pytorch base is built against numpy 1.x. The unpinned
# scipy/matplotlib install above (and requirements-extra.txt) pull numpy 2.x
# in transitively, which clobbers the numpy<2 pin from the filtered
# requirements step and breaks torch.Tensor.numpy() at runtime
# ("RuntimeError: Numpy is not available"). Pin numpy<2 last so it wins.
# scipy 1.15 is compiled against the numpy 2.0 ABI but stays runtime-
# compatible with numpy 1.x, so the downgrade does not break it.
RUN pip3 install --no-cache-dir "numpy<2"

# Copy application
COPY hydra_detect/ ./hydra_detect/
COPY config.ini .

EXPOSE 8080
EXPOSE 8554

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

CMD ["python3", "-m", "hydra_detect", "--config", "config.ini"]
