FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Berlin

RUN apt-get update \
 && apt-get install -y --no-install-recommends iputils-ping tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Chart.js lokal einbinden, damit das Dashboard auch ohne Internet funktioniert
ADD https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js ./app/static/vendor/chart.umd.min.js

VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
