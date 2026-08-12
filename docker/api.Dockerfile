FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends antiword \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml /app/
RUN pip install --no-cache-dir .
COPY VERSION /app/VERSION
COPY app /app/app
COPY web /app/web

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
