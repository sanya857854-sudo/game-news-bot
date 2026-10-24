"""Источник: RSS-ленты игровых сайтов (список лент — в файле feeds.txt)."""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

from . import ai, formatting, net
from .config import Config
from .models import Candidate, Post
from .storage import Storage

log = logging.getLogger(__name__)

YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/embed/|youtube-nocookie\.com/embed/|youtu\.be/|youtube\.com/watch\?v=)([\w-]{11})"
)
IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
BAD_IMAGE_HINTS = ("logo", "avatar", "pixel", "spacer", "emoji", "icon", ".gif", ".svg")


@dataclass
class Feed:
    lang: str
    name: str
    url: str


def load_feeds(path: Path) -> List[Feed]:
    """Формат строки: язык | название | адрес.  Строки с # игнорируются."""
    feeds: List[Feed] = []
    if not path.exists():
        log.warning("Файл с лентами не найден: %s", path)
        return feeds
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or not parts[2].startswith("http"):
            log.warning("Не понял строку в feeds.txt: %r (нужно: ru | Название | https://адрес)", line)
            continue
        feeds.append(Feed(lang=parts[0].lower(), name=parts[1], url=parts[2]))
    return feeds


# ---------------------------------------------------------------- разбор RSS/Atom
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _parse_date(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_feed(data: bytes) -> List[Dict]:
    """Простой разбор RSS 2.0 и Atom без сторонних библиотек."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        cleaned = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", data)
        root = ET.fromstring(cleaned)

    entries: List[Dict] = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        entry = {"title": "", "link": "", "guid": "", "published": None,
                 "summary_html": "", "content_html": "", "images": []}
        for child in node:
            name = _local(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                entry["title"] = text
            elif name == "link":
                href = child.attrib.get("href")
                if href:
                    if child.attrib.get("rel", "alternate") == "alternate" or not entry["link"]:
                        entry["link"] = href.strip()
                elif text:
                    entry["link"] = text
            elif name in ("guid", "id"):
                entry["guid"] = text
            elif name in ("pubdate", "published", "updated", "date"):
                if entry["published"] is None:
                    entry["published"] = _parse_date(text)
            elif name in ("description", "summary"):
                entry["summary_html"] = child.text or ""
            elif name in ("encoded", "content"):
                if child.text:  # у media:content текста нет — его не трогаем
                    entry["content_html"] = child.text
            elif name == "enclosure":
                if child.attrib.get("type", "").startswith("image") and child.attrib.get("url"):
                    entry["images"].append(child.attrib["url"])
        # картинки из media:content / media:thumbnail
        for child in node:
            if _local(child.tag) in ("content", "thumbnail") and child.attrib.get("url"):
                medium = child.attrib.get("medium", "image")
                ctype = child.attrib.get("type", "image/")
                if medium == "image" and ctype.startswith("image") and child.attrib["url"] not in entry["images"]:
                    entry["images"].append(child.attrib["url"])
        for html_part in (entry["content_html"], entry["summary_html"]):
            for src in IMG_RE.findall(html_part or ""):
                if src not in entry["images"]:
                    entry["images"].append(src)
        entries.append(entry)
    return entries


# ---------------------------------------------------------------- метаданные страницы
class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: Dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        a = dict(attrs)
        key = (a.get("property") or a.get("name") or "").lower()
        content = a.get("content")
        if key and content and key not in self.meta:
            self.meta[key] = content


def fetch_page_meta(url: str) -> Dict[str, str]:
    """Берёт со страницы новости картинку и описание для соцсетей (og:image, og:description)."""
    try:
        resp = net.get(url, timeout=20, retries=2)
        content_type = resp.headers.get("Content-Type", "")
        if "charset" not in content_type.lower():
            resp.encoding = resp.apparent_encoding
        parser = _MetaParser()
        parser.feed(resp.text[:400_000])
    except Exception as exc:
        log.debug("Страница %s не прочиталась: %s", url, exc)
        return {}
    meta = parser.meta
    image = meta.get("og:image") or meta.get("twitter:image") or ""
    return {
        "image": urljoin(url, image) if image else "",
        "description": meta.get("og:description") or meta.get("description") or "",
    }


def _usable_image(url: str) -> bool:
    low = url.lower()
    return url.startswith("http") and not any(hint in low for hint in BAD_IMAGE_HINTS)


# ---------------------------------------------------------------- кандидаты
def list_candidates(cfg: Config, storage: Storage) -> List[Candidate]:
    feeds = load_feeds(cfg.feeds_file)
    now = datetime.now(timezone.utc)
    max_age = timedelta(hours=cfg.rss_max_age_hours)
    candidates: List[Candidate] = []

    for feed in feeds:
        if feed.lang != "ru" and not ai.enabled(cfg):
            log.info("Лента «%s» не на русском, а ИИ-перевод не настроен — пропускаю", feed.name)
            continue
        try:
            entries = parse_feed(net.get(feed.url, timeout=25).content)
        except Exception as exc:
            log.warning("Лента «%s» недоступна: %s", feed.name, exc)
            continue

        for entry in entries[:25]:
            if not entry["title"] or not entry["link"]:
                continue
            published = entry["published"] or (now - timedelta(hours=6))
            age = now - published
            if age > max_age or age < timedelta(hours=-2):
                continue
            key = "rss:" + hashlib.sha1((entry["guid"] or entry["link"]).encode("utf-8")).hexdigest()[:20]
            if storage.is_known(key):
                continue
            freshness = max(0.0, 1.0 - age / max_age)
            title = formatting.clean_text(entry["title"])

            # Пропуск неигрового мусора (косплей, кино, железо)
            title_low = title.lower()
            if any(kw in title_low for kw in cfg.rss_exclude_keywords):
                log.debug("Пропуск неигровой темы в RSS: %s", title)
                continue

            candidates.append(
                Candidate(
                    key=key,
                    kind="news",
                    title=title,
                    score=3.0 + freshness,
                    build=lambda f=feed, e=entry: build_post(cfg, f, e),
                )
            )
    log.info("RSS: найдено кандидатов — %d", len(candidates))
    return candidates


def build_post(cfg: Config, feed: Feed, entry: Dict) -> Optional[Post]:
    title = formatting.clean_text(entry["title"])
    summary = formatting.shorten(formatting.clean_text(entry["summary_html"]), 280)
    image = next((u for u in entry["images"] if _usable_image(u)), "")

    if not image or len(summary) < 40:
        meta = fetch_page_meta(entry["link"])
        image = image or meta.get("image", "")
        if len(summary) < 40:
            summary = formatting.shorten(formatting.clean_text(meta.get("description", "")), 280) or summary
    if image:
        image = urljoin(entry["link"], image)

    if summary and (summary.lower().startswith(title.lower()) or title.lower().startswith(summary.lower())):
        summary = ""  # лента дублирует заголовок — текст не нужен

    if feed.lang != "ru":
        translated = ai.translate_news(cfg, title, summary)
        if not translated:
            raise RuntimeError("не удалось перевести новость (проверьте настройки ИИ)")
        title, summary = translated

    html_blob = f"{entry['content_html']} {entry['summary_html']}"
    youtube = YOUTUBE_RE.search(html_blob)
    trailer_url = f"https://www.youtube.com/watch?v={youtube.group(1)}" if youtube else None

    text = formatting.news_post_text(cfg, title, summary, feed.name, entry["link"], with_slot=bool(trailer_url))
    return Post(
        text=text,
        photos=[image] if image else [],
        trailer_url=trailer_url,
        link=entry["link"],
        button_text=f"📖 Читать полностью ({source_name})",
        button_url=entry["link"],
    )
