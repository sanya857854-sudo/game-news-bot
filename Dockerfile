FROM python:3.12-slim

# Установка ffmpeg для обработки видео/трейлеров
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Создаем пользователя с UID 1000 для совместимости с Hugging Face Spaces
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR $HOME/app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user . .

RUN mkdir -p $HOME/app/data $HOME/app/logs

# Hugging Face Spaces слушает порт 7860
EXPOSE 7860

CMD ["python", "main.py"]
