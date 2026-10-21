# Установка WSL2 и Docker Desktop (нужны права администратора). Лог — data\install_docker.log
$log = Join-Path (Split-Path -Parent $PSScriptRoot) "data\install_docker.log"
Start-Transcript -Path $log -Append | Out-Null
Write-Host "== 1/2 WSL2 (подсистема Linux, нужна Docker Desktop)"
wsl.exe --install --no-distribution
Write-Host "wsl exit code: $LASTEXITCODE"
Write-Host "== 2/2 Docker Desktop"
winget install --id Docker.DockerDesktop -e --accept-package-agreements --accept-source-agreements --silent
Write-Host "winget exit code: $LASTEXITCODE"
Write-Host "== Готово. Нужна перезагрузка Windows, затем запустить Docker Desktop из меню Пуск."
Stop-Transcript | Out-Null
