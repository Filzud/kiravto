# Настройка выгрузки в Google Таблицу. Запускает ЧЕЛОВЕК: скрипт создаёт ключ сервисного аккаунта,
# агент его не видит (хук guard.py запрещает ему читать secrets/ и *.sa.json).
#   powershell -ExecutionPolicy Bypass -File scripts\setup_gsheets.ps1 [-Project <id>]
# Что делает:
#   1) включает Google Sheets API в проекте;
#   2) создаёт сервисный аккаунт kiravto-sheets (если его нет);
#   3) кладёт JSON-ключ в secrets\kiravto.sa.json (папка в .gitignore);
#   4) просит ссылку на таблицу, которой вы дали доступ «Редактор» этому аккаунту, и пишет GSHEET_ID в .env.
param([string]$Project = "")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not $Project) { $Project = (gcloud config get-value project 2>$null).Trim() }
if (-not $Project) { throw "Не задан проект: gcloud config set project <id> или -Project <id>" }
$sa = "kiravto-sheets@$Project.iam.gserviceaccount.com"
Write-Host "Проект: $Project, сервисный аккаунт: $sa"

gcloud services enable sheets.googleapis.com --project $Project
$exists = gcloud iam service-accounts list --project $Project --filter="email:$sa" --format="value(email)"
if (-not $exists) {
    gcloud iam service-accounts create kiravto-sheets --project $Project --display-name "Kiravto: выгрузка склада"
}
New-Item -ItemType Directory -Force secrets | Out-Null
$key = Join-Path $root "secrets\kiravto.sa.json"
if (Test-Path $key) {
    Write-Host "Ключ уже есть: secrets\kiravto.sa.json (новый не создаю; удалите файл, чтобы перевыпустить)"
} else {
    gcloud iam service-accounts keys create $key --iam-account $sa --project $Project
}

Write-Host ""
Write-Host "Создайте таблицу на https://sheets.new и нажмите «Настройки доступа» → добавьте $sa с ролью «Редактор»."
$url = Read-Host "Вставьте ссылку на таблицу"
if ($url -notmatch "/spreadsheets/d/([A-Za-z0-9_-]+)") { throw "Не похоже на ссылку Google Таблицы" }
$sheetId = $Matches[1]

# .env правит человек этим скриптом; значения не печатаются
$envPath = Join-Path $root ".env"
$lines = @()
if (Test-Path $envPath) { $lines = Get-Content $envPath -Encoding UTF8 | Where-Object { $_ -notmatch "^(GSHEET_ID|GSHEET_CREDENTIALS)=" } }
$lines += "GSHEET_ID=$sheetId"
$lines += "GSHEET_CREDENTIALS=secrets/kiravto.sa.json"
[System.IO.File]::WriteAllLines($envPath, $lines, (New-Object System.Text.UTF8Encoding $false))
Write-Host "Готово. Проверка: python -m kiravto sheets-export (или docker compose up -d — выгрузка раз в 3 часа)"
