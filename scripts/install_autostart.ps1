# Автозапуск сервиса при входе в Windows (задача планировщика «Kiravto»). Запускает человек, не агент.
# Удалить: Unregister-ScheduledTask -TaskName Kiravto -Confirm:$false
$root = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\scripts\run_local.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "Kiravto" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Киравто: сборщик склада + бот + API" -Force | Out-Null
Start-ScheduledTask -TaskName "Kiravto"
Write-Host "Задача Kiravto зарегистрирована и запущена. Проверка: http://127.0.0.1:8080/health"
