# ============================================================
#  Resume / repair the live.simracing-hub.com tunnel.
#  - Locates the credentials JSON wherever cloudflared put it
#  - If still missing, deletes the orphan tunnel and recreates it
#  - Writes config + creds in user + system profile
#  - Routes DNS, installs/restarts service, verifies
#
#  Run elevated:
#     powershell -ExecutionPolicy Bypass -File .\resume_live_tunnel.ps1
# ============================================================

$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false

$NewTunnel = 'simhub-live'
$NewHost   = 'live.simracing-hub.com'
$LocalPort = 5009

$UserCfgDir = Join-Path $env:USERPROFILE '.cloudflared'
$SysCfgDir  = 'C:\Windows\System32\config\systemprofile\.cloudflared'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not (Test-Admin)) {
    Write-Error 'Run from an elevated PowerShell.'
    Read-Host 'Press Enter'; exit 1
}

function Get-TunnelUuid([string] $name) {
    $txt = & cloudflared tunnel list 2>&1 | Out-String
    $line = ($txt -split "`r?`n") | Where-Object {
        $_ -match "\b$([Regex]::Escape($name))\b"
    } | Select-Object -First 1
    if (-not $line) { return $null }
    $u = ($line.Trim() -split '\s+')[0]
    if ($u -match '^[0-9a-fA-F-]{36}$') { return $u }
    return $null
}

function Find-CredFile([string] $uuid) {
    $candidates = @(
        (Join-Path $UserCfgDir "$uuid.json"),
        (Join-Path $SysCfgDir  "$uuid.json"),
        "C:\ProgramData\Cloudflare\$uuid.json",
        (Join-Path $env:LOCALAPPDATA ".cloudflared\$uuid.json"),
        (Join-Path $env:APPDATA      ".cloudflared\$uuid.json")
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    Write-Host "  not in common locations - scanning C:\Users and C:\Windows..."
    foreach ($root in 'C:\Users', 'C:\Windows') {
        $hit = Get-ChildItem $root -Recurse -Filter "$uuid.json" `
            -ErrorAction SilentlyContinue -Force | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Stop-Cf {
    $svc = Get-Service cloudflared -ErrorAction SilentlyContinue
    if (-not $svc -or $svc.Status -eq 'Stopped') { return }
    & sc.exe stop cloudflared | Out-Null
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 1; $svc.Refresh()
        if ($svc.Status -eq 'Stopped') { return }
    }
    & taskkill.exe /F /IM cloudflared.exe /T 2>&1 | Out-Null
    Start-Sleep -Seconds 2
}

# ---------- 1) Locate or recreate ----------
Write-Host '[1] Locating tunnel UUID...' -ForegroundColor Cyan
$uuid = Get-TunnelUuid $NewTunnel
if (-not $uuid) {
    Write-Host "  '$NewTunnel' not found - creating..."
    & cloudflared tunnel create $NewTunnel 2>&1 | ForEach-Object { Write-Host "    $_" }
    $uuid = Get-TunnelUuid $NewTunnel
    if (-not $uuid) { throw "tunnel create failed; '$NewTunnel' not in list" }
}
Write-Host "  UUID: $uuid"

Write-Host ''
Write-Host '[2] Locating credentials JSON...' -ForegroundColor Cyan
$cred = Find-CredFile $uuid

if (-not $cred) {
    Write-Warning "  credentials JSON for $uuid not found. The tunnel is orphaned."
    Write-Host '  -> deleting orphan tunnel and recreating cleanly.'
    Stop-Cf
    & cloudflared tunnel cleanup $NewTunnel 2>&1 | Out-Null
    & cloudflared tunnel delete -f $NewTunnel 2>&1 | Out-Null

    # Recreate
    & cloudflared tunnel create $NewTunnel 2>&1 | ForEach-Object { Write-Host "    $_" }
    $uuid = Get-TunnelUuid $NewTunnel
    if (-not $uuid) { throw 'recreate failed' }
    Write-Host "  new UUID: $uuid"

    $cred = Find-CredFile $uuid
    if (-not $cred) {
        throw "Even after recreate, $uuid.json is nowhere to be found. Check cloudflared output above for errors."
    }
}
Write-Host "  found: $cred"

# ---------- 3) Place credentials in both profile dirs ----------
Write-Host ''
Write-Host '[3] Distributing credentials to user + system profile...' -ForegroundColor Cyan
foreach ($d in @($UserCfgDir, $SysCfgDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
$userCred = Join-Path $UserCfgDir "$uuid.json"
$sysCred  = Join-Path $SysCfgDir  "$uuid.json"
if ($cred -ne $userCred) { Copy-Item $cred -Destination $userCred -Force }
Copy-Item $userCred -Destination $sysCred -Force
Write-Host "  $userCred"
Write-Host "  $sysCred"

# ---------- 4) Write config.yml in both ----------
Write-Host ''
Write-Host '[4] Writing config.yml in both locations...' -ForegroundColor Cyan
$enc = New-Object System.Text.UTF8Encoding($false)

$cfgUserText = @"
tunnel: $uuid
credentials-file: $userCred

ingress:
  - hostname: $NewHost
    service: http://localhost:$LocalPort
  - service: http_status:404
"@
$cfgSysText = @"
tunnel: $uuid
credentials-file: $sysCred

ingress:
  - hostname: $NewHost
    service: http://localhost:$LocalPort
  - service: http_status:404
"@
[System.IO.File]::WriteAllText((Join-Path $UserCfgDir 'config.yml'), $cfgUserText, $enc)
[System.IO.File]::WriteAllText((Join-Path $SysCfgDir  'config.yml'), $cfgSysText,  $enc)
Write-Host '  ok.'

# ---------- 5) DNS route ----------
Write-Host ''
Write-Host "[5] Routing DNS $NewHost -> $NewTunnel ..." -ForegroundColor Cyan
$routeOut = & cloudflared tunnel route dns $NewTunnel $NewHost 2>&1 | Out-String
Write-Host $routeOut
if ($LASTEXITCODE -ne 0 -and $routeOut -notmatch 'already exists') {
    Write-Warning "DNS route may have failed. Check Cloudflare dashboard for a 'live' CNAME."
}

# ---------- 6) Reinstall + start service ----------
Write-Host ''
Write-Host '[6] Reinstalling cloudflared service...' -ForegroundColor Cyan
Stop-Cf
$svc = Get-Service cloudflared -ErrorAction SilentlyContinue
if ($svc) {
    & cloudflared service uninstall 2>&1 | Out-Null
}
& cloudflared service install 2>&1 | ForEach-Object { Write-Host "    $_" }
Start-Service cloudflared
Start-Sleep -Seconds 4

# ---------- 7) Verify ----------
Write-Host ''
Write-Host '[7] Verification:' -ForegroundColor Cyan
Get-Service cloudflared | Format-Table -AutoSize
Write-Host "> cloudflared tunnel info $NewTunnel"
& cloudflared tunnel info $NewTunnel 2>&1 | ForEach-Object { Write-Host "    $_" }
Write-Host '> nslookup'
& nslookup $NewHost 1.1.1.1 2>&1 | ForEach-Object { Write-Host "    $_" }

Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host " Public URL : https://$NewHost/share/chart"
Write-Host "             https://$NewHost/share/standings"
Write-Host '============================================================' -ForegroundColor Green
Read-Host 'Press Enter to exit'
