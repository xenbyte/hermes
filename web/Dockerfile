FROM python:3.13-slim-bookworm

LABEL maintainer="maintainers@example.com" \
      description="Hermes housing notification web portal"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd -g 1000 hermes && \
    useradd -u 1000 -g hermes -s /bin/bash -m hermes

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

COPY --chown=hermes:hermes requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=hermes:hermes hermes_web/ hermes_web/
COPY --chown=hermes:hermes templates/ templates/
COPY --chown=hermes:hermes static/ static/

USER hermes
EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/health').read()" || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "2", "--threads", "4", \
     "--worker-class", "gthread", "--timeout", "60", \
     "--access-logfile", "-", "--error-logfile", "-", "--log-level", "info", "hermes_web.app:app"]
