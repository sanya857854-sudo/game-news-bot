"""
Web UI & 24/7 Daemon Runner for Game News Bot on Hugging Face Spaces (Gradio SDK).
100% Free, No credit card required.
"""
import os
import sys
import threading
import time
import gradio as gr

# Добавляем путь к боту
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from gamebot.config import ConfigError, load_config
from gamebot.engine import Bot

# Глобальные переменные
bot_instance = None
status_log = []


def run_bot_in_background():
    global bot_instance
    try:
        cfg = load_config()
        bot_instance = Bot(cfg)
        status_log.append(f"[{time.strftime('%X')}] Бот успешно инициализирован и запущен в фоне.")
        bot_instance.run_forever()
    except Exception as e:
        status_log.append(f"[{time.strftime('%X')}] Ошибка запуска бота: {e}")


# Запускаем фоновый поток при старте
bot_thread = threading.Thread(target=run_bot_in_background, daemon=True)
bot_thread.start()


def get_status():
    if bot_instance:
        return "🟢 Бот активен и публикует посты по расписанию в канал @gameworld2029"
    return "⏳ Инициализация бота..."


def post_now_action():
    if not bot_instance:
        return "⚠️ Бот ещё запускается, подождите пару секунд..."
    try:
        ok = bot_instance.post_one()
        if ok:
            return f"✅ [{time.strftime('%X')}] Пост успешно отправлен в канал!"
        else:
            return f"ℹ️ [{time.strftime('%X')}] Новых подходящих материалов пока нет."
    except Exception as e:
        return f"❌ Ошибка публикации: {e}"


def refresh_logs():
    return "\n".join(status_log[-10:]) if status_log else "Бот работает в штатном режиме"


with gr.Blocks(title="Game News Bot Control Panel", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🎮 Панель управления Game News Bot")
    gr.Markdown(
        "Круглосуточный автономный бот игровых новостей для канала **[@gameworld2029](https://t.me/gameworld2029)**."
    )

    with gr.Row():
        status_box = gr.Textbox(label="Текущий статус", value=get_status, every=10)

    with gr.Row():
        post_btn = gr.Button("🚀 Опубликовать 1 пост прямо сейчас", variant="primary")
        result_box = gr.Textbox(label="Результат ручной публикации", interactive=False)
        post_btn.click(fn=post_now_action, outputs=result_box)

    with gr.Row():
        log_box = gr.Textbox(label="События бота", value=refresh_logs, every=15, lines=4)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
