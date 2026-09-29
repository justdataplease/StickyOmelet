$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-build.txt
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed with exit code $LASTEXITCODE"
}

& ".venv\Scripts\python.exe" -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) {
    throw "Tests failed with exit code $LASTEXITCODE"
}

& ".venv\Scripts\python.exe" -m py_compile notes_widget.py keep_sync.py token_flow.py windows_integration.py
if ($LASTEXITCODE -ne 0) {
    throw "Syntax check failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "StickyOmelet tests passed." -ForegroundColor Green
