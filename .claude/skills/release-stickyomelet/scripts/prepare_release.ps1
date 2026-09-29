param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [switch]$SkipBuild,
    [switch]$RequireClean
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
Set-Location $projectRoot

$versionParts = $Version.Split('.')
$tuple = "($($versionParts[0]), $($versionParts[1]), $($versionParts[2]), 0)"
$metadata = Get-Content ".\assets\version_info.txt" -Raw -Encoding UTF8

foreach ($required in @(
    "filevers=$tuple",
    "prodvers=$tuple",
    "StringStruct('FileVersion', '$Version')",
    "StringStruct('ProductVersion', '$Version')"
)) {
    if (-not $metadata.Contains($required)) {
        throw "Version metadata is inconsistent. Missing: $required"
    }
}

if ($RequireClean -and (git status --porcelain)) {
    throw "The worktree must be clean when -RequireClean is used."
}

& ".\run-tests.ps1"
if ($LASTEXITCODE -ne 0) {
    throw "Test suite failed with exit code $LASTEXITCODE"
}

if (-not $SkipBuild) {
    & ".\build.ps1"
    if ($LASTEXITCODE -ne 0) {
        throw "Portable build failed with exit code $LASTEXITCODE"
    }
}

$artifact = (Resolve-Path ".\dist\StickyOmelet.exe").Path
$info = Get-Item -LiteralPath $artifact
if ($info.VersionInfo.FileVersion -ne $Version) {
    throw "Built EXE version $($info.VersionInfo.FileVersion) does not match $Version"
}
if ($info.VersionInfo.ProductName -ne "StickyOmelet") {
    throw "Built EXE product name is not StickyOmelet"
}

$setupArtifacts = @(Get-ChildItem ".\dist" -Filter "StickyOmelet-Setup-*.exe" -File -ErrorAction SilentlyContinue)
if ($setupArtifacts.Count -gt 0) {
    throw "Portable-only release violated: remove stale StickyOmelet setup executables from dist."
}

$signature = Get-AuthenticodeSignature -LiteralPath $artifact
$hash = Get-FileHash -LiteralPath $artifact -Algorithm SHA256

[pscustomobject]@{
    Version = $Version
    Artifact = $artifact
    SizeMB = [math]::Round($info.Length / 1MB, 2)
    SHA256 = $hash.Hash
    Signature = $signature.Status
    Tests = "passed"
    PortableOnly = $true
} | Format-List
