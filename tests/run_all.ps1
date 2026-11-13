# Run the whole YouTrack Widget test suite.
#   powershell -ExecutionPolicy Bypass -File tests\run_all.ps1          # all tests (sandboxed, safe)
#   powershell -ExecutionPolicy Bypass -File tests\run_all.ps1 -Real    # + read-only check against your YouTrack
#   powershell -ExecutionPolicy Bypass -File tests\run_all.ps1 -Build   # rebuild exe before exe/installer tests
param([switch]$Real, [switch]$Build)
$ErrorActionPreference = 'Continue'
$tests = $PSScriptRoot
$root = Split-Path $tests
$py = $env:YTW_PYTHON
if (-not $py) { $py = (& py -3 -c "import sys;print(sys.executable)" 2>$null) }
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw 'Python 3 not found (install Python 3.12+ or set YTW_PYTHON)' }
$env:PYTHONIOENCODING = 'utf-8'
$results = [ordered]@{}

Write-Host "== 1. Static analysis (pyflakes)" -ForegroundColor Cyan
$tmp = Join-Path $env:TEMP 'ytw_lint.py'
Copy-Item (Join-Path $root 'youtrack_widget.pyw') $tmp -Force
& $py -m pyflakes $tmp (Join-Path $root 'installer.py') (Join-Path $root 'make_icon.py') $tests
$results['static analysis'] = ($LASTEXITCODE -eq 0)

if ($Build) {
    Write-Host "== Build" -ForegroundColor Cyan
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'build.ps1')
    $results['build'] = ($LASTEXITCODE -eq 0)
}

Push-Location $tests
foreach ($t in @(@('2. Unit tests', 'test_unit.py'), @('3. UI and scenarios', 'test_ui.py'), @('4. Installer and exe', 'test_build.py'))) {
    Write-Host "== $($t[0])" -ForegroundColor Cyan
    & $py $t[1]
    $results[$t[0]] = ($LASTEXITCODE -eq 0)
}
Pop-Location

if ($Real) {
    Write-Host "== 5. Your YouTrack (read-only)" -ForegroundColor Cyan
    & $py (Join-Path $tests 'smoke_real.py')
    $results['5. Your YouTrack (read-only)'] = ($LASTEXITCODE -eq 0)
}

Write-Host "`n== Summary" -ForegroundColor Cyan
foreach ($k in $results.Keys) {
    if ($results[$k]) { Write-Host "  PASS  $k" -ForegroundColor Green } else { Write-Host "  FAIL  $k" -ForegroundColor Red }
}
if ($results.Values -contains $false) { exit 1 }
