# Запуск сервиса на этом компьютере без Docker: сборщик по расписанию + бот + HTTP API (порт 8080).
# Логи — data\serve.log. Остановить: закрыть окно или Ctrl+C.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
# localhost не должен уходить в системный прокси
$env:NO_PROXY = "127.0.0.1,localhost"
& "$root\.venv\Scripts\python.exe" -m kiravto serve --host 127.0.0.1 --port 8080 *>> "$root\data\serve.log"
