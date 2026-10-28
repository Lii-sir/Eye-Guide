param(
    [switch]$BuildInstaller
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SpecFile = Join-Path $ProjectRoot "packaging\eyeguide.spec"
$DistDir = Join-Path $ProjectRoot "dist"
$BuildDir = Join-Path $ProjectRoot "build"
$InstallerScript = Join-Path $ProjectRoot "packaging\EyeGuide.iss"

function Find-IsccExe {
    if ($env:ISCC_EXE -and (Test-Path $env:ISCC_EXE)) {
        return $env:ISCC_EXE
    }

    if ($env:INNO_SETUP_HOME) {
        $fromHome = Join-Path $env:INNO_SETUP_HOME "ISCC.exe"
        if (Test-Path $fromHome) {
            return $fromHome
        }
    }

    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:LOCALAPPDATA} "Programs\Inno Setup 6\ISCC.exe")
    )

    $driveRoots = Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Root }
    foreach ($root in $driveRoots) {
        $candidates += (Join-Path $root "Inno Setup 6\ISCC.exe")
    }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    return $null
}

if (-not (Test-Path $PythonExe)) {
    throw "Virtualenv Python not found: $PythonExe"
}

Push-Location $ProjectRoot
try {
    Write-Host "Checking PyInstaller..."
    & $PythonExe -c "import PyInstaller; print(PyInstaller.__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is not installed. Run: uv sync --extra package"
    }

    if (Test-Path $DistDir) {
        Remove-Item -LiteralPath $DistDir -Recurse -Force
    }
    if (Test-Path $BuildDir) {
        Remove-Item -LiteralPath $BuildDir -Recurse -Force
    }

    Write-Host "Building EyeGuide with PyInstaller..."
    & $PythonExe -m PyInstaller --noconfirm --clean $SpecFile

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }

    Write-Host ("Build complete: " + (Join-Path $DistDir "EyeGuide"))

    if ($BuildInstaller) {
        $IsccExe = Find-IsccExe
        if (-not $IsccExe) {
            throw "ISCC.exe not found. Please install Inno Setup 6 first."
        }

        Write-Host "Building installer..."
        & $IsccExe $InstallerScript
        if ($LASTEXITCODE -ne 0) {
            throw "Installer build failed."
        }
    }
}
finally {
    Pop-Location
}
