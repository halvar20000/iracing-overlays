# ============================================================
#  Cloudflare Tunnel - fresh setup for live.simracing-hub.com
#  - Removes the old 'iracing-livedata' tunnel + config
#  - Creates a new tunnel 'simhub-live'
#  - Creates DNS CNAME live.simracing-hub.com -> tunnel
#  - Writes config in both user profile and system profile
#  - Reinstalls + starts the Windows service
#
#  Run from an ELEVATED PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\setup_live_tunnel.ps1
# ============================================================

$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false

$NewTunnel  = 'simhub-live'
$NewHost    = 'live.simracing-hub.com'
$LocalPort  = 5009
$OldTunnel  = 'iracing-livedata'

$UserCfgDir = Join-Path $env:USERPROFILE '.cloudflared'
$SysCfgDir  = 'C:\Windows\System32\config\systemprofile\.cloudflared'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Step($n, $msg) {
    Write-Host ''
    Write-Host "[$n] $msg" -ForegroundColor Cyan
}

function Run-Cf {
    param([Parameter(ValueFromRemainingArguments=$true)] [string[]] $CfArgs)
    $out = & cloudflared @CfArgs 2>&1 | Out-String
    return $out
}

if (-not (Test-Admin)) {
    Write-Error 'Please run from an elevated PowerShell.'
    Read-Host 'Press Enter to exit'; exit 1
}

Write-Host '============================================================'
Write-Host " Fresh tunnel: $NewTunnel  ->  https://$NewHost"
Write-Host '============================================================'
Read-Host 'Press Enter to continue'

# ---------- 1) Stop + remove the existing service ----------
Step 1 'Stopping + removing existing cloudflared service...'
$svc = Get-Service cloudflared -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -ne 'Stopped') {
        & sc.exe stop cloudflared | Out-Null
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Seconds 1
            $svc.Refresh()
            if ($svc.Status -eq 'Stopped') { break }
        }
        if ($svc.Status -ne 'Stopped') {
            & taskkill.exe /F /IM cloudflared.exe /T 2>&1 | Out-Null
            Start-Sleep -Seconds 2
        }
    }
    & cloudflared service uninstall 2>&1 | Out-Null
    Write-Host '  service removed.'
} else {
    Write-Host '  no existing service.'
}

# ---------- 2) Delete the old tunnel (best-effort) ----------
Step 2 "Cleaning up old tunnel '$OldTunnel' (best-effort)..."
$listText = Run-Cf tunnel list
if ($listText -match "\b$([Regex]::Escape($OldTunnel))\b") {
    Write-Host "  found '$OldTunnel' - removing stale connections then deleting..."
    Run-Cf tunnel cleanup $OldTunnel | Out-Null
    $del = Run-Cf tunnel delete -f $OldTunnel
    Write-Host "  $del"
} else {
    Write-Host "  '$OldTunnel' not present - skipping."
}

# ---------- 3) Wipe old config files ----------
Step 3 'Removing old config files...'
foreach ($d in @($UserCfgDir, $SysCfgDir)) {
    if (Test-Path $d) {
        # keep cert.pem (account cert), delete only old config + json creds
        Get-ChildItem $d -Filter 'config.yml' -ErrorAction SilentlyContinue | Remove-Item -Force
        Get-ChildItem $d -Filter '*.json'   -ErrorAction SilentlyContinue | Remove-Item -Force
        Write-Host "  cleaned $d"
    }
}
if (-not (Test-Path $SysCfgDir)) {
    New-Item -ItemType Directory -Path $SysCfgDir -Force | Out-Null
}

# ---------- 4) Make sure we still have a Cloudflare login cert ----------
Step 4 'Verifying Cloudflare login cert...'
$cert = Join-Path $UserCfgDir 'cert.pem'
if (-not (Test-Path $cert)) {
    Write-Host '  no cert.pem - launching login...'
    Read-Host 'Press Enter to open browser for Cloudflare login'
    & cloudflared tunnel login 2>&1 | ForEach-Object { Write-Host $_ }
    if (-not (Test-Path $cert)) { throw 'login did not produce cert.pem' }
} else {
    Write-Host '  cert.pem present.'
}

# ---------- 5) Create the new tunnel ----------
Step 5 "Creating tunnel '$NewTunnel'..."
$createOut = Run-Cf tunnel create $NewTunnel
Write-Host $createOut
if ($LASTEXITCODE -ne 0 -and $createOut -notmatch 'already exists') {
    throw 'tunnel create failed'
}

# ---------- 6) Resolve UUID ----------
Step 6 'Resolving new tunnel UUID...'
$listText = Run-Cf tunnel list
$line = ($listText -split "`r?`n") | Where-Object { $_ -match "\b$([Regex]::Escape($NewTunnel))\b" } | Select-Object -First 1
if (-not $line) { throw 'new tunnel not in list' }
$uuid = ($line.Trim() -split '\s+')[0]
if ($uuid -notmatch '^[0-9a-fA-F-]{36}$') { throw "bad UUID: $uuid" }
Write-Host "  UUID: $uuid"

$userCred = Join-Path $UserCfgDir "$uuid.json"
$sysCred  = Join-Path $SysCfgDir  "$uuid.json"

# ---------- 7) Write config.yml in both locations ----------
Step 7 'Writing config.yml (user + system profile)...'
$cfgUser = @"
tunnel: $uuid
credentials-file: $userCred

ingress:
  - hostname: $NewHost
    service: http://localhost:$LocalPort
  - service: http_status:404
"@
$cfgSys = @"
tunnel: $uuid
credentials-file: $sysCred

ingress:
  - hostname: $NewHost
    service: http://localhost:$LocalPort
  - service: http_status:404
"@
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $UserCfgDir 'config.yml'), $cfgUser, $enc)
[System.IO.File]::WriteAllText((Join-Path $SysCfgDir  'config.yml'), $cfgSys,  $enc)

# Copy the new credentials JSON into the system profile so the
# service (LocalSystem) can read it.
if (Test-Path $userCred) {
    Copy-Item $userCred -Destination $sysCred -Force
    Write-Host "  copied $userCred -> $sysCred"
} else {
    throw "cloudflared did not produce $userCred"
}

# ---------- 8) DNS route ----------
Step 8 "Routing DNS $NewHost -> $NewTunnel ..."
$routeOut = Run-Cf tunnel route dns $NewTunnel $NewHost
Write-Host $routeOut
if ($LASTEXITCODE -ne 0 -and $routeOut -notmatch 'already exists') {
    Write-Warning "route dns reported a problem - check Cloudflare DNS for a 'live' CNAME."
}

# ---------- 9) Install + start service ----------
Step 9 'Installing + starting cloudflared service...'
$installOut = & cloudflared service install 2>&1 | Out-String
Write-Host $installOut
Start-Service cloudflared
Start-Sleep -Seconds 4

# ---------- 10) Verify ----------
Step 10 'Verification:'
Get-Service cloudflared | Format-Table -AutoSize
Write-Host "> cloudflared tunnel info $NewTunnel"
& cloudflared tunnel info $NewTunnel 2>&1 | ForEach-Object { Write-Host "    $_" }

Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host ' Done.' -ForegroundColor Green
Write-Host '============================================================' -ForegroundColor Green
Write-Host " Public URL : https://$NewHost/share/chart"
Write-Host "             https://$NewHost/share/standings"
Write-Host ''
Write-Host ' Note: DNS may take 1-2 minutes to propagate. If "nslookup'
Write-Host " $NewHost 1.1.1.1`" returns NXDOMAIN, wait briefly and retry."
Write-Host ''
Read-Host 'Press Enter to exit'
