"""Команда «python main.py --check»: проверяет, всё ли настроено, ничего не публикуя."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone

from . import ai, net, rss, steam
from .config import Config
from .telegram import Telegram, TelegramError

OK, WARN, FAIL = "  ✅", "  ⚠️ ", "  ❌"


def run_check(cfg: Config) -> int:
    problems = 0
    print("\n=== Проверка настроек ===\n")

    # --- Telegram ---
    print("Telegram:")
    tg = Telegram(cfg.bot_token, cfg.channel_id, cfg.api_base)
    try:
        me = tg.get_me()
        print(f"{OK} токен принят, бот: @{me.get('username')}")
        try:
            chat = tg.get_chat()
            print(f"{OK} канал найден: «{chat.get('title')}», числовой ID: {chat.get('id')}")
            member = tg.get_member(me["id"])
            status = member.get("status")
            if status in ("administrator", "creator"):
                if member.get("can_post_messages") is False:
                    print(f"{FAIL} бот — администратор, но у него нет права «Публикация сообщений»")
                    problems += 1
                else:
                    print(f"{OK} бот — администратор канала и может публиковать")
            else:
                print(f"{FAIL} бот не администратор канала (статус: {status}). Добавьте его админом.")
                problems += 1
        except TelegramError as exc:
            print(f"{FAIL} канал недоступен: {exc}. Проверьте TELEGRAM_CHANNEL_ID и добавьте бота админом.")
            problems += 1
    except TelegramError as exc:
        print(f"{FAIL} токен не работает: {exc}")
        problems += 1

    # --- Steam ---
    print("\nSteam:")
    if cfg.enable_steam:
        try:
            data = net.get(steam.FEATURED_URL, params={"cc": cfg.steam_list_cc, "l": cfg.steam_lang}).json()
            counts = {k: len((data.get(k) or {}).get("items") or []) for k in
                      ("new_releases", "top_sellers", "specials", "coming_soon")}
            print(f"{OK} витрина доступна: {counts}")
        except Exception as exc:
            print(f"{WARN} Steam не ответил: {exc}")
    else:
        print("  — выключен (ENABLE_STEAM=false)")

    # --- RSS ---
    print("\nRSS-ленты:")
    if cfg.enable_rss:
        feeds = rss.load_feeds(cfg.feeds_file)
        if not feeds:
            print(f"{WARN} в {cfg.feeds_file.name} нет ни одной активной ленты")
        for feed in feeds:
            if feed.lang != "ru" and not ai.enabled(cfg):
                print(f"{WARN} {feed.name}: не на русском, нужен ИИ-перевод (AI_* в .env) — будет пропущена")
                continue
            try:
                entries = rss.parse_feed(net.get(feed.url, timeout=25).content)
                dates = [e["published"] for e in entries if e["published"]]
                newest = ""
                if dates:
                    hours = (datetime.now(timezone.utc) - max(dates)).total_seconds() / 3600
                    newest = f", самая свежая — {hours:.0f} ч. назад"
                print(f"{OK} {feed.name}: записей {len(entries)}{newest}")
            except Exception as exc:
                print(f"{WARN} {feed.name}: не открылась ({exc}). Проверьте адрес на сайте.")
    else:
        print("  — выключены (ENABLE_RSS=false)")

    # --- дополнительно ---
    print("\nДополнительно:")
    print(f"{OK if shutil.which('ffmpeg') else WARN} ffmpeg: "
          + ("установлен — трейлеры Steam будут прикладываться видео"
             if shutil.which("ffmpeg") else "не найден — трейлеры Steam видео приложить не получится (см. инструкцию)"))
    print(f"{OK if ai.enabled(cfg) else '  —'} ИИ-перевод: " + ("настроен" if ai.enabled(cfg) else "не настроен (необязательно)"))
    print(f"{OK if cfg.youtube_api_key else '  —'} YouTube API: " + ("ключ задан" if cfg.youtube_api_key else "не настроен (необязательно)"))
    print(f"{OK if cfg.subscribe_button else '  —'} Кнопка «Подписаться»: "
          + ("под постами, входы считаются по пригласительной ссылке" if cfg.subscribe_button else "выключена (SUBSCRIBE_BUTTON=false)"))
    print(f"  Стиль постов: {cfg.post_style} " + {
        "prepod": "(как «ПРЕПОД STEAM»: строго и коротко)",
        "stars": "(как «PlayStation Stars»: факты пунктами •)",
        "platforma": "(как «Платформа»: эмодзи, пункты 🤩, вопрос в конце)",
    }.get(cfg.post_style, ""))
    print(f"  Публикация раз в {cfg.post_interval_minutes} мин., тихие часы: "
          + (f"{cfg.quiet_hours[0]}:00–{cfg.quiet_hours[1]}:00" if cfg.quiet_hours else "нет")
          + f", часовой пояс {cfg.timezone}")
    if cfg.hot_news:
        print(f"{OK} Горячие новости: проверка каждые {max(1, cfg.poll_seconds // 60)} мин., "
              f"важное публикуется сразу (пауза не меньше {cfg.hot_min_gap_minutes} мин., "
              f"порог интересности {cfg.hot_min_score:.1f})")
    else:
        print("  — Горячие новости выключены (HOT_NEWS=false): только плановые посты")

    print("\n" + ("Всё готово к запуску!" if problems == 0 else f"Нужно исправить: {problems}."))
    return 1 if problems else 0
