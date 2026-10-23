"""Обёртка над requests: общий User-Agent, таймауты и повторные попытки."""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; GameNewsBot/1.0; +https://core.telegram.org/bots)"

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"})


def get(
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: float = 25,
    retries: int = 3,
    stream: bool = False,
    headers: Optional[dict] = None,
) -> requests.Response:
    """GET с повторами. Ошибки 4xx (кроме 429) не повторяем — смысла нет."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = _session.get(url, params=params, timeout=timeout, stream=stream, headers=headers)
        except requests.RequestException as exc:
            last_exc = exc
        else:
            if resp.status_code < 400:
                return resp
            status = resp.status_code
            resp.close()
            error = requests.HTTPError(f"HTTP {status} для {url}", response=resp)
            if status != 429 and status < 500:
                raise error
            last_exc = error
        if attempt < retries:
            time.sleep(2 * attempt)
    assert last_exc is not None
    raise last_exc
