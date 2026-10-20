"""Справочник марок/моделей, человеческие подписи к кодам источника, поиск аномалий."""
from __future__ import annotations

import re
import statistics
from datetime import date

# Канон — код марки Авто.ру. Справа — как марку пишут люди и другие сайты.
MARKS: dict[str, tuple[str, list[str]]] = {
    "MERCEDES": ("Mercedes-Benz", ["mercedes-benz", "mercedes benz", "mercedes", "мерседес", "мерседес-бенц", "mb", "мб", "benz"]),
    "BMW": ("BMW", ["bmw", "бмв", "бэмвэ"]),
    "TOYOTA": ("Toyota", ["toyota", "тойота", "таета"]),
    "LEXUS": ("Lexus", ["lexus", "лексус"]),
    "LAND_ROVER": ("Land Rover", ["land rover", "land-rover", "landrover", "ленд ровер", "лэнд ровер", "range rover"]),
    "KIA": ("Kia", ["kia", "киа"]),
    "HYUNDAI": ("Hyundai", ["hyundai", "хендай", "хёндай", "хундай", "хюндай"]),
    "VOLKSWAGEN": ("Volkswagen", ["volkswagen", "vw", "фольксваген"]),
    "PORSCHE": ("Porsche", ["porsche", "порше"]),
    "NISSAN": ("Nissan", ["nissan", "ниссан"]),
    "AUDI": ("Audi", ["audi", "ауди"]),
    "SKODA": ("Skoda", ["skoda", "škoda", "шкода"]),
    "CHERY": ("Chery", ["chery", "чери"]),
    "GEELY": ("Geely", ["geely", "джили"]),
    "FORD": ("Ford", ["ford", "форд"]),
    "MAZDA": ("Mazda", ["mazda", "мазда"]),
    "OMODA": ("Omoda", ["omoda", "омода"]),
    "CHANGAN": ("Changan", ["changan", "чанган"]),
    "RENAULT": ("Renault", ["renault", "рено"]),
    "OPEL": ("Opel", ["opel", "опель"]),
    "CHEVROLET": ("Chevrolet", ["chevrolet", "шевроле"]),
    "CITROEN": ("Citroen", ["citroen", "citroën", "ситроен"]),
    "MITSUBISHI": ("Mitsubishi", ["mitsubishi", "митсубиси", "мицубиси"]),
    "EXEED": ("Exeed", ["exeed", "эксид"]),
    "LYNK_CO": ("Lynk & Co", ["lynk & co", "lynk&co", "lynk and co", "линк энд ко"]),
    "HAVAL": ("Haval", ["haval", "хавал", "хавейл"]),
    "VOLVO": ("Volvo", ["volvo", "вольво"]),
    "VOYAH": ("Voyah", ["voyah", "воя"]),
    "JAECOO": ("Jaecoo", ["jaecoo", "джейку"]),
    "GENESIS": ("Genesis", ["genesis", "генезис"]),
    "JEEP": ("Jeep", ["jeep", "джип"]),
    "VAZ": ("Lada (ВАЗ)", ["lada", "лада", "ваз", "vaz", "lada (ваз)"]),
    "HONGQI": ("Hongqi", ["hongqi", "хончи", "хунци"]),
    "SUBARU": ("Subaru", ["subaru", "субару"]),
    "INFINITI": ("Infiniti", ["infiniti", "инфинити"]),
    "UAZ": ("УАЗ", ["уаз", "uaz"]),
    "SUZUKI": ("Suzuki", ["suzuki", "сузуки"]),
    "ZEEKR": ("Zeekr", ["zeekr", "зикр"]),
    "TANK": ("Tank", ["tank", "танк"]),
    "ROLLS_ROYCE": ("Rolls-Royce", ["rolls-royce", "rolls royce", "роллс-ройс"]),
    "CADILLAC": ("Cadillac", ["cadillac", "кадиллак"]),
    "JETOUR": ("Jetour", ["jetour", "джетур"]),
    "LIXIANG": ("LiXiang", ["lixiang", "li auto", "li", "лисян", "ли сян"]),
    "MINI": ("MINI", ["mini", "мини"]),
}

_ALIAS: dict[str, str] = {}
for _code, (_name, _aliases) in MARKS.items():
    _ALIAS[_code.lower()] = _code
    _ALIAS[_name.lower()] = _code
    for _a in _aliases:
        _ALIAS[_a] = _code


def _norm_text(s: str) -> str:
    return re.sub(r"[\s_]+", " ", s.strip().lower().replace("ё", "е"))


def resolve_mark(text: str | None) -> str | None:
    """«Мерседес», «MB», «mercedes-benz» → MERCEDES. Неизвестное → код в верхнем регистре."""
    if not text:
        return None
    t = _norm_text(text)
    if t in _ALIAS:
        return _ALIAS[t]
    t2 = t.replace("ё", "е")
    for alias, code in _ALIAS.items():
        if alias.replace("ё", "е") == t2:
            return code
    return re.sub(r"[^A-Z0-9]+", "_", text.strip().upper()).strip("_") or None


def mark_name(code: str | None, fallback: str | None = None) -> str | None:
    if code and code in MARKS:
        return MARKS[code][0]
    return fallback


def resolve_model(text: str | None) -> str | None:
    """Модель сравниваем по коду Авто.ру: «Land Cruiser Prado» → LAND_CRUISER_PRADO."""
    if not text:
        return None
    return re.sub(r"[^A-Z0-9А-Я]+", "_", text.strip().upper()).strip("_")


# Палитра Авто.ру: сайт хранит цвет кодом, а показывает словом.
COLORS = {
    "040001": "чёрный", "FAFBFB": "белый", "97948F": "серый", "CACECB": "серебристый",
    "0000CC": "синий", "EE1D19": "красный", "007F00": "зелёный", "200204": "коричневый",
    "C49648": "бежевый", "22A0F8": "голубой", "DEA522": "золотистый", "660099": "фиолетовый",
    "4A2197": "пурпурный", "FFD600": "жёлтый", "FF8649": "оранжевый", "FFC0CB": "розовый",
}

FUEL = {"GASOLINE": "бензин", "DIESEL": "дизель", "HYBRID": "гибрид", "ELECTRO": "электро", "LPG": "газ"}
TRANSMISSION = {"AUTOMATIC": "автомат", "VARIATOR": "вариатор", "ROBOT": "робот", "MECHANICAL": "механика"}
DRIVE = {"ALL_WHEEL_DRIVE": "полный", "FORWARD_CONTROL": "передний", "REAR_DRIVE": "задний"}
AVAILABILITY = {"IN_STOCK": "в наличии", "IN_TRANSIT": "в пути", "ON_ORDER": "под заказ"}
PTS = {"ORIGINAL": "оригинал", "DUPLICATE": "дубликат"}


def find_anomalies(offer: dict, model_medians: dict[tuple[str, str], float], today: date | None = None) -> list[str]:
    """Коды аномалий. Ничего не выбрасываем — только помечаем."""
    today = today or date.today()
    out: list[str] = []
    price = offer.get("price") or 0
    if price <= 1000:
        out.append("price_too_low")
    if offer.get("section") == "used" and not offer.get("mileage"):
        out.append("zero_mileage_used")
    year = offer.get("year")
    if year and year > today.year + 1:
        out.append("year_in_future")
    if year and year < 1950:
        out.append("year_too_old")
    med = model_medians.get((offer.get("mark_code") or "", offer.get("model_code") or ""))
    if med and price and price < med / 10:
        out.append("price_10x_below_median")
    if offer.get("section") == "used" and (offer.get("mileage") or 0) > 1_500_000:
        out.append("mileage_too_high")
    return out


def model_medians(offers: list[dict]) -> dict[tuple[str, str], float]:
    groups: dict[tuple[str, str], list[int]] = {}
    for o in offers:
        if o.get("price"):
            groups.setdefault((o.get("mark_code") or "", o.get("model_code") or ""), []).append(o["price"])
    return {k: statistics.median(v) for k, v in groups.items()}
