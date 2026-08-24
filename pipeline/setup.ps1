$ErrorActionPreference = "Stop"

$venvPath = Join-Path $PSScriptRoot ".venv"
$pythonPath = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPath)) {
    Write-Host "Creating Python virtual environment..."
    python -m venv $venvPath
}

Write-Host "Upgrading pip..."
& $pythonPath -m pip install --upgrade pip

Write-Host "Installing Python pipeline dependencies..."
& $pythonPath -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")

Write-Host "Python pipeline setup complete."
