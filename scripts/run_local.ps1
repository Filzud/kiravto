# Запуск сервиса на этом компьютере без Docker: сборщик по расписанию + бот + HTTP API (порт 8080).
# Лог — data\serve.log (UTF-8). Остановить: закрыть окно или Ctrl+C.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
$env:KIRAVTO_LOG_FILE = "$root\data\serve.log"
# localhost не должен уходить в системный прокси
$env:NO_PROXY = "127.0.0.1,localhost"
Write-Host "Киравто запущен: сборщик + бот + API http://127.0.0.1:8080/health . Лог: data\serve.log. Окно не закрывать."
& "$root\.venv\Scripts\python.exe" -m kiravto serve --host 127.0.0.1 --port 8080
