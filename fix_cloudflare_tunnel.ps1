# ============================================================
#  Cloudflare Tunnel - service config fix + diagnostic
#  The cloudflared Windows service runs as LocalSystem and reads
#  config from   C:\Windows\System32\config\systemprofile\.cloudflared
#  not from your user profile. This script copies the user-profile
#  config.yml + credentials JSON over and restarts the service.
#
#  Run from an ELEVATED PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\fix_cloudflare_tunnel.ps1
# ============================================================

$ErrorActionPreference = 'Continue'
$PSNativeCommandUseErrorActionPreference = $false

$TunnelName = 'iracing-livedata'
$UserCfgDir = Join-Path $env:USERPROFILE '.cloudflared'
$SysCfgDir  = 'C:\Windows\System32\config\systemprofile\.cloudflared'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Error 'This script needs to run as Administrator (it copies into C:\Windows\System32\...).'
    Read-Host 'Press Enter to exit'
    exit 1
}

Write-Host '============================================================'
Write-Host ' Diagnostic - before'
Write-Host '============================================================'

Write-Host ''
Write-Host '> Get-Service cloudflared'
Get-Service cloudflared -ErrorAction SilentlyContinue | Format-Table -AutoSize

Write-Host ''
Write-Host "> cloudflared tunnel info $TunnelName"
& cloudflared tunnel info $TunnelName 2>&1 | ForEach-Object { Write-Host "    $_" }

Write-Host ''
Write-Host '> User profile config:'
if (Test-Path $UserCfgDir) {
    Get-ChildItem $UserCfgDir | Format-Table Name, Length, LastWriteTime -AutoSize
} else {
    Write-Host "    (no $UserCfgDir)"
}

Write-Host ''
Write-Host '> System profile config:'
if (Test-Path $SysCfgDir) {
    Get-ChildItem $SysCfgDir | Format-Table Name, Length, LastWriteTime -AutoSize
} else {
    Write-Host "    (no $SysCfgDir - this is the problem)"
}

# ---------- locate user-profile config + credentials ----------
$userCfg = Join-Path $UserCfgDir 'config.yml'
if (-not (Test-Path $userCfg)) {
    Write-Error "No config.yml at $userCfg. Run setup_cloudflare_tunnel.ps1 first."
    Read-Host 'Press Enter to exit'
    exit 1
}

# Find the credentials JSON whose filename matches the UUID in the yaml
$cfgText = Get-Content $userCfg -Raw
$uuidMatch = [regex]::Match($cfgText, 'tunnel:\s*([0-9a-fA-F-]{36})')
if (-not $uuidMatch.Success) {
    Write-Error "Could not find tunnel UUID in $userCfg"
    Read-Host 'Press Enter to exit'
    exit 1
}
$uuid = $uuidMatch.Groups[1].Value
$userCred = Join-Path $UserCfgDir "$uuid.json"
if (-not (Test-Path $userCred)) {
    Write-Error "Credentials file missing: $userCred"
    Read-Host 'Press Enter to exit'
    exit 1
}

Write-Host ''
Write-Host '============================================================'
Write-Host " Fix - copying config + credentials for tunnel $uuid"
Write-Host '============================================================'

# ---------- ensure target dir ----------
if (-not (Test-Path $SysCfgDir)) {
    New-Item -ItemType Directory -Path $SysCfgDir -Force | Out-Null
    Write-Host "Created $SysCfgDir"
}

# ---------- stop service so files aren't locked ----------
function Stop-CfService {
    $svc = Get-Service cloudflared -ErrorAction SilentlyContinue
    if (-not $svc) { return }
    if ($svc.Status -eq 'Stopped') { return }

    # 1) Polite stop with a 10s timeout (Stop-Service -Force can hang
    #    indefinitely if cloudflared is busy reconnecting).
    Write-Host 'Stopping cloudflared service (sc stop)...'
    & sc.exe stop cloudflared | Out-Null
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 1
        $svc.Refresh()
        if ($svc.Status -eq 'Stopped') {
            Write-Host '  service stopped.'
            return
        }
    }

    # 2) Hard kill the process(es). The service control manager will
    #    flip the service to Stopped state once the binary exits.
    Write-Host '  service did not stop within 10s. Killing cloudflared.exe ...'
    & taskkill.exe /F /IM cloudflared.exe /T 2>&1 | ForEach-Object { Write-Host "    $_" }
    Start-Sleep -Seconds 2
    $svc.Refresh()
    Write-Host "  service status now: $($svc.Status)"
}
Stop-CfService

# ---------- rewrite config.yml in system profile with absolute path
# pointing to the system-profile credentials JSON ----------
$sysCred = Join-Path $SysCfgDir "$uuid.json"
$sysCfg  = Join-Path $SysCfgDir 'config.yml'

# Read current ingress block from user yaml verbatim except the
# credentials-file line, which we rewrite to the system path.
$lines = Get-Content $userCfg
$newLines = foreach ($l in $lines) {
    if ($l -match '^\s*credentials-file\s*:') {
        "credentials-file: $sysCred"
    } else {
        $l
    }
}
[System.IO.File]::WriteAllLines($sysCfg, $newLines, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote $sysCfg"

# ---------- copy credentials JSON ----------
Copy-Item $userCred -Destination $sysCred -Force
Write-Host "Copied $userCred -> $sysCred"

Write-Host ''
Write-Host '--- system-profile config.yml ---'
Get-Content $sysCfg
Write-Host '---------------------------------'

# ---------- start service ----------
Write-Host ''
Write-Host 'Starting cloudflared service...'
if (-not (Get-Service cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host 'No service installed yet - installing now.'
    & cloudflared service install 2>&1 | ForEach-Object { Write-Host $_ }
}
Start-Service cloudflared
Start-Sleep -Seconds 4

Write-Host ''
Write-Host '============================================================'
Write-Host ' Diagnostic - after'
Write-Host '============================================================'

Write-Host ''
Write-Host '> Get-Service cloudflared'
Get-Service cloudflared | Format-Table -AutoSize

Write-Host ''
Write-Host "> cloudflared tunnel info $TunnelName"
& cloudflared tunnel info $TunnelName 2>&1 | ForEach-Object { Write-Host "    $_" }

Write-Host ''
Write-Host 'If the tunnel still shows 0 connectors, check the service log:'
Write-Host '   Get-EventLog -LogName Application -Source cloudflared -Newest 20'
Write-Host 'Or, for newer Windows:'
Write-Host '   Get-WinEvent -ProviderName cloudflared -MaxEvents 20'
Write-Host ''
Read-Host 'Press Enter to exit'
