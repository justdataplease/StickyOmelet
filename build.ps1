$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

$workPath = Join-Path ([System.IO.Path]::GetTempPath()) "StickyOmeletBuild-$PID"

& ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-build.txt
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed with exit code $LASTEXITCODE"
}
& ".venv\Scripts\python.exe" tools\generate_icon.py
if ($LASTEXITCODE -ne 0) {
    throw "Icon generation failed with exit code $LASTEXITCODE"
}
& ".venv\Scripts\pyinstaller.exe" `
    --noconfirm `
    --clean `
    --workpath $workPath `
    --onefile `
    --windowed `
    --noupx `
    --name "StickyOmelet" `
    --icon "assets\stickyomelet.ico" `
    --add-data "assets\stickyomelet.ico;assets" `
    --add-data "assets\dot-mark.png;assets" `
    --add-data "assets\dot-bubble.png;assets" `
    --version-file "assets\version_info.txt" `
    --copy-metadata "urllib3" `
    --copy-metadata "gpsoauth" `
    --hidden-import "windows_integration" `
    notes_widget.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$resolvedWorkPath = [System.IO.Path]::GetFullPath($workPath)
if (-not $resolvedWorkPath.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    -not ([System.IO.Path]::GetFileName($resolvedWorkPath)).StartsWith("StickyOmeletBuild-", [System.StringComparison]::Ordinal)) {
    throw "Refusing to clean unexpected build path: $resolvedWorkPath"
}
if (Test-Path -LiteralPath $resolvedWorkPath) {
    Remove-Item -LiteralPath $resolvedWorkPath -Recurse -Force
}

if (Test-Path "dist\NotesWidget.exe") {
    Remove-Item -LiteralPath "dist\NotesWidget.exe" -Force
}
if (Test-Path "dist\GoogleKeepWidget.exe") {
    Remove-Item -LiteralPath "dist\GoogleKeepWidget.exe" -Force
}
if (Test-Path "dist\KeepNotesWidget.exe") {
    Remove-Item -LiteralPath "dist\KeepNotesWidget.exe" -Force
}
if (Test-Path "dist\JustNotes.exe") {
    Remove-Item -LiteralPath "dist\JustNotes.exe" -Force
}
if (Test-Path "dist\StickyFeather.exe") {
    Remove-Item -LiteralPath "dist\StickyFeather.exe" -Force
}
if (Test-Path "dist\StickyDot.exe") {
    Remove-Item -LiteralPath "dist\StickyDot.exe" -Force
}

Write-Host ""
Write-Host "Built: $PSScriptRoot\dist\StickyOmelet.exe" -ForegroundColor Green
