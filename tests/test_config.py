"""Настройки из .env: пустые числа не роняют сервис (живой случай 22.10.2026)."""
from kiravto.config import ROOT, load_env_file, load_settings


def test_empty_numbers_mean_defaults(tmp_path, monkeypatch):
    # `secrets` записал новые настройки пустыми — сервис падал на int('') и перезапускался по кругу
    env = tmp_path / ".env"
    env.write_text("MARKET_GROUPS_PER_RUN=\nMARKET_TTL_HOURS=\nRUN_INTERVAL_MIN=\nDB_PATH=" + str(tmp_path / "x.db")
                   + "\nSTT_MODEL=\nMARKET_BELOW_PCT=\n", encoding="utf-8")
    for k in ("MARKET_GROUPS_PER_RUN", "MARKET_TTL_HOURS", "RUN_INTERVAL_MIN", "DB_PATH", "STT_MODEL",
              "MARKET_BELOW_PCT"):
        monkeypatch.delenv(k, raising=False)
    s = load_settings(env)
    assert s.market_groups_per_run == 12 and s.market_ttl_hours == 48 and s.run_interval_min == 45
    assert s.stt_model == ""             # строковая настройка: пусто — это «выключено», а не значение по умолчанию
    assert s.market_below_pct is None


def test_example_has_value_for_every_number():
    """Шаблон .env.example — источник значений, которые `secrets` дописывает в .env."""
    ex = load_env_file(ROOT / ".env.example")
    assert ex["MARKET_GROUPS_PER_RUN"] == "12" and ex["ROLF_INTERVAL_HOURS"] == "6"
