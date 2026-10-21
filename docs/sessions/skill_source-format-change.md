# Скилл source-format-change сработал сам: разбор «нет обязательного поля»

Экспорт stream-json сессии `claude -p` (модель sonnet) в корне проекта, 21.10.2026.
Список MCP-серверов машины и системный промпт убраны; токенов в сессиях нет (проверено регэкспом).

**→ Skill** `{"skill": "source-format-change"}`
```
Launching skill: source-format-change
```
**→ Grep** `{"pattern": "price_info|_req\\(|\"price\"", "path": "C:\\Users\\filzu\\kiravto\\kiravto", "output_mode": "content"}`
**→ Bash** `{"command": "ls data/fresh tests/fixtures/autoru && .venv/Scripts/python -I - <<'EOF'\nimport json,glob\ndef find(o,p=\"\"):\n    if isinstance(o,dict):\n        for k,v in o.items():\n            if 'price' in k.lower(): yield p+\"/\"+k, v if not isinstance(v,(dict,list)) else type(v).__name__+(str(list(v)[:12]) if isinstance(v,dict) else \"\")\n            yield from find(v,p+\"/\"+k)\n    elif `
```
kiravto\source\parse.py:24:def _req(obj: dict, path: str):
kiravto\source\parse.py:65:    offers = _req(data, "offers")
kiravto\source\parse.py:68:    _req(data, "pagination.total_offers_count")
kiravto\source\parse.py:69:    _req(data, "pagination.total_page_count")
kiravto\source\parse.py:75:    sizes = _req(image, "sizes")
kiravto\source\parse.py:84:    sizes = _req(image, "sizes")
kiravto\source\parse.py:91:    vi = _req(raw, "vehicle_info")
kiravto\source\parse.py:92:    docs = _req(raw, "documents")
kiravto\source\parse.py:93:    state = _req(raw, "state")
kiravto\source\parse.py:94:    price = _req(raw, "price_info.price")
kiravto\source\parse.py:95:    mark_info = _req(vi, "mark_info")
kiravto\source\parse.py:96:    model_info = _req(vi, "model_info")
kiravto\source\parse.py:97:   
…
```
```
This Bash command contains multiple operations. The following part requires approval: .venv/Scripts/python -I -
```
**→ Read** `{"file_path": "C:\\Users\\filzu\\kiravto\\kiravto\\source\\parse.py", "limit": 170}`
**→ Glob** `{"pattern": "**/*.json", "path": "C:\\Users\\filzu\\kiravto\\tests\\fixtures\\autoru"}`
**→ Glob** `{"pattern": "*", "path": "C:\\Users\\filzu\\kiravto\\data\\fresh"}`
```
1	"""Разбор JSON Авто.ру (тот же, что получает фронтенд сайта) в плоскую запись.
2	
3	Если в ответе нет обязательного поля — бросаем SourceFormatError с путём до поля.
4	Лучше упасть с понятной ошибкой, чем тихо писать пустые колонки.
5	"""
6	from __future__ import annotations
7	
8	import hashlib
9	import json
10	import re
11	from datetime import datetime, timezone
12	
13	from .. import normalize as nz
14	
15	
16	class SourceFormatError(Exception):
17	    """Источник поменял формат ответа."""
18	
19	
20	class SourceBlocked(Exception):
21	    """Капча, требование входа или другая защита. Прогон станет неполным."""
22	
23	
24	def _req(obj: dict, path: str):
25	    cur = obj
26	    for part in path.split("."):
27	        if not isinstance(cur, dict) or part not in cur:
28	            raise So
…
```
```
tests\fixtures\autoru\card_1000000001-deadbeef.json
tests\fixtures\autoru\card_1160027054-72d39620.json
tests\fixtures\autoru\card_1160094460-61dcf15b.json
tests\fixtures\autoru\card_1160106218-047e09c4.json
tests\fixtures\autoru\card_1160201318-8fbcc743.json
tests\fixtures\autoru\listing_new_p1.json
tests\fixtures\autoru\listing_used_p1.json
tests\fixtures\autoru\card_removed_1160201304.json
```
```
data\fresh\listing_used_p1.json
```
**→ PowerShell** `{"command": "$py = @'\nimport json,sys\nfrom collections import Counter\ndef load(p): return json.load(open(p,encoding=\"utf-8\"))\nnew=load(\"data/fresh/listing_used_p1.json\"); old=load(\"tests/fixtures/autoru/listing_used_p1.json\")\nfor n,d in ((\"old\",old),(\"new\",new)):\n    print(\"==\",n,\"keys\",list(d)[:12],\"offers\",len(d[\"offers\"]),\"pagination\",d.get(\"pagination\"))\n    o=d[\"`
```
This PowerShell command contains multiple operations. The following part requires approval: $py = @'
import json,sys
from collections import Counter
def load(p): return json.load(open(p,encoding="utf-8"))
new=load("data/fresh/listing_used_p1.json"); old=load("tests/fixtures/autoru/listing_used_p1.json")
for n,d in (("old",old),("new",new)):
    print("==",n,"keys",list(d)[:12],"offers",len(d["offers"]),"pagination",d.get("pagination"))
    o=d["offers"][0]
    print(" offer keys",sorted(o))
    print(" price_info",o.get("price_info"))
    print(" price-ish",{k:(v if not isinstance(v,(dict,list)) else type(v).__name__) for k,v in o.items() if "price" in k.lower() or "cost" in k.lower()})
    c=Counter(); 
    for x in d["offers"]:
        c["price_info" in x, "price_info.price" if isinstanc
…
```
**→ Grep** `{"pattern": "\"price_info\":\\{[^}]*\\}|\"price_info\":\\{.{0,300}", "path": "C:\\Users\\filzu\\kiravto\\tests\\fixtures\\autoru\\listing_used_p1.json", "output_mode": "content", "-o": true, "head_limit": 2}`
**→ Grep** `{"pattern": "\"price[A-Za-z_]*\"\\s*:\\s*(\\{.{0,200}|[^,{]{0,40})", "path": "C:\\Users\\filzu\\kiravto\\data\\fresh\\listing_used_p1.json", "output_mode": "content", "-o": true, "head_limit": 8}`
```
No matches found
```
```
1:"price_info": {"currency": "RUR", "EUR": 29546, "RUR": 2749000, "USD": 33165, "with_nds": false, "amount": 2749000}, "salon": {"actual_stock": true, "client_id": "13311", "code": "rolf_yasenevo_moskva", "dealer_id":
1:"price_stats": {}, "days_in_stock": 21, "days_on_sale": 21, "can_view": false, "app2app_calls_disabled": false, "displayed_creation_date": "1790692246097", "calls_and_messages_only_from_verified": false, "original_re
1:"price_history": [
1:"price": 36000
1:"price": {"21026245": {"price": 2581000, "currency": "RUR", "dprice": 2581000}}, "aliases": ["Executive (2019-2020)", "g2932", "екзекьютив (2019-2020)", "экзекютив (2019-2020)", "8k 927", "экзекутив (2019-2020)
1:"price_segment": "MEDIUM"
1:"price_info": {"currency": "RUR", "EUR": 16648, "RUR": 1549000, "USD": 18688, "wit
…
```
Сейчас сравниваю `price_info` в старой фикстуре и в свежем ответе. В свежем уже видно, что поля `price` в нём нет.

**→ Grep** `{"pattern": "\"price_info\": \\{[^}]*\\}", "path": "C:\\Users\\filzu\\kiravto\\tests\\fixtures\\autoru\\listing_used_p1.json", "output_mode": "content", "-o": true, "head_limit": 3}`
**→ Grep** `{"pattern": "\"price_info\": \\{[^}]*\\}", "path": "C:\\Users\\filzu\\kiravto\\tests\\fixtures\\autoru\\listing_new_p1.json", "output_mode": "content", "-o": true, "head_limit": 2}`
**→ Grep** `{"pattern": "\"price_info\": \\{[^}]*\\}", "path": "C:\\Users\\filzu\\kiravto\\tests\\fixtures\\autoru\\card_1160027054-72d39620.json", "output_mode": "content", "-o": true, "head_limit": 2}`
```
1:"price_info": {"price": 2749000, "currency": "RUR", "EUR": 29546, "RUR": 2749000, "USD": 33165, "with_nds": false}
1:"price_info": {"price": 1549000, "currency": "RUR", "EUR": 16648, "RUR": 1549000, "USD": 18688, "with_nds": false}
1:"price_info": {"price": 3298000, "currency": "RUR", "EUR": 35446, "RUR": 3298000, "USD": 39789, "with_nds": false}

[Showing results with pagination = limit: 3]
```
**→ Grep** `{"pattern": "\"price_info\": \\{[^}]*\\}", "path": "C:\\Users\\filzu\\kiravto\\data\\fresh\\listing_used_p1.json", "output_mode": "count"}`
**→ Grep** `{"pattern": "\"price_info\": \\{[^}]*\"price\"", "path": "C:\\Users\\filzu\\kiravto\\data\\fresh\\listing_used_p1.json", "output_mode": "count"}`
**→ Grep** `{"pattern": "\"price_info\": \\{[^}]*\"price\"", "path": "C:\\Users\\filzu\\kiravto\\tests\\fixtures\\autoru", "output_mode": "count"}`
```
1:"price_info": {"price": 12490000, "currency": "RUR", "EUR": 134241, "RUR": 12490000, "USD": 150685, "with_nds": false}
1:"price_info": {"price": 11890000, "currency": "RUR", "EUR": 127792, "RUR": 11890000, "USD": 143447, "with_nds": false}

[Showing results with pagination = limit: 2]
```
```
No matches found
```
```
data\fresh\listing_used_p1.json:1

Found 1 total occurrence across 1 file.
```
```
No matches found

Found 0 total occurrences across 0 files.
```
```
tests\fixtures\autoru\listing_used_p1.json:1
tests\fixtures\autoru\listing_new_p1.json:1

Found 2 total occurrences across 2 files.
```
**→ Grep** `{"pattern": "\"price_info\": \\{[^}]*\\}", "path": "C:\\Users\\filzu\\kiravto\\tests\\fixtures\\autoru\\card_1160094460-61dcf15b.json", "output_mode": "content", "-o": true, "-n": false}`
**→ Grep** `{"pattern": "\"price_info\": \\{", "path": "C:\\Users\\filzu\\kiravto\\data\\fresh\\listing_used_p1.json", "output_mode": "content", "-o": true, "-n": false, "head_limit": 0}`
**→ Grep** `{"pattern": "\"price_info\": \\{\"currency\": \"RUR\", \"EUR\": \\d+, \"RUR\": \\d+, \"USD\": \\d+, \"with_nds\": (true|false), \"amount\": \\d+\\}", "path": "C:\\Users\\filzu\\kiravto\\data\\fresh\\listing_used_p1.json", "output_mode": "content", "-o": true, "-n": false, "head_limit": 0}`
```
No matches found
```
```
"price_info": {
"price_info": {
"price_info": {
"price_info": {
"price_info": {
"price_info": {
"price_info": {
"price_info": {
"price_info": {
"price_info": {
"price_info": {
"price_info": {
"price_info": {
"price_info": {
```
```
"price_info": {"currency": "RUR", "EUR": 29546, "RUR": 2749000, "USD": 33165, "with_nds": false, "amount": 2749000}
"price_info": {"currency": "RUR", "EUR": 16648, "RUR": 1549000, "USD": 18688, "with_nds": false, "amount": 1549000}
"price_info": {"currency": "RUR", "EUR": 35446, "RUR": 3298000, "USD": 39789, "with_nds": false, "amount": 3298000}
"price_info": {"currency": "RUR", "EUR": 28998, "RUR": 2698000, "USD": 32550, "with_nds": false, "amount": 2698000}
"price_info": {"currency": "RUR", "EUR": 10200, "RUR": 949000, "USD": 11449, "with_nds": false, "amount": 949000}
"price_info": {"currency": "RUR", "EUR": 24699, "RUR": 2298000, "USD": 27724, "with_nds": false, "amount": 2298000}
"price_info": {"currency": "RUR", "EUR": 10726, "RUR": 998000, "USD": 12040, "with_nds": false, "amount": 
…
```
**→ Read** `{"file_path": "C:\\Users\\filzu\\kiravto\\kiravto\\source\\parse.py", "offset": 164, "limit": 40}`
**→ Grep** `{"pattern": "\"price_info\"", "path": "C:\\Users\\filzu\\kiravto\\tests\\fixtures\\autoru\\card_1160094460-61dcf15b.json", "output_mode": "content", "-A": 8}`
**→ Grep** `{"pattern": "price_info|\"price\":", "path": "C:\\Users\\filzu\\kiravto\\tests", "glob": "*.py", "output_mode": "content"}`
```
164	
165	def parse_card(raw: dict) -> dict | None:
166	    """Карточка объявления. None — объявления больше нет (OFFER_NOT_FOUND)."""
167	    if raw.get("status") == "ERROR":
168	        if raw.get("error") == "OFFER_NOT_FOUND":
169	            return None
170	        if raw.get("action_kind") == "NEED_AUTH" or raw.get("error") in ("ACTION_REQUIRED", "CAPTCHA"):
171	            raise SourceBlocked(f"карточка: {raw.get('error')}")
172	        raise SourceFormatError(f"карточка вернула ошибку {raw.get('error')}")
173	    status = raw.get("status")
174	    if status and status != "ACTIVE":
175	        # Снятое объявление: выдача ещё показывает его, а карточка уже REMOVED и без цены
176	        # (поймано живым прогоном 21.10.2026 на #1160201304). Это не смена формата.
177	        return {"id"
…
```
```
1:[Omitted long matching line]
```
```
tests\test_collector.py:32:    victim["price_info"]["price"] -= 100_000
tests\test_collector.py:116:    del source.offers["used"][2]["price_info"]
tests\test_collector.py:120:    assert row["status"] == "failed" and "price_info" in row["note"]
tests\test_collector.py:211:    stale["price_info"]["price"] += 1000
tests\test_collector.py:219:    del source.offers["used"][2]["price_info"]
tests\test_collector.py:260:    card["price_info"]["price"] = new_price
tests\test_collector.py:269:    old_price = o["price_info"]["price"]
tests\test_collector.py:288:    _set_card_price(source, o, o["price_info"]["price"] - 300_000)   # выдача устарела уже при первом знакомстве
tests\test_collector.py:292:    assert [h[0] for h in hist] == [o["price_info"]["price"] - 300_000]
tests\test_telegram.py:154:   
…
```
### Жду от вас
- Разрешить мне запустить `python -m kiravto dump-fixtures --pages 1 --out recon/fresh`. Он снимет выдачу и 2 карточки, а карточки нужны для проверки плана. Шаг 2 из скилла `source-format-change` — это живой запрос к Авто.ру, и мои скриптовые команды сегодня не прошли без вашего подтверждения.
- Решить, правим ли код и фикстуры. Сейчас по вашему запросу ничего не менял. Вот что я прочитал:
  - Сравнение делал поиском по JSON в `data/fresh/listing_used_p1.json` и в `tests/fixtures/autoru/*`.
  - Читал `kiravto/source/parse.py` и `tests/*.py`.
- Пока есть неподтверждённое (ниже), `python -m kiravto status` и записи прогонов я тоже не смотрел.

### Сделано
- Нашёл причину падения. Авто.ру переименовал в `price_info` поле с ценой: было `price_info.price`, стало `price_info.amount`. Остальные поля блока на месте.
  - Раньше (`tests/fixtures/autoru/listing_used_p1.json`, `listing_new_p1.json`): `{"price": 2749000, "currency": "RUR", "EUR": …, "RUR": 2749000, "USD": …, "with_nds": false}`.
  - Сейчас (`data/fresh/listing_used_p1.json`): `{"currency": "RUR", "EUR": …, "RUR": 2749000, "USD": …, "with_nds": false, "amount": 2749000}`.
- Это не единичный случай. Во всех 14 `price_info` свежей выдачи ключа `price` нет, а у всех 14 есть `amount`. В `amount` рубли: он равен `RUR` и старому `price` на тех же ценах.
- Сообщение об ошибке точное: `_req(raw, "price_info.price")` стоит в `kiravto/source/parse.py:94`, и `_req` оборвался на `price`.
- Остальной конверт выдачи парсер пропустил, иначе упал бы раньше. Но это вывод по порядку проверок в `parse_offer`, а не отдельная сверка всех полей.
- `price_stats`, `price_history`, `price_segment` в свежем ответе есть. Это другие поля, к ошибке они не относятся.

### Нашёл
**План правки (когда разрешите):**
1. **Снять свежие карточки.** Запустить `dump-fixtures`, как описано выше. Нужно проверить, переименовалась ли цена и в `getOfferCard`. Парсер у выдачи и карточки общий (`parse_offer`), так что скорее всего да, но *не проверено*: в `data/fresh/` лежит только выдача. В старой карточке `card_1160094460-...json` строки `"price_info": {…}` мой поиск не нашёл, там могла быть другая форма. Структуру карточки надо смотреть глазами.
2. **Поправить `kiravto/source/parse.py:94`** на `price = _req(raw, "price_info.amount")`.
   - Обязательное поле остаётся обязательным (`_req`). Не заменять на `.get()` и не ставить запасной `price` — это молчаливая подмена, как в запретах скилла.
   - Если карточки остались на старом `price`, пока выдача уже на `amount`, нужна явная ветка для каждой формы. Сначала проверить пункт 1.
   - Ничего другого в `parse.py` менять не нужно: `price_discount` и `listing_signature` берут значение из `offer["price"]`.
3. **Фикстуры.**
   - Заменить `tests/fixtures/autoru/listing_used_p1.json` и `listing_new_p1.json` реальными свежими ответами. Для new выдачу тоже надо снять заново: проверял я только used.
   - Карточки тоже заменить реальными ответами. Руками переименовывать ключ в старых нельзя, это «подгонка под код».
   - В `card_removed_*` цены и так нет, там ветка `inactive`.
4. **Тесты.** Самая трудоёмкая часть — они тянут `["price_info"]["price"]` из фикстур:
   - `tests/test_collector.py:32,116,120,211,219,260,269,288,292`
   - `tests/test_api.py:27,143,159,160`
   - `tests/test_telegram.py:154,155`
   - `tests/test_report.py:32,43,44`
   - `tests/test_service.py:21`
   - `tests/test_mcp.py:22`

   После замены фикстур все эти обращения нужно перевести на `["price_info"]["amount"]`. Нужен новый тест в `tests/test_parse.py`: цена читается из `amount`, а при отсутствии `amount` бросается `SourceFormatError`. Тест на падение в `test_collector.py:116-120` проверяет только слово `price_info`, и он останется верным.
5. **Проверка:**
   - `.venv/Scripts/python -m pytest` в 1 поток, до зелёного.
   - Живой прогон `python -m kiravto run-once`. Цифры в отчёт: собрано / заявлено источником, запросов, ошибок, новых, изменений, снято.
   - `python -m kiravto sample 20` и субагент `verifier` на 5–10 машинах. Главное — цена на странице против `price` в базе.

**Что проверить кроме цены:**
- Совпадает ли `amount` с ценой на сайте. Сейчас вывод основан только на равенстве `amount` и `RUR`. С сайтом не сверял, это *не проверено*.
- Появилось ли что-то ещё. У `price_info` по словарю один новый ключ. По остальным полям `offers[0]` старой и новой выдачи я ключи не сравнивал, это *не проверено*. Сравнить надо, чтобы следующий прогон не упал на втором переименовании. Без команд для этого нужно ваше разрешение.
- Сколько в свежей выдаче объявлений. В ней нашлось 14 `price_info`, а в базе 352 активных и один полный прогон собирал 453. Возможно, это одна страница по 50 и часть её ответа обрезана, *не проверено*. На размер фикстуры и тест на полноту это влияет.

**Что в базе (по хуку сессии, не по `status`):** #13 в 10:34 был `failed` («процесс остановлен»), а #14 в 10:36 и #12 в 10:26 — `complete`, по 453 машины. Это записи, которые мне показал хук при старте. Отчёт о ночном прогоне со сменой формата я не видел: ни его номер, ни время, ни число `failed`-прогонов подряд я не проверял. Эти цифры нужны для отчёта по скиллу, их надо взять из `python -m kiravto status`.

**Риск:** пока парсер не поправлен, каждый ночной прогон будет падать с `failed`. Машины по неполному прогону не снимаются, так что в базе данные просто устареют, а не испортятся. Посты и цены в MCP верны на момент последнего полного прогона — об этом надо сказать тем, кто на них смотрит.

## Итог агента

### Жду от вас
- Разрешить мне запустить `python -m kiravto dump-fixtures --pages 1 --out recon/fresh`. Он снимет выдачу и 2 карточки, а карточки нужны для проверки плана. Шаг 2 из скилла `source-format-change` — это живой запрос к Авто.ру, и мои скриптовые команды сегодня не прошли без вашего подтверждения.
- Решить, правим ли код и фикстуры. Сейчас по вашему запросу ничего не менял. Вот что я прочитал:
  - Сравнение делал поиском по JSON в `data/fresh/listing_used_p1.json` и в `tests/fixtures/autoru/*`.
  - Читал `kiravto/source/parse.py` и `tests/*.py`.
- Пока есть неподтверждённое (ниже), `python -m kiravto status` и записи прогонов я тоже не смотрел.

### Сделано
- Нашёл причину падения. Авто.ру переименовал в `price_info` поле с ценой: было `price_info.price`, стало `price_info.amount`. Остальные поля блока на месте.
  - Раньше (`tests/fixtures/autoru/listing_used_p1.json`, `listing_new_p1.json`): `{"price": 2749000, "currency": "RUR", "EUR": …, "RUR": 2749000, "USD": …, "with_nds": false}`.
  - Сейчас (`data/fresh/listing_used_p1.json`): `{"currency": "RUR", "EUR": …, "RUR": 2749000, "USD": …, "with_nds": false, "amount": 2749000}`.
- Это не единичный случай. Во всех 14 `price_info` свежей выдачи ключа `price` нет, а у всех 14 есть `amount`. В `amount` рубли: он равен `RUR` и старому `price` на тех же ценах.
- Сообщение об ошибке точное: `_req(raw, "price_info.price")` стоит в `kiravto/source/parse.py:94`, и `_req` оборвался на `price`.
- Остальной конверт выдачи парсер пропустил, иначе упал бы раньше. Но это вывод по порядку проверок в `parse_offer`, а не отдельная сверка всех полей.
- `price_stats`, `price_history`, `price_segment` в свежем ответе есть. Это другие поля, к ошибке они не относятся.

### Нашёл
**План правки (когда разрешите):**
1. **Снять свежие карточки.** Запустить `dump-fixtures`, как описано выше. Нужно проверить, переименовалась ли цена и в `getOfferCard`. Парсер у выдачи и карточки общий (`parse_offer`), так что скорее всего да, но *не проверено*: в `data/fresh/` лежит только выдача. В старой карточке `card_1160094460-...json` строки `"price_info": {…}` мой поиск не нашёл, там могла быть другая форма. Структуру карточки надо смотреть глазами.
2. **Поправить `kiravto/source/parse.py:94`** на `price = _req(raw, "price_info.amount")`.
   - Обязательное поле остаётся обязательным (`_req`). Не заменять на `.get()` и не ставить запасной `price` — это молчаливая подмена, как в запретах скилла.
   - Если карточки остались на старом `price`, пока выдача уже на `amount`, нужна явная ветка для каждой формы. Сначала проверить пункт 1.
   - Ничего другого в `parse.py` менять не нужно: `price_discount` и `listing_signature` берут значение из `offer["price"]`.
3. **Фикстуры.**
   - Заменить `tests/fixtures/autoru/listing_used_p1.json` и `listing_new_p1.json` реальными свежими ответами. Для new выдачу тоже надо снять заново: проверял я только used.
   - Карточки тоже заменить реальными ответами. Руками переименовывать ключ в старых нельзя, это «подгонка под код».
   - В `card_removed_*` цены и так нет, там ветка `inactive`.
4. **Тесты.** Самая трудоёмкая часть — они тянут `["price_info"]["price"]` из фикстур:
   - `tests/test_collector.py:32,116,120,211,219,260,269,288,292`
   - `tests/test_api.py:27,143,159,160`
   - `tests/test_telegram.py:154,155`
   - `tests/test_report.py:32,43,44`
   - `tests/test_service.py:21`
   - `tests/test_mcp.py:22`

   После замены фикстур все эти обращения нужно перевести на `["price_info"]["amount"]`. Нужен новый тест в `tests/test_parse.py`: цена читается из `amount`, а при отсутствии `amount` бросается `SourceFormatError`. Тест на падение в `test_collector.py:116-120` проверяет только слово `price_info`, и он останется верным.
5. **Проверка:**
   - `.venv/Scripts/python -m pytest` в 1 поток, до зелёного.
   - Живой прогон `python -m kiravto run-once`. Цифры в отчёт: собрано / заявлено источником, запросов, ошибок, новых, изменений, снято.
   - `python -m kiravto sample 20` и субагент `verifier` на 5–10 машинах. Главное — цена на странице против `price` в базе.

**Что проверить кроме цены:**
- Совпадает ли `amount` с ценой на сайте. Сейчас вывод основан только на равенстве `amount` и `RUR`. С сайтом не сверял, это *не проверено*.
- Появилось ли что-то ещё. У `price_info` по словарю один новый ключ. По остальным полям `offers[0]` старой и новой выдачи я ключи не сравнивал, это *не проверено*. Сравнить надо, чтобы следующий прогон не упал на втором переименовании. Без команд для этого нужно ваше разрешение.
- Сколько в свежей выдаче объявлений. В ней нашлось 14 `price_info`, а в базе 352 активных и один полный прогон собирал 453. Возможно, это одна страница по 50 и часть её ответа обрезана, *не проверено*. На размер фикстуры и тест на полноту это влияет.

**Что в базе (по хуку сессии, не по `status`):** #13 в 10:34 был `failed` («процесс остановлен»), а #14 в 10:36 и #12 в 10:26 — `complete`, по 453 машины. Это записи, которые мне показал хук при старте. Отчёт о ночном прогоне со сменой формата я не видел: ни его номер, ни время, ни число `failed`-прогонов подряд я не проверял. Эти цифры нужны для отчёта по скиллу, их надо взять из `python -m kiravto status`.

**Риск:** пока парсер не поправлен, каждый ночной прогон будет падать с `failed`. Машины по неполному прогону не снимаются, так что в базе данные просто устареют, а не испортятся. Посты и цены в MCP верны на момент последнего полного прогона — об этом надо сказать тем, кто на них смотрит.

Стоимость: $0.307, ходов: 23
