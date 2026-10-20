"""Разбор JSON Авто.ру (тот же, что получает фронтенд сайта) в плоскую запись.

Если в ответе нет обязательного поля — бросаем SourceFormatError с путём до поля.
Лучше упасть с понятной ошибкой, чем тихо писать пустые колонки.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .. import normalize as nz


class SourceFormatError(Exception):
    """Источник поменял формат ответа."""


class SourceBlocked(Exception):
    """Капча, требование входа или другая защита. Прогон станет неполным."""


def _req(obj: dict, path: str):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise SourceFormatError(f"нет обязательного поля '{path}' (оборвалось на '{part}')")
        cur = cur[part]
    return cur


def _opt(obj: dict, path: str, default=None):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _ts(ms) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def check_listing_response(data: dict) -> dict:
    """Проверяет конверт ответа выдачи, различает защиту и смену формата."""
    if not isinstance(data, dict):
        raise SourceFormatError("ответ выдачи не JSON-объект")
    if data.get("status") == "ERROR":
        if data.get("action_kind") == "NEED_AUTH" or data.get("error") in ("ACTION_REQUIRED", "CAPTCHA"):
            raise SourceBlocked(f"источник требует вход/капчу: {data.get('error')} {data.get('action_kind', '')}".strip())
        raise SourceFormatError(f"выдача вернула ошибку {data.get('error')}: {str(data.get('detailed_error'))[:200]}")
    offers = _req(data, "offers")
    if not isinstance(offers, list):
        raise SourceFormatError("'offers' не список")
    _req(data, "pagination.total_offers_count")
    _req(data, "pagination.total_page_count")
    return data


def photo_url(image: dict) -> str:
    """Ссылка на фото в исходном разрешении: алиас 'orig' на CDN avatars.avto.ru."""
    sizes = _req(image, "sizes")
    any_size = sizes.get("1200x900") or next(iter(sizes.values()))
    base = any_size.rsplit("/", 1)[0]
    if base.startswith("//"):
        base = "https:" + base
    return base + "/orig"


def photo_preview_url(image: dict) -> str:
    sizes = _req(image, "sizes")
    u = sizes.get("320x240") or next(iter(sizes.values()))
    return "https:" + u if u.startswith("//") else u


def parse_offer(raw: dict) -> dict:
    """Одна запись выдачи или карточки → плоский словарь полей сервиса."""
    vi = _req(raw, "vehicle_info")
    docs = _req(raw, "documents")
    state = _req(raw, "state")
    price = _req(raw, "price_info.price")
    mark_info = _req(vi, "mark_info")
    model_info = _req(vi, "model_info")
    tech = _req(vi, "tech_param")

    disc = raw.get("discount_options") or {}
    max_disc = disc.get("max_discount") or 0
    vin = docs.get("vin") or None
    images = state.get("image_urls") or []
    color_hex = (raw.get("color_hex") or "").upper() or None
    volume = tech.get("displacement")
    section = _req(raw, "section")

    offer = {
        "id": str(_req(raw, "id")),
        "hash": _req(raw, "hash"),
        "url": _req(raw, "url"),
        "section": section,
        "mark_code": nz.resolve_mark(mark_info.get("code") or mark_info.get("name")),
        "mark": nz.mark_name(nz.resolve_mark(mark_info.get("code")), mark_info.get("name")),
        "model_code": model_info.get("code"),
        "model": model_info.get("name"),
        "generation": _opt(vi, "super_gen.name") or None,
        "complectation": _opt(vi, "complectation.name") or None,
        "complectation_id": _opt(vi, "complectation.id") or None,
        "year": docs.get("year"),
        "mileage": state.get("mileage") or 0,
        "price": int(price),
        "price_discount": int(price) - int(max_disc) if max_disc else int(price),
        "discount_tradein": disc.get("tradein"),
        "discount_credit": disc.get("credit"),
        "discount_insurance": disc.get("insurance"),
        "discount_max": max_disc or None,
        "engine_volume": round(volume / 1000, 1) if volume else None,
        "engine_power": tech.get("power"),
        "fuel": nz.FUEL.get(tech.get("engine_type"), tech.get("engine_type")),
        "transmission": nz.TRANSMISSION.get(tech.get("transmission"), tech.get("transmission")),
        "drive": nz.DRIVE.get(tech.get("gear_type"), tech.get("gear_type")),
        "body": _opt(vi, "configuration.human_name"),
        "color_hex": color_hex,
        "color": nz.COLORS.get(color_hex or "", color_hex),
        "vin": vin,
        "vin_full": bool(vin and "*" not in vin and len(vin) == 17),
        # владельцы/ПТС/растаможка имеют смысл только для машин с пробегом
        "owners": docs.get("owners_number") if section == "used" else None,
        "pts": nz.PTS.get(docs.get("pts"), docs.get("pts")) if section == "used" else None,
        "customs_cleared": docs.get("custom_cleared"),
        "availability": raw.get("availability"),
        "status_source": raw.get("status"),
        "photos": [photo_url(i) for i in images],
        "photo_preview": photo_preview_url(images[0]) if images else None,
        "photo_count": state.get("images_count") or len(images),
        "photo_names": [i.get("sizes", {}).get("320x240", "").rsplit("/", 2)[-2] for i in images],
        "created_at_source": _ts(_opt(raw, "additional_info.creation_date")),
        "fresh_at_source": _ts(_opt(raw, "additional_info.fresh_date")),
        "description": raw.get("description"),
        "dealer_code": _opt(raw, "salon.code"),
    }
    if offer["mark_code"] is None or offer["model_code"] is None:
        raise SourceFormatError(f"у объявления {offer['id']} нет марки или модели")
    return offer


def listing_signature(o: dict) -> str:
    """Что видно в выдаче и по чему решаем, нужна ли свежая карточка."""
    key = [o["price"], o["price_discount"], o["mileage"], o["availability"], o["complectation_id"],
           o["photo_count"], o["photo_names"][:1], o["fresh_at_source"]]
    return hashlib.sha1(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]


def parse_card(raw: dict) -> dict | None:
    """Карточка объявления. None — объявления больше нет (OFFER_NOT_FOUND)."""
    if raw.get("status") == "ERROR":
        if raw.get("error") == "OFFER_NOT_FOUND":
            return None
        if raw.get("action_kind") == "NEED_AUTH" or raw.get("error") in ("ACTION_REQUIRED", "CAPTCHA"):
            raise SourceBlocked(f"карточка: {raw.get('error')}")
        raise SourceFormatError(f"карточка вернула ошибку {raw.get('error')}")
    offer = parse_offer(raw)
    if "description" not in raw:
        raise SourceFormatError(f"в карточке {offer['id']} нет поля 'description'")
    return offer
