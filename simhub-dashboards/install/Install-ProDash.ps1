<#
    Install-ProDash.ps1  —  SimHub Pro Dash installer
    - Copies the User.SimHubProDash.dll into your SimHub program folder
    - Extracts every dashboard in ..\release into your SimHub DashTemplates folder
    Run by double-clicking "Install ProDash.bat" (it launches this, elevating if needed).
#>
[CmdletBinding()] param([string]$SimHubDir)

$ErrorActionPreference = 'Stop'
$root      = Split-Path -Parent $MyInvocation.MyCommand.Path      # ...\install
$repo      = Split-Path -Parent $root                              # ...\simhub-dashboards
$dll       = Join-Path $root 'dist\User.SimHubProDash.dll'
$releaseDir= Join-Path $repo 'release'

function Find-SimHub {
    if ($SimHubDir -and (Test-Path (Join-Path $SimHubDir 'SimHubWPF.exe'))) { return $SimHubDir }
    $cands = @(
        'C:\Program Files (x86)\SimHub',
        'C:\Program Files\SimHub'
    )
    # registry (uninstall entries)
    foreach ($h in 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
                   'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*') {
        try { Get-ItemProperty $h -EA SilentlyContinue |
              Where-Object { $_.DisplayName -like 'SimHub*' -and $_.InstallLocation } |
              ForEach-Object { $cands += $_.InstallLocation } } catch {}
    }
    foreach ($c in $cands) { if ($c -and (Test-Path (Join-Path $c 'SimHubWPF.exe'))) { return $c } }
    return $null
}

Write-Host "== SimHub Pro Dash installer ==" -ForegroundColor Cyan

# 1) Dashboards -> DashTemplates
$docs = [Environment]::GetFolderPath('MyDocuments')
$dashTpl = Join-Path $docs 'SimHub\DashTemplates'
if (-not (Test-Path $dashTpl)) { New-Item -ItemType Directory -Force -Path $dashTpl | Out-Null }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$installed = 0
Get-ChildItem $releaseDir -Filter *.simhubdash | ForEach-Object {
    $name = [IO.Path]::GetFileNameWithoutExtension($_.Name)
    $dest = Join-Path $dashTpl $name
    if (Test-Path $dest) {
        $bak = "$dest.bak_$(Get-Date -f yyyyMMdd_HHmmss)"
        Write-Host ("  backing up existing '{0}' -> {1}" -f $name, (Split-Path $bak -Leaf)) -ForegroundColor DarkYellow
        Rename-Item $dest $bak
    }
    [IO.Compression.ZipFile]::ExtractToDirectory($_.FullName, $dashTpl)
    Write-Host "  dashboard installed: $name" -ForegroundColor Green
    $installed++
}
Write-Host "  $installed dashboards extracted to $dashTpl"

# 2) Plugin DLL -> SimHub program folder
if (Test-Path $dll) {
    $sh = Find-SimHub
    if (-not $sh) {
        Write-Host "  ! Could not locate SimHub. Re-run with:  -SimHubDir 'C:\Path\To\SimHub'" -ForegroundColor Red
    } else {
        try {
            Copy-Item $dll (Join-Path $sh 'User.SimHubProDash.dll') -Force
            Write-Host "  plugin installed: $sh\User.SimHubProDash.dll" -ForegroundColor Green
        } catch {
            Write-Host "  ! Could not copy the DLL (need admin?). $_" -ForegroundColor Red
        }
    }
} else {
    Write-Host "  (no dist\User.SimHubProDash.dll found - skipping plugin copy; build it or drop the DLL there)" -ForegroundColor DarkYellow
}

Write-Host ""
Write-Host "Done. Next steps:" -ForegroundColor Cyan
Write-Host "  1. Start SimHub. If it asks to enable the 'SimHub Pro Dash' plugin, click YES."
Write-Host "  2. Dashboards appear under Dash Studio / Add > they are named 'ProDash ...'."
Write-Host "  3. Add a dashboard as an OBS Browser source or on your DDU as usual."
Read-Host "Press Enter to close"
