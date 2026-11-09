FROM python:3.13.18-slim-bookworm

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ffmpeg openssl ca-certificates tzdata libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-app.txt ./
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements-app.txt \
    && python -c "import cv2, numpy, PIL, paho.mqtt.client"
COPY *.py ./
COPY config.json ./
COPY ha_service/ ./ha_service/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENCV_LOG_LEVEL=OFF
LABEL io.hass.name="Munai" io.hass.description="MQTT nest occupant recognizer" \
    io.hass.type="addon" io.hass.version="0.1.45" io.hass.arch="aarch64|amd64"
CMD ["python", "-m", "ha_service"]
