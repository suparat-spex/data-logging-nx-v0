# Build NX Logger: tests -> EXE (PyInstaller) -> installer (Inno Setup 6).
#   .\packaging\build.ps1                 full build
#   .\packaging\build.ps1 -SkipTests      skip pytest
#   .\packaging\build.ps1 -NoInstaller    EXE folder only
# Needs: pip install -e ".[dev,build]"   and Inno Setup 6 (https://jrsoftware.org/isdl.php) for the installer.
param([switch]$SkipTests, [switch]$NoInstaller)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    $version = python -c "import sys; sys.path.insert(0, 'src'); import nxlogger; print(nxlogger.__version__)"
    Write-Host "NX Logger $version" -ForegroundColor Cyan

    if (-not $SkipTests) {
        python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "tests failed" }
    }

    Remove-Item -Recurse -Force build, "dist\NX Logger" -ErrorAction SilentlyContinue
    python -m PyInstaller packaging\nxlogger.spec --noconfirm --clean --distpath dist --workpath build
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    Write-Host "EXE: dist\NX Logger\NX Logger.exe" -ForegroundColor Green

    if (-not $NoInstaller) {
        $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $iscc) { throw "Inno Setup 6 not found - install it or use -NoInstaller" }
        & $iscc "/DAppVersion=$version" packaging\installer.iss
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        Write-Host "Installer: dist\NX-Logger-Setup-$version.exe" -ForegroundColor Green
    }
}
finally { Pop-Location }
