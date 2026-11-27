ARG NOTEDOWN_RUNTIME_IMAGE=runtime

FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=5500 NOTE_SYNC_STORAGE=/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --shell /bin/false appuser && mkdir -p /data && chown appuser:appuser /data

FROM ${NOTEDOWN_RUNTIME_IMAGE}
ARG VERSION=2.0.2-dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Notedown FastAPI" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/ImuruKevol/notedown-server"
WORKDIR /app
COPY --chown=appuser:appuser *.py ./
COPY --chown=appuser:appuser templates ./templates
COPY --chown=appuser:appuser static ./static
COPY --from=frontend --chown=appuser:appuser /build/dist ./frontend/dist
USER appuser
VOLUME ["/data"]
EXPOSE 5500
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5500/api/health', timeout=4)"
CMD ["python", "-m", "uvicorn", "app:create_app", "--factory", "--host", "0.0.0.0", "--port", "5500", "--workers", "1"]
