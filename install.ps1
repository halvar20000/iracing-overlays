# iRacing Overlays - installer (Windows PowerShell 5.1+)
#
# Run via install.bat (double-click), or without downloading anything first:
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/halvar20000/iracing-overlays/main/install.ps1 | iex"
#
# 1. finds Python 3.10+ (with tkinter) - installs Python 3.12 via winget if none
# 2. asks for the install folder (default: %USERPROFILE%\iRacing-Overlays,
#    or the folder this script sits in when that is an unpacked ZIP)
# 3. downloads the current GitHub version + pip install (updater.py --install)
# 4. creates "iRacing Overlays" shortcuts on the desktop and in the Start menu
#
# Running it again is safe: it updates the folder, local configs are kept.
# Later updates come from the launcher itself ("Update & restart").

param([string]$Dir = "")

$ErrorActionPreference = "Stop"
$Repo = "halvar20000/iracing-overlays"
$Raw  = "https://raw.githubusercontent.com/$Repo/main"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Say([string]$m, [string]$c = "Gray") { Write-Host $m -ForegroundColor $c }

function Find-Python {
    $cands = @()
    foreach ($cmd in @("py", "python")) {
        $g = Get-Command $cmd -ErrorAction SilentlyContinue
        # Skip the Microsoft Store alias (WindowsApps) - it only opens the Store.
        if ($g -and $g.Source -notmatch "WindowsApps") { $cands += $g.Source }
    }
    foreach ($root in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles")) {
        $cands += Get-ChildItem "$root\Python3*\python.exe" -ErrorAction SilentlyContinue |
                  Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
    }
    foreach ($c in $cands) {
        $pyArgs = @()
        if ((Split-Path $c -Leaf) -ieq "py.exe") { $pyArgs = @("-3") }
        try {
            $exe = & $c @pyArgs -c "import sys, tkinter; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return ($exe | Select-Object -Last 1).Trim() }
        } catch { }
    }
    return $null
}

Say ""
Say "=== iRacing Overlays - installer ===" "Cyan"
Say ""

# --- 1. Python --------------------------------------------------------------
$py = Find-Python
if (-not $py) {
    Say "Python 3.10+ not found." "Yellow"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "Installing Python 3.12 with winget (a few minutes) ..."
        $ErrorActionPreference = "Continue"
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $ErrorActionPreference = "Stop"
        $py = Find-Python
    }
    if (-not $py) {
        Say "Please install Python from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')," "Red"
        Say "then run this installer again." "Red"
        Start-Process "https://www.python.org/downloads/"
        exit 1
    }
}
Say "Python: $py" "Green"

# --- 2. Folder ----------------------------------------------------------------
$here = $PSScriptRoot
if (-not $Dir) {
    if ($here -and (Test-Path (Join-Path $here "launch_gui.py")) -and -not (Test-Path (Join-Path $here ".git"))) {
        $default = $here
    } else {
        $default = Join-Path $env:USERPROFILE "iRacing-Overlays"
    }
    $ans = Read-Host "Install folder [$default]"
    if ($ans) { $Dir = $ans.Trim('"', ' ') } else { $Dir = $default }
}
if (Test-Path (Join-Path $Dir ".git")) {
    Say "$Dir is a git checkout - update it with 'git pull' instead." "Red"
    exit 1
}
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$Dir = (Resolve-Path $Dir).Path
Say "Folder: $Dir" "Green"

# --- 3. Download + pip --------------------------------------------------------
$upd = Join-Path $Dir "updater.py"
Invoke-WebRequest "$Raw/updater.py" -OutFile $upd -UseBasicParsing
# Native commands: don't let a stderr line abort the script (PS 5.1 turns
# redirected stderr into errors under "Stop").
$ErrorActionPreference = "Continue"
& $py $upd --install $Dir
$rc = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($rc -eq 1) { Say "Download / install failed (see above)." "Red"; exit 1 }
if ($rc -eq 2) { Say "Files installed, but 'pip install' reported an error (see above)." "Yellow" }

# --- 4. Shortcuts ---------------------------------------------------------------
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
$ws = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    $lnk = $ws.CreateShortcut((Join-Path $folder "iRacing Overlays.lnk"))
    $lnk.TargetPath = $pyw
    $lnk.Arguments = '"' + (Join-Path $Dir "launch_gui.py") + '"'
    $lnk.WorkingDirectory = $Dir
    $lnk.Description = "iRacing Overlays launcher"
    $lnk.Save()
}
Say "Shortcuts 'iRacing Overlays' created (desktop + Start menu)." "Green"

Say ""
Say "Done." "Cyan"
Say "OBS: Browser source -> 'Local file' -> pick a file from"
Say "     $(Join-Path $Dir 'obs_loaders')" "White"
Say "     Sizes: OBS_OVERLAYS.md in the install folder."
Say ""
$go = Read-Host "Start the launcher now? [Y/n]"
if (-not $go -or $go -match "^[yYjJ]") {
    Start-Process -FilePath $pyw -ArgumentList ('"' + (Join-Path $Dir "launch_gui.py") + '"') -WorkingDirectory $Dir
}
