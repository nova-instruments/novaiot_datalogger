# Remove build artifacts, caches and local runtime data.
# Source files are never touched.
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$targets = @(
    'build',
    'dist',
    '__pycache__',
    '.runtime_home',
    'temp',
    '.agents',
    '.codex',
    'run_error.log',
    'run_runtime.log',
    'img\logo2026.ico',
    'novaview_datalogger_logo.png'
)

foreach ($t in $targets) {
    if (Test-Path $t) {
        Remove-Item -Recurse -Force $t
        Write-Host "removed  $t"
    } else {
        Write-Host "skip     $t (not present)"
    }
}

# Stray SQLite databases dropped in the project root
Get-ChildItem -File -Filter '*.db' | ForEach-Object {
    Remove-Item -Force $_.FullName
    Write-Host "removed  $($_.Name)"
}

Write-Host "`nDone."
