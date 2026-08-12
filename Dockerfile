FROM python:3.11-slim

WORKDIR /app

RUN adduser --disabled-password --gecos '' appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data && chown appuser:appuser data

USER appuser

EXPOSE 5055

CMD ["gunicorn", "--worker-class", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "--workers", "1", \
     "--timeout", "300", \
     "--worker-connections", "2000", \
     "--bind", "0.0.0.0:5055", "wsgi:app"]
