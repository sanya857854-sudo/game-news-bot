#!/usr/bin/env python3
"""Точка входа игрового новостного бота для Telegram-канала @gameworld2029.

Обычный запуск (работает постоянно):   python main.py
Проверка настроек:                      python main.py --check
Статистика подписчиков:                 python main.py --stats
Опубликовать один пост прямо сейчас:    python main.py --once
Показать пост, не отправляя в канал:    python main.py --once --dry-run
Отправить тестовое сообщение в канал:   python main.py --ping
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from gamebot.config import ConfigError, load_config


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Game News Bot</title></head>"
            "<body style='font-family:sans-serif;text-align:center;padding:60px;background:#0f172a;color:#f8fafc;'>"
            "<h1 style='color:#38bdf8;'>🎮 Game News Telegram Bot</h1>"
            "<p style='font-size:18px;color:#4ade80;'>🟢 Бот работает круглосуточно (24/7)</p>"
            "<p style='color:#94a3b8;'>Канал: <a style='color:#38bdf8;' href='https://t.me/gameworld2029'>@gameworld2029</a></p>"
            "</body></html>"
        )
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def _start_health_server() -> None:
    port = int(os.getenv("PORT", "7860"))
    try:
        server = HTTPServer(("0.0.0.0", port), _HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logging.getLogger().info("Веб-сервер статуса (Health Check) запущен на порту %d", port)
    except Exception as exc:
        logging.getLogger().debug("Фоновый веб-сервер не запущен: %s", exc)


def setup_logging(level: str, log_dir) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    file_handler = RotatingFileHandler(log_dir / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> int:
    parser = argparse.ArgumentParser(description="Бот игровых новостей для Telegram-канала")
    parser.add_argument("--check", action="store_true", help="проверить настройки и источники")
    parser.add_argument("--stats", action="store_true", help="показать статистику подписчиков")
    parser.add_argument("--once", action="store_true", help="опубликовать один пост и выйти")
    parser.add_argument("--dry-run", action="store_true", help="вместе с --once: только показать пост")
    parser.add_argument("--ping", action="store_true", help="отправить в канал тестовое сообщение")
    args = parser.parse_args()

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"\nОшибка настроек: {exc}\n")
        return 2

    setup_logging(cfg.log_level, cfg.log_dir)

    if args.check:
        from gamebot.check import run_check
        return run_check(cfg)

    if args.stats:
        from gamebot.growth import show_stats
        return show_stats(cfg)

    from gamebot.engine import Bot
    bot = Bot(cfg, dry_run=args.dry_run)

    if args.ping:
        bot.tg.send_message("✅ Бот подключён к каналу и готов к работе.")
        print("Тестовое сообщение отправлено — загляните в канал.")
        return 0

    if args.once:
        return 0 if bot.post_one() else 1

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: bot.request_stop())
    _start_health_server()
    bot.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
