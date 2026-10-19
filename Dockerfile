FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg does the segment cutting. ca-certificates/curl are needed to fetch
# Deno, which yt-dlp uses to solve YouTube's JavaScript challenges.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime to solve YouTube's challenge before it can
# hand back playable media URLs. Without it, metadata extraction can succeed
# while the actual download fails.
ENV DENO_INSTALL=/usr/local
RUN curl -fsSL https://deno.land/install.sh | sh \
    && deno --version \
    && ln -sf /usr/local/bin/deno /usr/bin/deno

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Writable location for temporary job output. Render's filesystem is ephemeral,
# so this only needs to survive for the life of a request.
RUN mkdir -p /app/downloads
ENV STREAMCUT_OUTPUT_DIR=/app/downloads

# Render injects PORT; default to 10000 when running the image directly.
EXPOSE 10000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
