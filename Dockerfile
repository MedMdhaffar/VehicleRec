# CUDA 12.8 + cuDNN runtime — matches torch/torchvision cu128 + tensorrt-cu12 10.x.
FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HOME=/app \
    HF_HOME=/app/.cache/huggingface \
    PADDLE_PDX_CACHE_HOME=/app/.paddlex

# Python 3.11 (match the dev venv) + OpenCV runtime libs + curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
      software-properties-common curl ca-certificates && \
    add-apt-repository ppa:deadsnakes/ppa && apt-get update && \
    apt-get install -y --no-install-recommends \
      python3.11 python3.11-dev python3.11-distutils \
      libgl1 libglib2.0-0 libgomp1 && \
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 && \
    ln -sf /usr/bin/python3.11 /usr/local/bin/python && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first (cached layer). torch/torchvision/tensorrt pull from the cu128 index.
# NOTE: tensorrt-cu12 (~4 GB of libs) is what lets the .engine build at startup.
COPY requirements.txt .
RUN python -m pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    --extra-index-url https://pypi.nvidia.com \
    -r requirements.txt

# Bake PaddleOCR PP-OCRv6 model weights into the image.
# The transformers engine path fails (AutoModelForTextRecognition missing in transformers 4.57);
# the runtime falls back to PaddleOCR pipeline — prefetch that path instead.
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(text_recognition_model_name='PP-OCRv6_medium_rec', use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, enable_mkldnn=False)" \
    || echo "WARN: PaddleOCR prefetch failed at build; downloads on first run"

# App code + baked .pt weights (.dockerignore strips training/datasets/frontend).
# Weights: common/models/vehicle_yolo26.pt,
#          plate_recognition/models/{best_matricule,tounes_word_detector}.pt
# The vehicle .engine is NOT baked — it builds at startup onto the target GPU.
COPY common/ common/
COPY plate_recognition/ plate_recognition/
COPY live_feed/ live_feed/

# Internal port (compose maps host:container, or host networking uses it directly).
ENV LF_PORT=8000
EXPOSE 8000
# Long start-period: first boot may spend minutes building the TRT engine.
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
  CMD curl -fsS http://localhost:${LF_PORT}/health || exit 1

CMD ["sh", "-c", "python -m uvicorn live_feed.app:app --host 0.0.0.0 --port ${LF_PORT}"]
