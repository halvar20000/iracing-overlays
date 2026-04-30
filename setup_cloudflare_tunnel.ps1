# ============================================================
#  Cloudflare Tunnel setup for iRacing Race Logger
#  Maps livedata.simracing-hub.com -> http://localhost:5009
#  Idempotent: re-running skips steps already done.
#  Run from an ELEVATED PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\setup_cloudflare_tunnel.ps1
# ============================================================

# Use 'Continue' (not 'Stop') because cloudflared writes harmless
# warnings (e.g. "version outdated") to stderr, and 'Stop' would
# treat those as terminating errors. We check $LASTEXITCODE manually.
$ErrorActionPreference = 'Continue'
# PS 7+: don't auto-throw on non-zero native exit codes either.
$PSNativeCommandUseErrorActionPreference = $false

$TunnelName = 'iracing-livedata'
$Hostname   = 'livedata.simracing-hub.com'
$LocalPort  = 5009
$CfgDir     = Join-Path $env:USERPROFILE '.cloudflared'

function Write-Step($n, $msg) {
    Write-Host ""
    Write-Host "[$n/7] $msg" -ForegroundColor Cyan
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host "============================================================"
Write-Host " Cloudflare Tunnel - iRacing Race Logger"
Write-Host "============================================================"
Write-Host " Tunnel name : $TunnelName"
Write-Host " Hostname    : $Hostname"
Write-Host " Local URL   : http://localhost:$LocalPort"
Write-Host " Config dir  : $CfgDir"
Write-Host "============================================================"
if (-not (Test-Admin)) {
    Write-Warning "Not running as Administrator. Step 7 (service install) will fail."
    Write-Warning "Re-launch PowerShell with 'Run as administrator' if you want auto-start at boot."
}
Read-Host "Press Enter to continue"

# ---------- 1) Ensure cloudflared is installed ----------
Write-Step 1 'Checking for cloudflared...'
$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cf) {
    Write-Host 'cloudflared not on PATH. Installing via winget...'
    winget install --id Cloudflare.cloudflared --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget install failed. Install manually from https://github.com/cloudflare/cloudflared/releases/latest"
    }
    Write-Host ''
    Write-Warning 'cloudflared installed. PATH only refreshes in NEW PowerShell sessions.'
    Write-Warning 'Close this window, open a NEW elevated PowerShell, and re-run this script.'
    Read-Host 'Press Enter to exit'
    exit 0
}
Write-Host "Found: $($cf.Source)"

# ---------- 2) Login if cert.pem missing ----------
Write-Step 2 'Cloudflare login...'
$certPath = Join-Path $CfgDir 'cert.pem'
if (Test-Path $certPath) {
    Write-Host 'cert.pem already present - skipping login.'
} else {
    if (-not (Test-Path $CfgDir)) { New-Item -ItemType Directory -Path $CfgDir | Out-Null }
    Write-Host 'Browser will open. Pick your zone "simracing-hub.com" and click Authorize.'
    Read-Host 'Press Enter to start login'
    & cloudflared tunnel login 2>&1 | ForEach-Object { Write-Host $_ }
    if (-not (Test-Path $certPath)) { throw 'Login did not produce cert.pem' }
}

# Helper: run cloudflared and capture stdout+stderr as a single string.
# Avoids PS treating stderr lines as errors.
function Invoke-CfText {
    param([Parameter(ValueFromRemainingArguments=$true)] [string[]] $CfArgs)
    $out = & cloudflared @CfArgs 2>&1 | Out-String
    return $out
}

# ---------- 3) Create tunnel if missing ----------
Write-Step 3 "Ensuring tunnel '$TunnelName' exists..."
$listText = Invoke-CfText tunnel list
if ($LASTEXITCODE -ne 0) {
    Write-Host $listText
    throw "cloudflared tunnel list failed (exit $LASTEXITCODE)"
}
$tunnelLine = ($listText -split "`r?`n") | Where-Object { $_ -match "\b$([Regex]::Escape($TunnelName))\b" } | Select-Object -First 1
if ($tunnelLine) {
    Write-Host "Tunnel '$TunnelName' already exists."
} else {
    & cloudflared tunnel create $TunnelName
    if ($LASTEXITCODE -ne 0) { throw 'tunnel create failed' }
    # Re-read list so step 4 finds it
    $listText = Invoke-CfText tunnel list
    $tunnelLine = ($listText -split "`r?`n") | Where-Object { $_ -match "\b$([Regex]::Escape($TunnelName))\b" } | Select-Object -First 1
}

# ---------- 4) Resolve UUID ----------
Write-Step 4 'Resolving tunnel UUID...'
if (-not $tunnelLine) { throw 'Could not find tunnel in list output.' }
$uuid = ($tunnelLine.Trim() -split '\s+')[0]
if ($uuid -notmatch '^[0-9a-fA-F-]{36}$') {
    Write-Host "Raw line: $tunnelLine"
    throw "Parsed UUID looks wrong: '$uuid'"
}
Write-Host "UUID: $uuid"

# ---------- 5) Write config.yml ----------
Write-Step 5 'Writing config.yml...'
$credFile = Join-Path $CfgDir "$uuid.json"
$cfgYaml = @"
tunnel: $uuid
credentials-file: $credFile

ingress:
  - hostname: $Hostname
    service: http://localhost:$LocalPort
  - service: http_status:404
"@
$cfgPath = Join-Path $CfgDir 'config.yml'
# UTF-8 without BOM (cloudflared parses YAML; a BOM can break the first key).
[System.IO.File]::WriteAllText($cfgPath, $cfgYaml, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "--- $cfgPath ---"
Get-Content $cfgPath
Write-Host '------------------'

# ---------- 6) DNS route ----------
Write-Step 6 "Routing DNS $Hostname -> $TunnelName..."
& cloudflared tunnel route dns $TunnelName $Hostname 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'route dns returned non-zero - usually means the record already exists. Continuing.'
}

# ---------- 7) Service install ----------
Write-Step 7 'Install Windows service?'
$ans = Read-Host 'Install cloudflared as a Windows service so it auto-starts at boot? [Y/n]'
if ($ans -and $ans.ToLower().StartsWith('n')) {
    Write-Host 'Skipping service install.'
    Write-Host "To run manually:  cloudflared tunnel run $TunnelName"
} else {
    if (-not (Test-Admin)) {
        Write-Warning 'Need administrator for service install. Skipping.'
    } else {
        # Reinstall cleanly so any old config is dropped
        $svc = Get-Service -Name cloudflared -ErrorAction SilentlyContinue
        if ($svc) {
            Write-Host 'Existing cloudflared service found - reinstalling.'
            if ($svc.Status -eq 'Running') { Stop-Service cloudflared -Force }
            & cloudflared service uninstall 2>&1 | Out-Null
        }
        & cloudflared service install 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw 'service install failed' }
        Start-Service cloudflared -ErrorAction SilentlyContinue
        Write-Host 'Service installed and started.'
    }
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host ' Setup complete.' -ForegroundColor Green
Write-Host '============================================================' -ForegroundColor Green
Write-Host " Make sure iracing_race_logger.py is running on port $LocalPort."
Write-Host ''
Write-Host ' Public URLs (share with viewers):'
Write-Host "   https://$Hostname/share/chart"
Write-Host "   https://$Hostname/share/standings"
Write-Host ''
Write-Host ' Service control:'
Write-Host '   Start-Service cloudflared'
Write-Host '   Stop-Service  cloudflared'
Write-Host '   Get-Service   cloudflared'
Write-Host ''
Read-Host 'Press Enter to exit'
