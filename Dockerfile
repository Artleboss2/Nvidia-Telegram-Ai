FROM python:3.9-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.9-slim

WORKDIR /app

COPY --from=builder /install /usr/local

COPY bot.py .

RUN mkdir -p /app/data

RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

ENV PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/memory.db \
    NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1 \
    MODEL_ID=meta/llama-3.1-405b-instruct \
    MEMORY_THRESHOLD=10

CMD ["python", "-u", "bot.py"]
