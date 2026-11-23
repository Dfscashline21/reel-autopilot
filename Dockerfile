FROM python:3.12-slim

# ffmpeg renders the reels; DejaVu draws the hook text, Noto Color Emoji the emoji in two-box hooks,
# and libfribidi lets Pillow shape emoji sequences (skin tones, ZWJ) correctly.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-dejavu-extra \
    fonts-noto-color-emoji libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
