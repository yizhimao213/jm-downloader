$ErrorActionPreference = 'SilentlyContinue'

$deployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $deployDir
$py = 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe'
$serverPy = Join-Path $root 'server.py'
$cf = Join-Path $root 'cloudflared.exe'
$tokenFile = Join-Path $root '.tunnel_token'
$serverOut = Join-Path $deployDir 'server.out.log'
$serverErr = Join-Path $deployDir 'server.err.log'
$tunnelOut = Join-Path $deployDir 'tunnel.out.log'
$tunnelErr = Join-Path $deployDir 'tunnel.err.log'
$daemonLog = Join-Path $deployDir 'daemon.log'

function Write-DaemonLog([string]$msg) {
  $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Add-Content -LiteralPath $daemonLog -Value $line -Encoding UTF8
}

if (-not (Test-Path -LiteralPath $py)) {
  Write-DaemonLog "Python not found: $py"
  exit 1
}
if (-not (Test-Path -LiteralPath $serverPy)) {
  Write-DaemonLog "server.py not found: $serverPy"
  exit 1
}
if (-not (Test-Path -LiteralPath $cf)) {
  Write-DaemonLog "cloudflared not found: $cf"
  exit 1
}
if (-not (Test-Path -LiteralPath $tokenFile)) {
  Write-DaemonLog "tunnel token not found: $tokenFile"
  exit 1
}

$serverListening = Get-NetTCPConnection -LocalPort 8899 -State Listen -ErrorAction SilentlyContinue
if (-not $serverListening) {
  Start-Process -FilePath $py -ArgumentList "`"$serverPy`"" -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr
  Write-DaemonLog 'started jm-downloader server'
} else {
  Write-DaemonLog 'server already listening on 8899'
}

$tunnelRunning = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" | Where-Object { $_.CommandLine -match 'token-file' }
if (-not $tunnelRunning) {
  Start-Process -FilePath $cf -ArgumentList 'tunnel --no-autoupdate run --token-file .tunnel_token' -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $tunnelOut -RedirectStandardError $tunnelErr
  Write-DaemonLog 'started cloudflared tunnel'
} else {
  Write-DaemonLog 'cloudflared tunnel already running'
}
