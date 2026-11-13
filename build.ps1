# Build YouTrackWidget.exe and the installer YouTrackWidget-Setup.exe
# Run: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root
# Python: $env:YTW_PYTHON, otherwise the py launcher, otherwise python from PATH
$py = $env:YTW_PYTHON
if (-not $py) { $py = (& py -3 -c "import sys;print(sys.executable)" 2>$null) }
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw 'Python 3 not found (install Python 3.12+ or set YTW_PYTHON)' }
$ico = Join-Path $root 'app.ico'

& $py (Join-Path $root 'make_icon.py') $ico
if ($LASTEXITCODE) { throw 'icon failed' }
$png = Join-Path $root 'app.png'  # иконка для уведомлений Windows
& $py -c "import sys; sys.path.insert(0, r'$root'); import make_icon; open(r'$png', 'wb').write(make_icon.render(128))"
if ($LASTEXITCODE) { throw 'png icon failed' }

Copy-Item (Join-Path $root 'youtrack_widget.pyw') (Join-Path $root 'build_widget.py') -Force
& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name YouTrackWidget `
    --icon $ico --add-data "$ico;." --add-data "$png;." --add-data "$root\fonts\MaterialIconsRound-Regular.otf;fonts" `
    --distpath "$root\build\widget" --workpath "$root\build\tmp" --specpath "$root\build" "$root\build_widget.py"
if ($LASTEXITCODE) { throw 'widget build failed' }
Remove-Item (Join-Path $root 'build_widget.py')

& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name YouTrackWidget-Setup `
    --icon $ico --add-data "$ico;." --add-binary "$root\build\widget\YouTrackWidget.exe;." `
    --distpath "$root\dist" --workpath "$root\build\tmp" --specpath "$root\build" "$root\installer.py"
if ($LASTEXITCODE) { throw 'installer build failed' }

Write-Host "OK: $root\dist\YouTrackWidget-Setup.exe"
