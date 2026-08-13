# Build a pip-installable wheel + sdist.
# Usage (from repo root):
#   powershell -File scripts\build_wheel.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$python = if (Test-Path "C:\PythonProjects\.venv\Scripts\python.exe") {
    "C:\PythonProjects\.venv\Scripts\python.exe"
} else {
    "python"
}

& $python -m pip install -q -e ".[build]"
& $python -m build
Get-ChildItem dist -File | Format-Table Name, Length
Write-Host "OK — wheels/sdists in dist\"
