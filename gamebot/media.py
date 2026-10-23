"""Картинки и видео: скачиваем сами и отправляем в Telegram файлом.

Так надёжнее, чем отдавать Telegram ссылки: он иногда не может их открыть.
"""
from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional

from PIL import Image

from . import net
from .config import Config
from .models import Post

log = logging.getLogger(__name__)

MAX_IMAGE_DOWNLOAD = 12_000_000      # больше не скачиваем
MAX_PHOTO_BYTES = 9_500_000          # лимит Telegram на фото — 10 МБ
MAX_VIDEO_BYTES = 49_000_000         # лимит Telegram для ботов — 50 МБ

_RESAMPLE = getattr(getattr(Image, "Resampling", Image), "LANCZOS")


# ---------------------------------------------------------------- картинки
def fetch_image(url: str) -> Optional[bytes]:
    """Скачивает картинку и приводит к JPEG, который точно примет Telegram."""
    try:
        resp = net.get(url, stream=True, timeout=30, retries=2)
        buf = bytearray()
        try:
            for chunk in resp.iter_content(65536):
                buf += chunk
                if len(buf) > MAX_IMAGE_DOWNLOAD:
                    log.info("Картинка слишком большая, пропускаю: %s", url)
                    return None
        finally:
            resp.close()
        return normalize_image(bytes(buf))
    except Exception as exc:
        log.warning("Картинка не скачалась (%s): %s", url, exc)
        return None


def normalize_image(raw: bytes) -> Optional[bytes]:
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None
    width, height = img.size
    if width < 320 or height < 180:           # иконки и «пиксели» не нужны
        return None
    if max(width / height, height / width) > 3.5:   # слишком вытянутые Telegram не любит
        return None

    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert("RGB")

    longest = max(img.size)
    if longest > 2560:
        scale = 2560 / longest
        img = img.resize((int(img.width * scale), int(img.height * scale)), _RESAMPLE)

    for quality in (88, 75, 60):
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality, optimize=True)
        if out.tell() <= MAX_PHOTO_BYTES:
            return out.getvalue()
    return None


# ---------------------------------------------------------------- видео
def prepare_video(cfg: Config, post: Post, workdir: Path) -> Optional[Path]:
    """Возвращает путь к mp4 (не больше ~49 МБ) или None, если трейлер получить не удалось."""
    target = workdir / "trailer.mp4"
    if post.video_url:
        if _download_video(post.video_url, target):
            return target
    if post.hls_url and cfg.use_ffmpeg and shutil.which("ffmpeg"):
        if hls_to_mp4(post.hls_url, target, cfg.max_video_seconds):
            return target
    elif post.hls_url and cfg.use_ffmpeg:
        log.info("Трейлер есть, но ffmpeg не установлен — видео пропущено (см. инструкцию)")
    return None


def _download_video(url: str, dest: Path) -> bool:
    try:
        resp = net.get(url, stream=True, timeout=40, retries=2)
        try:
            length = int(resp.headers.get("Content-Length") or 0)
            if length > MAX_VIDEO_BYTES:
                log.info("Трейлер больше 49 МБ (%.1f МБ) — пропускаю", length / 1e6)
                return False
            written = 0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(262144):
                    written += len(chunk)
                    if written > MAX_VIDEO_BYTES:
                        log.info("Трейлер оказался больше 49 МБ — пропускаю")
                        return False
                    fh.write(chunk)
        finally:
            resp.close()
        return written > 50_000
    except Exception as exc:
        log.warning("Трейлер не скачался (%s): %s", url, exc)
        return False


def hls_to_mp4(url: str, dest: Path, max_seconds: int) -> bool:
    """Скачивает HLS-трейлер Steam и ужимает до mp4 (720p, не более max_seconds секунд)."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", url,
        "-t", str(max_seconds),
        "-vf", "scale='min(1280,iw)':-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-maxrate", "1800k", "-bufsize", "3600k", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=420)
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg не успел обработать трейлер за 7 минут")
        return False
    except subprocess.CalledProcessError as exc:
        log.warning("ffmpeg не смог обработать трейлер: %s", exc.stderr.decode("utf-8", "ignore")[-300:])
        return False
    except OSError as exc:
        log.warning("ffmpeg не запустился: %s", exc)
        return False
    if not dest.exists():
        return False
    size = dest.stat().st_size
    if size > MAX_VIDEO_BYTES or size < 50_000:
        log.info("Получилось видео подозрительного размера (%d байт) — пропускаю", size)
        return False
    return True


def probe(path: Path) -> Dict[str, int]:
    """Размер и длительность видео (чтобы Telegram показал его правильно). Без ffprobe — пусто."""
    if not shutil.which("ffprobe"):
        return {}
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, timeout=30, check=True,
        ).stdout
        data = json.loads(out)
        stream = (data.get("streams") or [{}])[0]
        meta = {
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
            "duration": int(float((data.get("format") or {}).get("duration") or 0)),
        }
        return {k: v for k, v in meta.items() if v}
    except Exception:
        return {}
