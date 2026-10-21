"""Доступ к Авто.ру через тот же JSON, которым пользуется фронтенд сайта.

Почему браузер, а не httpx: простой HTTP-клиент Авто.ру сразу отправляет на вход
(`NEED_AUTH`), а обычный браузер с собственной новой сессией получает те же данные,
что видит посетитель. Чужих cookie, аккаунтов и сервисов разгадывания капчи нет.
Страница открывается один раз за прогон, дальше — fetch() к /-/ajax/ из контекста страницы.
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Protocol

from .parse import SourceBlocked, SourceFormatError, check_listing_response

log = logging.getLogger("kiravto.source")

LISTING_EP = "/-/ajax/desktop-search/listing/"
CARD_EP = "/-/ajax/desktop-search/getOfferCard/"

# Фильтры по умолчанию прячут часть машин (без фото, битые, нерастаможенные).
# Снимаем их явно — так видно весь склад салона.
UNHIDE_FILTERS = {"has_image": False, "damage_group": "ANY", "customs_state_group": "DOESNT_MATTER"}
PAGE_SIZE = 50  # больше источник не отдаёт

# Таймаут обязателен: прогон #3 (21.10.2026) завис навсегда на fetch без ответа.
FETCH_JS = """async ([ep, body, timeoutMs]) => {
  const csrf = (document.cookie.match(/_csrf_token=([^;]+)/) || [])[1] || '';
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs || 30000);
  try {
    const r = await fetch(ep, {method: 'POST', credentials: 'include', signal: ctrl.signal,
        headers: {'content-type': 'application/json', 'x-csrf-token': csrf}, body: JSON.stringify(body)});
    return {status: r.status, url: r.url, text: await r.text()};
  } catch (e) {
    return {status: 0, url: ep, text: '', error: String(e)};
  } finally {
    clearTimeout(timer);
  }
}"""
FETCH_TIMEOUT_MS = 30_000


class Source(Protocol):
    requests: int

    def listing(self, section: str, page: int, sort: str | None = None) -> dict: ...

    def card(self, offer_ref: str) -> dict: ...

    def close(self) -> None: ...


class TransientError(Exception):
    pass


def _decode(resp: dict, what: str) -> dict:
    text = resp.get("text") or ""
    url = resp.get("url") or ""
    if "showcaptcha" in url or "captcha" in text[:2000].lower() and text.lstrip().startswith("<"):
        raise SourceBlocked(f"{what}: капча ({url[:80]})")
    if "auth.auto.ru" in url:
        raise SourceBlocked(f"{what}: редирект на вход")
    status = resp.get("status", 0)
    if resp.get("error"):
        raise TransientError(f"{what}: {resp['error'][:80]}")
    if status in (429,) or status >= 500:
        raise TransientError(f"{what}: HTTP {status}")
    if status == 403:
        raise SourceBlocked(f"{what}: HTTP 403")
    if not text.strip():
        # разовый пустой ответ (прогон #2, 21.10.2026); тот же запрос через минуту отдал карточку целиком
        raise TransientError(f"{what}: пустой ответ (HTTP {status})")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if text.lstrip().startswith("<"):
            raise TransientError(f"{what}: вместо JSON пришёл HTML (HTTP {status})")
        raise SourceFormatError(f"{what}: ответ не JSON: {text[:120]!r}")


class AutoruBrowserSource:
    def __init__(self, dealer_code: str, dealer_id: str, headless: bool = True,
                 pause: tuple[float, float] = (2.0, 4.5), retries: int = 3):
        self.dealer_code = dealer_code
        self.dealer_id = dealer_id
        self.pause = pause
        self.retries = retries
        self.requests = 0
        self.errors = 0
        self._headless = headless
        self._pw = None
        self._browser = None
        self._page = None

    # --- жизненный цикл ---
    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        ctx = self._browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow")
        self._page = ctx.new_page()
        resp = self._page.goto(f"https://auto.ru/diler/cars/all/{self.dealer_code}/", wait_until="domcontentloaded",
                               timeout=60_000)
        self.requests += 1
        url = self._page.url
        if "showcaptcha" in url or "auth.auto.ru" in url:
            raise SourceBlocked(f"страница салона открылась как {url[:80]}")
        # Из-за рубежа сайт показывает «не предназначен для вашего региона» — это просто согласие
        try:
            btn = self._page.get_by_text("Я согласен", exact=False)
            if btn.count():
                btn.first.click(timeout=3000)
        except Exception:  # noqa: BLE001 — кнопки может не быть, это нормально
            pass
        if resp is not None and resp.status == 404:
            raise SourceFormatError(f"страница салона {self.dealer_code} вернула 404")
        return self._page

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        finally:
            self._page = self._browser = self._pw = None

    # --- запросы ---
    def _post(self, ep: str, body: dict, what: str) -> dict:
        delay = 5.0
        network_errors = 0
        for attempt in range(1, self.retries + 1):
            time.sleep(random.uniform(*self.pause))
            try:
                # открытие страницы тоже внутри повторов: при обрыве сети падает именно оно
                page = self._ensure_page()
                self.requests += 1
                resp = page.evaluate(FETCH_JS, [ep, body, FETCH_TIMEOUT_MS])
                return _decode(resp, what)
            except TransientError as e:
                self.errors += 1
                log.warning("%s, попытка %s/%s, ждём %.0f с", e, attempt, self.retries, delay)
            except SourceBlocked:
                self.errors += 1
                raise
            except SourceFormatError:
                raise
            except Exception as e:  # сеть оборвалась, страница упала
                self.errors += 1
                network_errors += 1
                log.warning("%s: %s, попытка %s/%s", what, e.__class__.__name__, attempt, self.retries)
                self.close()
            time.sleep(delay)
            delay *= 2
        # для сборщика и то и другое — «неполный прогон, никого не снимаем»; в записи прогона различаем причину
        reason = "нет сети или страница не открывается" if network_errors == self.retries else "источник не отвечает"
        raise SourceBlocked(f"{what}: {reason} после {self.retries} попыток")

    def listing(self, section: str, page: int, sort: str | None = None) -> dict:
        body = {"category": "cars", "section": section, "dealer_id": self.dealer_id, "dealer_code": self.dealer_code,
                "page": page, "page_size": PAGE_SIZE, "geo_id": [], **UNHIDE_FILTERS}
        if sort:
            body["sort"] = sort
        return check_listing_response(self._post(LISTING_EP, body, f"выдача {section} стр.{page}"))

    def card(self, offer_ref: str) -> dict:
        return self._post(CARD_EP, {"category": "cars", "offer_id": offer_ref}, f"карточка {offer_ref}")

