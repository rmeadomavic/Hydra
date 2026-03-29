# Hydra Detect v2.0 — Dockerfile for Jetson Orin Nano
# Base: l4t-pytorch with CUDA-enabled OpenCV, PyTorch, and TensorRT
FROM dustynv/l4t-pytorch:r36.4.0

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
COPY requirements.txt .
RUN pip3 install --no-cache-dir --no-deps ultralytics supervision && \
    grep -v "opencv-python\|ultralytics\|supervision" requirements.txt | \
    sed 's/numpy>=1.24,<3.0/numpy>=1.24,<2.0/' > /tmp/reqs-filtered.txt && \
    pip3 install --no-cache-dir -r /tmp/reqs-filtered.txt && \
    pip3 install --no-cache-dir scipy polars ultralytics-thop defusedxml pyDeprecate matplotlib tqdm && \
    rm /tmp/reqs-filtered.txt

# GStreamer RTSP server for annotated video output
RUN apt-get update && apt-get install -y --no-install-recommends \
    gstreamer1.0-rtsp \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gir1.2-gst-rtsp-server-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    gir1.2-gstreamer-1.0 \
    python3-gi \
    && rm -rf /var/lib/apt/lists/*

# Copy application
COPY hydra_detect/ ./hydra_detect/
COPY config.ini .

EXPOSE 8080
EXPOSE 8554

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1

CMD ["python3", "-m", "hydra_detect", "--config", "config.ini"]
