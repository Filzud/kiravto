"""Настройки сервиса. Всё берётся из окружения и файла .env (секреты только там)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> dict[str, str]:
    """Простой разбор .env без сторонних библиотек: KEY=VALUE, # — комментарий."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _get(env: dict[str, str], key: str, default: str = "") -> str:
    # переменные окружения важнее файла — так удобно в docker compose и тестах
    return os.environ.get(key, env.get(key, default))


@dataclass
class Settings:
    dealer_code: str = "rolf_yasenevo_moskva"
    dealer_id: str = "20866489"
    db_path: Path = ROOT / "data" / "kiravto.db"
    report_path: Path = ROOT / "data" / "report.html"

    run_interval_min: int = 45
    completeness_threshold: float = 0.9
    sold_after_days: int = 7
    max_cards_per_run: int = 120
    refresh_cards_per_run: int = 60   # сколько уже известных карточек освежать по кругу за прогон
    max_verify_per_run: int = 40
    request_pause_s: tuple[float, float] = (2.0, 4.5)
    headless: bool = True

    drop_min_pct: float = 3.0
    daily_post_time: str = "10:00"
    timezone: str = "Europe/Moscow"

    tg_token: str = ""
    tg_api_base: str = "https://api.telegram.org"
    tg_proxy: str = ""
    tg_admin_chat_id: str = ""
    tg_test_channel_id: str = ""
    tg_prod_channel_id: str = ""
    # «prod» включает только человек, правкой .env руками. Бот, команды и агент этого не умеют.
    tg_publish_target: str = "test"

    api_token: str = ""
    api_port: int = 8080

    llm_provider: str = ""  # openrouter | openai | anthropic; пусто = функция выключена
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_daily_budget_usd: float = 0.20
    llm_price_in_per_m: float = 0.15
    llm_price_out_per_m: float = 0.60

    extra: dict[str, str] = field(default_factory=dict)

    @property
    def publish_channel_id(self) -> str:
        if self.tg_publish_target == "prod" and self.tg_prod_channel_id:
            return self.tg_prod_channel_id
        return self.tg_test_channel_id

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_provider and self.llm_api_key and self.llm_model)


def load_settings(env_path: Path | None = None) -> Settings:
    env = load_env_file(env_path or ROOT / ".env")
    g = lambda k, d="": _get(env, k, d)  # noqa: E731
    s = Settings(
        dealer_code=g("DEALER_CODE", "rolf_yasenevo_moskva"),
        dealer_id=g("DEALER_ID", "20866489"),
        db_path=Path(g("DB_PATH", str(ROOT / "data" / "kiravto.db"))),
        report_path=Path(g("REPORT_PATH", str(ROOT / "data" / "report.html"))),
        run_interval_min=int(g("RUN_INTERVAL_MIN", "45")),
        completeness_threshold=float(g("COMPLETENESS_THRESHOLD", "0.9")),
        sold_after_days=int(g("SOLD_AFTER_DAYS", "7")),
        max_cards_per_run=int(g("MAX_CARDS_PER_RUN", "120")),
        refresh_cards_per_run=int(g("REFRESH_CARDS_PER_RUN", "60")),
        max_verify_per_run=int(g("MAX_VERIFY_PER_RUN", "40")),
        headless=g("HEADLESS", "1") != "0",
        drop_min_pct=float(g("DROP_MIN_PCT", "3")),
        daily_post_time=g("DAILY_POST_TIME", "10:00"),
        timezone=g("TZ_NAME", "Europe/Moscow"),
        tg_token=g("TG_BOT_TOKEN"),
        tg_api_base=g("TG_API_BASE", "https://api.telegram.org"),
        tg_proxy=g("TG_PROXY"),
        tg_admin_chat_id=g("TG_ADMIN_CHAT_ID"),
        tg_test_channel_id=g("TG_TEST_CHANNEL_ID"),
        tg_prod_channel_id=g("TG_PROD_CHANNEL_ID"),
        tg_publish_target=g("TG_PUBLISH_TARGET", "test").lower(),
        api_token=g("API_TOKEN"),
        api_port=int(g("API_PORT", "8080")),
        llm_provider=g("LLM_PROVIDER").lower(),
        llm_api_key=g("LLM_API_KEY"),
        llm_model=g("LLM_MODEL"),
        llm_base_url=g("LLM_BASE_URL"),
        llm_daily_budget_usd=float(g("LLM_DAILY_BUDGET_USD", "0.20")),
        llm_price_in_per_m=float(g("LLM_PRICE_IN_PER_M", "0.15")),
        llm_price_out_per_m=float(g("LLM_PRICE_OUT_PER_M", "0.60")),
    )
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    return s
