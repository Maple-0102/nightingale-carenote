FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    CARENOTE_DB=/app/data/carenote.db \
    DEMO_MODE=1

WORKDIR /app
COPY . /app

RUN addgroup -S carenote \
    && adduser -S -G carenote carenote \
    && mkdir -p /app/data \
    && chown -R carenote:carenote /app

USER carenote
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

CMD ["python", "server.py"]
