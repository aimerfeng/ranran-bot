# tg-llm-bot supervisor: restart on crash, on hung polling (stale log), and every 12h.
$ErrorActionPreference = 'SilentlyContinue'
# 用脚本自身所在目录，换机器/换用户都不用改
$root    = $PSScriptRoot
$bat     = Join-Path $root 'run_bot.bat'
$errLog  = Join-Path $root 'bot.err.log'
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Log([string]$msg) {
    Write-Output ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
}

function Log-Stale([int]$maxMinutes = 6) {
    if (-not (Test-Path $errLog)) { return $false }
    try {
        $m = (Get-Item $errLog -ErrorAction Stop).LastWriteTime
    } catch { return $false }
    return ((Get-Date) - $m).TotalMinutes -gt $maxMinutes
}

Log 'supervisor started'
while ($true) {
    Log ('launching bot via ' + $bat)
    $p = Start-Process -FilePath 'cmd.exe' -ArgumentList ('/c "' + $bat + '"') -WorkingDirectory $root -WindowStyle Hidden -PassThru
    $started  = Get-Date
    $deadline = $started.AddHours(12)
    $reason   = ''
    while (-not $p.HasExited) {
        Start-Sleep -Seconds 45
        if (Log-Stale) { $reason = 'hung (no log activity > 6 min)'; break }
        if ((Get-Date) -ge $deadline) { $reason = '12h lifetime reached'; break }
    }
    if ($p.HasExited) {
        Log ('bot exited on its own (code ' + $p.ExitCode + '); restarting in 10s')
    } else {
        Log ('restarting bot: ' + $reason)
        & taskkill /PID $p.Id /T /F 2>$null | Out-Null
        $p.WaitForExit(15000) | Out-Null
    }
    Start-Sleep -Seconds 10
}
