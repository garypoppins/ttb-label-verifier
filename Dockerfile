# CPU-only image. The OCR models ship inside the rapidocr-onnxruntime wheel,
# so they are baked in at build time and nothing is downloaded at run time.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OCR_THREADS=2

# Shared libraries that OpenCV needs on a slim base image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

# Fail the build, not the first request, if the models cannot load.
RUN python -c "from app.ocr import get_engine; get_engine().warm_up()"

RUN useradd --system --no-create-home labelcheck
USER labelcheck

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
