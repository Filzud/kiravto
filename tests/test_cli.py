"""Команды для человека: скрытый ввод секретов и проверка настройки Telegram."""
import json

import httpx

from kiravto import __main__ as cli
from kiravto.config import load_env_file
from kiravto.telegram_bot import BotAPI

# токен правильной формы собираем на лету, чтобы в файле не было строки, похожей на настоящий токен
SECRET = "1" * 10 + ":" + "Ab_-" * 8 + "xyz"


def test_secrets_hidden_input_writes_env(tmp_path, monkeypatch, capsys):
    root = tmp_path
    (root / ".env.example").write_text("TG_BOT_TOKEN=\nAPI_TOKEN=\nLLM_API_KEY=\nTG_ADMIN_CHAT_ID=\n"
                                       "TG_PUBLISH_TARGET=test\n", encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", root)
    secrets_in = iter([SECRET, "", ""])                    # токен бота, API (сгенерировать), LLM (пропустить)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(secrets_in))
    plain_in = iter(["-100777", "", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(plain_in))
    cli.cmd_secrets(None)
    env = load_env_file(root / ".env")
    assert env["TG_BOT_TOKEN"] == SECRET
    assert len(env["API_TOKEN"]) >= 32                    # сгенерирован
    assert env["TG_ADMIN_CHAT_ID"] == "-100777"
    assert env["TG_PUBLISH_TARGET"] == "test"              # боевой канал так не включить
    assert SECRET not in capsys.readouterr().out           # секрет не печатается


def test_tg_check_reports_chats_and_rights(settings):
    settings.tg_admin_chat_id, settings.tg_test_channel_id = "-1001", "-1002"
    answers = {
        "getMe": {"id": 7, "username": "kiravto_test_bot"},
        "getUpdates": [{"update_id": 1, "message": {"chat": {"id": -1001, "type": "supergroup", "title": "Админка"}}},
                       {"update_id": 2, "channel_post": {"chat": {"id": -1002, "type": "channel", "title": "Тест"}}}],
    }
    calls = []

    def handler(req: httpx.Request):
        method = req.url.path.rsplit("/", 1)[-1]
        body = json.loads(req.content or b"{}")
        calls.append(str(req.url))
        if method == "getChatMember":
            status = "administrator" if body["chat_id"] == "-1002" else "member"
            return httpx.Response(200, json={"ok": True, "result": {"status": status}})
        return httpx.Response(200, json={"ok": True, "result": answers[method]})

    api = BotAPI("https://tg.example", SECRET, client=httpx.Client(transport=httpx.MockTransport(handler)))
    lines = cli.tg_check(settings, api)
    text = "\n".join(lines)
    assert "@kiravto_test_bot" in text and "-1001  supergroup: Админка" in text
    assert "✓ тестовый канал -1002: бот — administrator" in text
    assert "– боевой канал: ID не задан" in text
    assert "тестовый канал" in lines[-1]
    assert SECRET not in text
    # бот — участник админ-группы в режиме приватности: голосовые не придут (живой случай 22.10.2026)
    assert "не видит голосовые" in text
    answers["getMe"]["can_read_all_group_messages"] = True
    assert "не видит голосовые" not in "\n".join(cli.tg_check(settings, api))


def test_token_shape_explains_paste_errors():
    good = cli.token_shape(SECRET)
    assert "формат верный" in good[1]
    assert any("Ctrl+V" in x for x in cli.token_shape(chr(22)))                    # вставка Ctrl+V в скрытом вводе
    assert any("не целиком" in x for x in cli.token_shape(SECRET[:-5]))
    assert any("слова bot" in x for x in cli.token_shape("bot" + SECRET))
    assert all(SECRET not in x for x in cli.token_shape(SECRET))                  # значение не печатается


def test_tg_check_404_shows_shape_not_token(settings):
    settings.tg_token = SECRET[:-3]
    api = BotAPI("https://tg.example", settings.tg_token, client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Not Found"}))))
    text = "\n".join(cli.tg_check(settings, api))
    assert "не узнал токен" in text and "не целиком" in text and settings.tg_token not in text
