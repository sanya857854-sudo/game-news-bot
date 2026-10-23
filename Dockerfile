FROM python:3.12-slim

# ffmpeg нужен, чтобы превращать трейлеры Steam в видео
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN mkdir -p /app/data /app/logs

# При первом запуске на сервере базы ещё нет — кладём заранее подготовленную
# историю постов, чтобы бот не опубликовал повторно то, что уже вышло.
CMD ["sh", "-c", "if [ ! -f /app/data/bot.db ]; then cp /app/seed/bot.db /app/data/bot.db 2>/dev/null || true; fi; exec python main.py"]
