FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAPERLIB_DATA=/data \
    TZ=Asia/Taipei

# tzdata：排程用台灣時間；Tesseract：OCR 掃描檔（英文＋繁體中文）
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-tra \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY seed ./seed

EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/site')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
