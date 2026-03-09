# Hydra Detect v2.0 — Multi-stage Dockerfile
# For Jetson: use nvcr.io/nvidia/l4t-pytorch as base instead

FROM python:3.11-slim AS base

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Default: run with config.ini
CMD ["python", "-m", "hydra_detect", "--config", "config.ini"]
