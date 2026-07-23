FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    PATH="/root/.deno/bin:/usr/local/bin:${PATH}"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg curl ca-certificates git unzip && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fsSL https://deno.land/install.sh | sh && \
    ln -sf /root/.deno/bin/deno /usr/local/bin/deno && \
    deno --version && ffmpeg -version | head -1

WORKDIR /app

COPY requirements.txt .
RUN pip install -U pip setuptools wheel && \
    pip install -r requirements.txt

COPY . .

RUN chmod +x start.sh start 2>/dev/null || true && \
    mkdir -p downloads cache IotaXMedia/assets

EXPOSE 8080

# Render uses healthCheckPath; this is extra for Docker runtimes
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["bash", "start.sh"]
