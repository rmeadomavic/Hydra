# Hydra Detect v2.0 — Dockerfile for Jetson Orin Nano
# Base: NanoOWL container with TensorRT-optimised OWL-ViT
FROM nanoowl:r36.4.3

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application
COPY hydra_detect/ ./hydra_detect/
COPY config.ini .

EXPOSE 8080

CMD ["python3", "-m", "hydra_detect", "--config", "config.ini"]
