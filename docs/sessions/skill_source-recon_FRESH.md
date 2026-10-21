# Скилл source-recon сработал сам: разведка запасного салона FRESH

Экспорт stream-json сессии `claude -p` (модель sonnet) в корне проекта, 21.10.2026.
Список MCP-серверов машины и системный промпт убраны; токенов в сессиях нет (проверено регэкспом).

**→ Skill** `{"skill": "source-recon"}`
```
Launching skill: source-recon
```
**→ ToolSearch** `{"query": "select:mcp__plugin_playwright_playwright__browser_navigate,mcp__plugin_playwright_playwright__browser_evaluate,mcp__plugin_playwright_playwright__browser_network_requests", "max_results": 5}`
**→ Grep** `{"pattern": "dealer_id|dealer_code|DEALER|has_image|damage_group|customs_state_group|salon", "path": "C:\\Users\\filzu\\kiravto\\kiravto", "output_mode": "content", "head_limit": 80}`
```

```
```
kiravto\config.py:32:    dealer_code: str = "rolf_yasenevo_moskva"
kiravto\config.py:33:    dealer_id: str = "20866489"
kiravto\config.py:87:        dealer_code=g("DEALER_CODE", "rolf_yasenevo_moskva"),
kiravto\config.py:88:        dealer_id=g("DEALER_ID", "20866489"),
kiravto\collector.py:85:                      if not o.get("dealer_code") or o["dealer_code"] == self.s.dealer_code}
kiravto\collector.py:367:                    (not card.get("dealer_code") or card["dealer_code"] == self.s.dealer_code):
kiravto\__main__.py:152:    src = AutoruBrowserSource(s.dealer_code, s.dealer_id, headless=s.headless)
kiravto\source\parse.py:151:        "dealer_code": _opt(raw, "salon.code"),
kiravto\service.py:76:    src = source or AutoruBrowserSource(settings.dealer_code, settings.dealer_id, headless=
…
```
**→ Read** `{"file_path": "C:\\Users\\filzu\\kiravto\\kiravto\\source\\autoru.py", "offset": 80, "limit": 150}`
```
80	        if text.lstrip().startswith("<"):
81	            raise TransientError(f"{what}: вместо JSON пришёл HTML (HTTP {status})")
82	        raise SourceFormatError(f"{what}: ответ не JSON: {text[:120]!r}")
83	
84	
85	class AutoruBrowserSource:
86	    def __init__(self, dealer_code: str, dealer_id: str, headless: bool = True,
87	                 pause: tuple[float, float] = (2.0, 4.5), retries: int = 3):
88	        self.dealer_code = dealer_code
89	        self.dealer_id = dealer_id
90	        self.pause = pause
91	        self.retries = retries
92	        self.requests = 0
93	        self.errors = 0
94	        self._headless = headless
95	        self._pw = None
96	        self._browser = None
97	        self._page = None
98	
99	    # --- жизненный цикл ---
100	    def _ensure_page(sel
…
```
**→ Grep** `{"pattern": "section|SECTIONS|total_count|pagination", "path": "C:\\Users\\filzu\\kiravto\\kiravto", "output_mode": "content", "head_limit": 40}`
**→ mcp__plugin_playwright_playwright__browser_navigate** `{"url": "https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/"}`
```
kiravto\__main__.py:154:        for section in ("new", "used"):
kiravto\__main__.py:156:                data = src.listing(section, page)
kiravto\__main__.py:157:                (out / f"listing_{section}_p{page}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
kiravto\source\parse.py:68:    _req(data, "pagination.total_offers_count")
kiravto\source\parse.py:69:    _req(data, "pagination.total_page_count")
kiravto\source\parse.py:105:    section = _req(raw, "section")
kiravto\source\parse.py:111:        "section": section,
kiravto\source\parse.py:139:        "owners": docs.get("owners_number") if section == "used" else None,
kiravto\source\parse.py:140:        "pts": nz.PTS.get(docs.get("pts"), docs.get("pts")) if section == "used" else None,
kiravto\source\autoru.py:49:   
…
```
```
### Ran Playwright code
```js
await page.goto('https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/');
```
### Page
- Page URL: https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/
- Page Title: FRESH Москва — дилер авто с пробегом в Москве
- Console: 1 errors, 1 warnings
### Snapshot
- [Snapshot](.playwright-mcp\page-2026-10-21T08-19-16-472Z.yml)
### Events
- New console entries: .playwright-mcp\console-2026-10-21T08-19-12-753Z.log#L1-L2
```
Запрос 1 сделан. Смотрю сетевые запросы и встроенное состояние (новых запросов к сайту это не создаёт).

**→ mcp__plugin_playwright_playwright__browser_network_requests** `{"static": false, "filter": "ajax|api|listing"}`
```
### Result
56. [POST] https://auto.ru/-/ajax/desktop-search/sendUserChoice/ => [200] 
57. [POST] https://auto.ru/-/ajax/desktop-search/sendUserChoice/ => [200] 
85. [POST] https://api-common.tinkoff.ru/front-logs/v2/logs => [200] 
86. [POST] https://id.tbank.ru/preidentity/api/v1/user-info => [404] 
87. [GET] https://surveys.yandex.ru/api/v0/feedback/survey/suitable?owner_id=9575122d-c45e-4bed-a524-6314e8b85f77&source_url=https%3A%2F%2Fauto.ru%2Fdiler%2Fcars%2Fused%2Ffresh_x_syndicate_moskva%2F&lang=ru&uid=5990376191792570754 => [200] OK
90. [POST] https://api-common.tinkoff.ru/front-logs/v2/logs => [207] 
94. [POST] https://id.tbank.ru/preidentity/api/v1/web-info => [204] 
97. [POST] https://web2web.tbank-online.com/web-fingerprint/api/v1/external/fingerprint => [200] 
103. [POST] https:/
…
```
**→ mcp__plugin_playwright_playwright__browser_evaluate** `{"function": "() => { const h = document.documentElement.innerHTML; const ids = [...new Set([...h.matchAll(/dealer_id[\"':= ]+(\\d{5,})/g)].map(m=>m[1]))]; const codes=[...new Set([...h.matchAll(/fresh_x_syndicate[a-z_]*/g)].map(m=>m[0]))]; const cnt=[...h.matchAll(/(\\d+)\\s*(?:предложени|автомобил|объявлени)[а-я]*/g)].slice(0,8).map(m=>m[0]); const tabs=[...document.querySelectorAll('a[href*=\"/`
```
### Result
{
  "ids": [],
  "codes": [
    "fresh_x_syndicate_moskva"
  ],
  "cnt": [
    "676 автомобилей"
  ],
  "tabs": [
    "Обзор -> https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/?tab=profile",
    "Объявления676 -> https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/",
    "Бортжурнал -> https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/?tab=logbook",
    "Audi -> https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/audi/",
    "BMW -> https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/bmw/",
    "Geely -> https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/geely/",
    "Hyundai -> https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/hyundai/",
    "Kia -> https://auto.ru/diler/cars/used/fresh_x_syndicate_moskva/kia/",
    "Mazda -> https://
…
```
**→ mcp__plugin_playwright_playwright__browser_evaluate** `{"function": "() => { const h = document.documentElement.innerHTML; const out = {}; for (const k of ['dealerId','dealer_id','\"dealer\"','salon','clientId','client_id']) { const i = h.indexOf(k); out[k] = i<0 ? null : h.slice(Math.max(0,i-60), i+160); } out.scripts=[...document.querySelectorAll('script[type=\"application/json\"]')].map(s=>s.id+':'+s.textContent.length); return out; }"}`
```
### Result
{
  "dealerId": null,
  "dealer_id": null,
  "\"dealer\"": null,
  "salon": "80_mobile_card_group_dealers_block\":true,\"AUTORUFRONT-31528_salon-phone\":true,\"AUTORUFRONT-31685_used_car_card_new_layout\":true,\"TOGGLE_AUTORUFRONT-33730_enable_on_order_skip_vin\":true,\"AUTORUFRONT-33826_on_order_snippet",
  "clientId": "822t4ipetevvs05btqpulh7vs.9c9d862692171e98fa7b05b657f7ea44\",clientId:\"tid_autoru-pixel\"}).addPixel()}t.querySelector('script[src=\"https://sso-forms-prod.t-static.ru/tid/fp-pixel.js\"]').addEventListener(\"load\",e),i.TidFpP",
  "client_id": null,
  "scripts": [
    ":0",
    "app-config:874",
    "app-config-client:7722",
    "__LOADABLE_REQUIRED_CHUNKS__:25",
    "__LOADABLE_REQUIRED_CHUNKS___ext:41"
  ]
}
### Ran Playwright code
```js
await page.evaluate('
…
```
**→ Read** `{"file_path": "C:\\Users\\filzu\\kiravto\\kiravto\\source\\autoru.py", "limit": 80}`
```
1	"""Доступ к Авто.ру через тот же JSON, которым пользуется фронтенд сайта.
2	
3	Почему браузер, а не httpx: простой HTTP-клиент Авто.ру сразу отправляет на вход
4	(`NEED_AUTH`), а обычный браузер с собственной новой сессией получает те же данные,
5	что видит посетитель. Чужих cookie, аккаунтов и сервисов разгадывания капчи нет.
6	Страница открывается один раз за прогон, дальше — fetch() к /-/ajax/ из контекста страницы.
7	"""
8	from __future__ import annotations
9	
10	import json
11	import logging
12	import random
13	import time
14	from typing import Protocol
15	
16	from .parse import SourceBlocked, SourceFormatError, check_listing_response
17	
18	log = logging.getLogger("kiravto.source")
19	
20	LISTING_EP = "/-/ajax/desktop-search/listing/"
21	CARD_EP = "/-/ajax/desktop-search/getOfferCa
…
```
**→ mcp__plugin_playwright_playwright__browser_evaluate** `{"function": "async () => {\n  const csrf = (document.cookie.match(/_csrf_token=([^;]+)/) || [])[1] || '';\n  const r = await fetch('/-/ajax/desktop-search/listing/', {method:'POST', credentials:'include', headers:{'content-type':'application/json','x-csrf-token':csrf}, body: JSON.stringify({category:'cars', section:'used', dealer_code:'fresh_x_syndicate_moskva', page:1, page_size:50, geo_id:[]})}`
```
### Result
{
  "status": 200,
  "keys": [
    "status",
    "original_search_parameters",
    "search_parameters",
    "filter_search_parameters",
    "hiddenOffers",
    "offers",
    "pagination",
    "price_range",
    "response_flags",
    "grouping",
    "request_id",
    "search_request_id",
    "query",
    "paramsDescription",
    "productionYears",
    "relatedDealers",
    "output_type",
    "crossLinks",
    "trucksCount",
    "new4newCount",
    "shouldShowListingBestPriceBlock"
  ],
  "pagination": {
    "total_page_count": 99,
    "total_offers_count": 506600,
    "page": 1,
    "page_size": 50,
    "from": 1,
    "to": 50,
    "current": 1
  },
  "n": 50,
  "salon": null,
  "seller": {
    "keys": [
      "location",
      "name",
      "phones",
      "redirect_phones",
   
…
```
**→ mcp__plugin_playwright_playwright__browser_evaluate** `{"function": "() => { const h = document.documentElement.outerHTML; const out = {}; for (const re of [/dealer[_-]?id[^a-z0-9]{1,6}\\d+/gi, /client[_-]?id[^a-z0-9]{1,6}\\d{5,}/gi, /\"id\":\\s*\"?\\d{7,9}\"?[^}]{0,80}(?:FRESH|fresh)/g, /(?:FRESH|fresh)[^}]{0,120}\"id\":\\s*\"?\\d{7,9}/g]) out[re.source] = [...h.matchAll(re)].slice(0,5).map(m=>m[0].slice(0,160)); out.stateKeys = Object.keys(window).f`
```
### Result
{
  "dealer[_-]?id[^a-z0-9]{1,6}\\d+": [],
  "client[_-]?id[^a-z0-9]{1,6}\\d{5,}": [],
  "\"id\":\\s*\"?\\d{7,9}\"?[^}]{0,80}(?:FRESH|fresh)": [],
  "(?:FRESH|fresh)[^}]{0,120}\"id\":\\s*\"?\\d{7,9}": [],
  "stateKeys": [
    "oncontentvisibilityautostatechange",
    "oncontextrestored",
    "onpopstate",
    "cookieStore"
  ],
  "cfg": "{\"appId\":\"af-desktop-search\",\"debug\":false,\"env\":\"production\",\"envDev\":false,\"envProd\":true,\"envTesting\":false,\"metrikaId\":22753222,\"version\":\"1573.1.d447e32d\",\"authDomain\":\"auth.auto.ru\",\"baseDomain\":\"auto.ru\",\"cookiesDomain\":\".auto.ru\",\"ssoDomain\":\"sso.auto.ru\",\"ssoPassportDomain\":\"sso.passport.ya",
  "links": [
    "https://auto.ru/dealer-net/fresh_auto/",
    "https://play.google.com/store/apps/detail
…
```
**→ mcp__plugin_playwright_playwright__browser_evaluate** `{"function": "async () => {\n  const links = [...new Set([...document.querySelectorAll('a[href*=\"/cars/used/sale/\"]')].map(a=>a.getAttribute('href')))];\n  const m = links[0] && links[0].match(/\\/(\\d+-[0-9a-f]+)\\/?/);\n  if (!m) return {links: links.length, sample: links.slice(0,3)};\n  const csrf = (document.cookie.match(/_csrf_token=([^;]+)/) || [])[1] || '';\n  const r = await fetch('/-/aj`
```
### Result
{
  "nLinks": 37,
  "link": "https://auto.ru/cars/used/sale/denza/x/1134171568-8a9a01f0/",
  "status": 200,
  "topKeys": [
    "availability",
    "color_hex",
    "delivery_info",
    "discount_options",
    "documents",
    "is_favorite",
    "old_category_id",
    "owner_expenses",
    "score",
    "status",
    "tags",
    "predicted_price_ranges"
  ],
  "salon": {
    "actual_stock": true,
    "client_id": "59609",
    "code": "fresh_x_syndicate_moskva",
    "dealer_id": "23884202",
    "is_oficial": false,
    "logo_url": "//avatars.avto.ru/get-verba/1030388/2a0000019bbb8020ce3e84dc502e768464a9/dealer_logo",
    "loyalty_program": true,
    "offer_counters": {
      "cars_all": 679,
      "moto_all": 4,
      "trucks_all": 4
    },
    "name": "FRESH Москва",
    "phone_ca
…
```
**→ mcp__plugin_playwright_playwright__browser_evaluate** `{"function": "async () => {\n  const csrf = (document.cookie.match(/_csrf_token=([^;]+)/) || [])[1] || '';\n  const sleep = ms => new Promise(r => setTimeout(r, ms));\n  const base = {category:'cars', dealer_id:'23884202', dealer_code:'fresh_x_syndicate_moskva', page:1, page_size:50, geo_id:[]};\n  const unhide = {has_image:false, damage_group:'ANY', customs_state_group:'DOESNT_MATTER'};\n  const `
```
### Result
[
  {
    "label": "used default",
    "status": 200,
    "total": 676,
    "pages": 14,
    "n": 50,
    "codes": {
      "fresh_x_syndicate_moskva": 50
    },
    "hidden": 0
  },
  {
    "label": "used unhide",
    "status": 200,
    "total": 676,
    "pages": 14,
    "n": 50,
    "codes": {
      "fresh_x_syndicate_moskva": 50
    },
    "hidden": 0
  },
  {
    "label": "new default",
    "status": 200,
    "total": 0,
    "pages": 1,
    "n": 0,
    "codes": {},
    "hidden": 0
  },
  {
    "label": "new unhide",
    "status": 200,
    "total": 0,
    "pages": 1,
    "n": 0,
    "codes": {},
    "hidden": 0
  }
]
### Ran Playwright code
```js
await page.evaluate('async () => {\n  const csrf = (document.cookie.match(/_csrf_token=([^;]+)/) || [])[1] || \'\';\n  const sleep =
…
```
**→ mcp__plugin_playwright_playwright__browser_evaluate** `{"function": "async () => {\n  const csrf = (document.cookie.match(/_csrf_token=([^;]+)/) || [])[1] || '';\n  const r = await fetch('/-/ajax/desktop-search/listing/', {method:'POST', credentials:'include', headers:{'content-type':'application/json','x-csrf-token':csrf}, body: JSON.stringify({category:'cars', section:'all', dealer_id:'23884202', dealer_code:'fresh_x_syndicate_moskva', page:1, page_`
```
### Result
{
  "status": 200,
  "total": 676,
  "n": 50,
  "sec": {
    "used": 50
  }
}
### Ran Playwright code
```js
await page.evaluate('async () => {\n  const csrf = (document.cookie.match(/_csrf_token=([^;]+)/) || [])[1] || \'\';\n  const r = await fetch(\'/-/ajax/desktop-search/listing/\', {method:\'POST\', credentials:\'include\', headers:{\'content-type\':\'application/json\',\'x-csrf-token\':csrf}, body: JSON.stringify({category:\'cars\', section:\'all\', dealer_id:\'23884202\', dealer_code:\'fresh_x_syndicate_moskva\', page:1, page_size:50, geo_id:[], has_image:false, damage_group:\'ANY\', customs_state_group:\'DOESNT_MATTER\'})});\n  const j = await r.json(); const o = j.offers||[];\n  const sec = {}; o.forEach(x => sec[x.section] = (sec[x.section]||0)+1);\n  return {status:r.st
…
```
### Жду от вас
- Ничего. Чтобы переключиться, человек правит в `.env` две строки (я `.env` не читал и не менял): `DEALER_CODE=fresh_x_syndicate_moskva`, `DEALER_ID=23884202`. Перед боевым использованием нужен пробный прогон `run-once`, на другой базе или после очистки (см. «Нашёл»).

### Сделано
- Код, база и `.env` не тронуты. Запросов к сайту 8 из 15 (1 открытие страницы и 7 fetch из её контекста), между fetch паузы 3 с. Блокировок, капчи и ошибок не было.
- **dealer_id = 23884202.** Я взял его из `salon.dealer_id` в карточке машины FRESH (`getOfferCard`). Там же `salon.code = fresh_x_syndicate_moskva`, `client_id` 59609, `salon_id` 53144, название «FRESH Москва».
- **Откуда страница берёт данные:** список машин приходит уже готовым в HTML, отдельного запроса за выдачей при загрузке нет. Тот же JSON отдаёт `POST /-/ajax/desktop-search/listing/`, это тот эндпоинт, которым пользуется наш сборщик. Карточка идёт через `getOfferCard`, формат совпал с нашим.
- **Сколько машин:**

| Что проверено | Результат |
|---|---|
| `used` с фильтрами по умолчанию | 676 (14 страниц по 50) |
| `used` со снятыми фильтрами, как у нашего сборщика | 676 |
| `new` (с фильтрами и без) | 0 |
| `section=all` | 676, все `used` |
| Счётчик на вкладке «Объявления» | 676 |

  На первой странице `used` у всех 50 машин `salon.code` совпал с `fresh_x_syndicate_moskva`. `hiddenOffers` пуст.
- **Скрытых фильтрами нет:** при снятых `has_image`, `damage_group` и `customs_state_group` число осталось 676.

### Нашёл
- **Подходит ли сборщик: да, без правок кода, если поменять только `DEALER_CODE` и `DEALER_ID`.** Вывод по чтению кода и по живым ответам; сам `run-once` на FRESH я не запускал, не проверено.
  - Выдача, карточка, параметры запроса и клиентский фильтр `salon.code` (`collector.py:85`, `:367`) работают как для РОЛЬФа.
  - Раздел `new` пустой, сборщик это переживёт. Не проверено: не падает ли проверка «собрано = заявлено» на нулевой секции.
  - Не проверено: парсинг полей FRESH. Я смотрел только `salon`, `seller` и `section`. Полный разбор 676 машин нужно проверить живым прогоном и сверкой `verifier`.
- **Риск 1: `dealer_code` без `dealer_id` источник молча игнорирует.** Он отдал общую выдачу, 506 600 объявлений чужих продавцов, без ошибки. Если в `.env` ошибиться в ID, клиентский фильтр по `salon.code` спасёт данные, но прогон тратит запросы впустую и может выглядеть «заявлено 506600». Нужно задавать оба значения.
- **Риск 2: база общая, разделения по салонам нет.** Не проверено: я схему `db.py` не смотрел, но запрос в README и CLAUDE.md говорит о складе одного салона. Если в таблице нет поля салона, прогон по FRESH пометит все 353 активные машины РОЛЬФа «снятыми». Перед переключением нужна отдельная база (или проверка, что данные салонов не смешиваются).
- **Расхождение 679 и 676:** `salon.offer_counters.cars_all` в карточке показывает 679, листинг и вкладка дают 676. Объяснение не проверено (возможно, свежие объявления или кеш счётчика). Для сверки «собрано/заявлено» ориентир 676, он берётся из `pagination.total_offers_count`.
- **Масштаб:** 676 машин почти в 1,5 раза больше, чем у РОЛЬФа (453). Это 14 страниц выдачи плюс обход карточек, прогон и число запросов будут заметно больше, чем сейчас (≈266). Частоту поднимать нельзя, придётся смотреть, укладывается ли прогон в 30–60 минут.
- Фикстуры не сохранял (код и тесты менять нельзя). Если решите переключаться, нужны фикстуры FRESH для листинга и карточки.

## Итог агента

### Жду от вас
- Ничего. Чтобы переключиться, человек правит в `.env` две строки (я `.env` не читал и не менял): `DEALER_CODE=fresh_x_syndicate_moskva`, `DEALER_ID=23884202`. Перед боевым использованием нужен пробный прогон `run-once`, на другой базе или после очистки (см. «Нашёл»).

### Сделано
- Код, база и `.env` не тронуты. Запросов к сайту 8 из 15 (1 открытие страницы и 7 fetch из её контекста), между fetch паузы 3 с. Блокировок, капчи и ошибок не было.
- **dealer_id = 23884202.** Я взял его из `salon.dealer_id` в карточке машины FRESH (`getOfferCard`). Там же `salon.code = fresh_x_syndicate_moskva`, `client_id` 59609, `salon_id` 53144, название «FRESH Москва».
- **Откуда страница берёт данные:** список машин приходит уже готовым в HTML, отдельного запроса за выдачей при загрузке нет. Тот же JSON отдаёт `POST /-/ajax/desktop-search/listing/`, это тот эндпоинт, которым пользуется наш сборщик. Карточка идёт через `getOfferCard`, формат совпал с нашим.
- **Сколько машин:**

| Что проверено | Результат |
|---|---|
| `used` с фильтрами по умолчанию | 676 (14 страниц по 50) |
| `used` со снятыми фильтрами, как у нашего сборщика | 676 |
| `new` (с фильтрами и без) | 0 |
| `section=all` | 676, все `used` |
| Счётчик на вкладке «Объявления» | 676 |

  На первой странице `used` у всех 50 машин `salon.code` совпал с `fresh_x_syndicate_moskva`. `hiddenOffers` пуст.
- **Скрытых фильтрами нет:** при снятых `has_image`, `damage_group` и `customs_state_group` число осталось 676.

### Нашёл
- **Подходит ли сборщик: да, без правок кода, если поменять только `DEALER_CODE` и `DEALER_ID`.** Вывод по чтению кода и по живым ответам; сам `run-once` на FRESH я не запускал, не проверено.
  - Выдача, карточка, параметры запроса и клиентский фильтр `salon.code` (`collector.py:85`, `:367`) работают как для РОЛЬФа.
  - Раздел `new` пустой, сборщик это переживёт. Не проверено: не падает ли проверка «собрано = заявлено» на нулевой секции.
  - Не проверено: парсинг полей FRESH. Я смотрел только `salon`, `seller` и `section`. Полный разбор 676 машин нужно проверить живым прогоном и сверкой `verifier`.
- **Риск 1: `dealer_code` без `dealer_id` источник молча игнорирует.** Он отдал общую выдачу, 506 600 объявлений чужих продавцов, без ошибки. Если в `.env` ошибиться в ID, клиентский фильтр по `salon.code` спасёт данные, но прогон тратит запросы впустую и может выглядеть «заявлено 506600». Нужно задавать оба значения.
- **Риск 2: база общая, разделения по салонам нет.** Не проверено: я схему `db.py` не смотрел, но запрос в README и CLAUDE.md говорит о складе одного салона. Если в таблице нет поля салона, прогон по FRESH пометит все 353 активные машины РОЛЬФа «снятыми». Перед переключением нужна отдельная база (или проверка, что данные салонов не смешиваются).
- **Расхождение 679 и 676:** `salon.offer_counters.cars_all` в карточке показывает 679, листинг и вкладка дают 676. Объяснение не проверено (возможно, свежие объявления или кеш счётчика). Для сверки «собрано/заявлено» ориентир 676, он берётся из `pagination.total_offers_count`.
- **Масштаб:** 676 машин почти в 1,5 раза больше, чем у РОЛЬФа (453). Это 14 страниц выдачи плюс обход карточек, прогон и число запросов будут заметно больше, чем сейчас (≈266). Частоту поднимать нельзя, придётся смотреть, укладывается ли прогон в 30–60 минут.
- Фикстуры не сохранял (код и тесты менять нельзя). Если решите переключаться, нужны фикстуры FRESH для листинга и карточки.

Стоимость: $0.345, ходов: 17
