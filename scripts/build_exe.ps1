# Build a one-file Windows exe.
# Usage (from repo root):
#   powershell -File scripts\build_exe.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$python = if (Test-Path "C:\PythonProjects\.venv\Scripts\python.exe") {
    "C:\PythonProjects\.venv\Scripts\python.exe"
} else {
    "python"
}

Write-Host "Installing package + PyInstaller…"
& $python -m pip install -q -e ".[build]"
# PyInstaller refuses to run if the obsolete pathlib backport is present
& $python -m pip uninstall -y pathlib 2>$null | Out-Null

Write-Host "Building exe…"
& $python -m PyInstaller --noconfirm --clean adsb_manager.spec

$exe = Join-Path $PWD "dist\ADSB-Stack-Manager.exe"
if (-not (Test-Path $exe)) {
    throw "Build finished but $exe was not created"
}
Get-Item $exe | Format-List Name, Length, LastWriteTime
Write-Host "OK — $exe"
