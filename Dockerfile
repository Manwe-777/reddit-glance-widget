# Stdlib-only Python app -> tiny, dependency-free image. Nothing to pip install.
FROM python:3.12-slim

# Don't buffer stdout so `docker logs` shows scheduler/http lines immediately.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app
COPY app/ /app/

# Cache lives here; mount a volume to persist across restarts.
RUN mkdir -p /data && chown -R appuser:appuser /data /app
VOLUME ["/data"]

USER appuser
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4).status==200 else 1)"

CMD ["python", "server.py"]
