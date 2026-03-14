# Hydra Detect v2.0 — Dockerfile for Jetson Orin Nano
# Base: NanoOWL container with TensorRT-optimised OWL-ViT
FROM nanoowl:r36.4.3

ENV PYTHONUNBUFFERED=1
# Override the base image's PIP_INDEX_URL which points to an unreachable
# Jetson AI Lab index (pypi.jetson-ai-lab.dev) that fails DNS during build.
ENV PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_EXTRA_INDEX_URL=https://pypi.ngc.nvidia.com
WORKDIR /app

# Install Python dependencies
# The base NanoOWL image already provides opencv-contrib-python (4.11),
# numpy 1.26, torch 2.5, and jinja2. We must NOT let pip install
# opencv-python or opencv-python-headless from PyPI — they overwrite the
# base image's CUDA-enabled OpenCV and break cv2 imports.
#
# Strategy: install ultralytics and supervision with --no-deps (they both
# pull opencv-python transitively), then install everything else normally
# with numpy pinned to <2.
COPY requirements.txt .
RUN pip3 install --no-cache-dir --no-deps ultralytics supervision && \
    grep -v "opencv-python\|ultralytics\|supervision" requirements.txt | \
    sed 's/numpy>=1.24,<3.0/numpy>=1.24,<2.0/' > /tmp/reqs-filtered.txt && \
    pip3 install --no-cache-dir -r /tmp/reqs-filtered.txt && \
    pip3 install --no-cache-dir scipy polars ultralytics-thop defusedxml pyDeprecate && \
    rm /tmp/reqs-filtered.txt

# Copy application
COPY hydra_detect/ ./hydra_detect/
COPY config.ini .

EXPOSE 8080

CMD ["python3", "-m", "hydra_detect", "--config", "config.ini"]
