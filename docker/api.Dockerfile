FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
# Some corporate/local proxies reject plain-HTTP Debian mirrors with 405.
# Force the official Debian sources to HTTPS before installing the optional
# legacy Word (.doc) extractor so the shared API/worker image remains buildable.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends antiword \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml /app/
RUN pip install --no-cache-dir .
COPY VERSION /app/VERSION
COPY app /app/app
COPY web /app/web

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
