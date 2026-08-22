FROM python:3.11-slim

WORKDIR /app

RUN adduser --disabled-password --gecos '' --uid 1000 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data && chown appuser:appuser data
RUN chmod +x /app/docker-entrypoint.sh

# Starts as root only long enough to fix ownership of the mounted data
# directory, then execs the server as appuser. See docker-entrypoint.sh.
ENTRYPOINT ["/app/docker-entrypoint.sh"]

EXPOSE 5055

CMD ["gunicorn", "--worker-class", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "--workers", "1", \
     "--timeout", "300", \
     "--worker-connections", "2000", \
     "--bind", "0.0.0.0:5055", "wsgi:app"]
