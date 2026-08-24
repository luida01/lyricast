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

$nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($null -ne $nvidia) {
    Write-Host "NVIDIA GPU detected. Installing CUDA-enabled PyTorch..."
    & $pythonPath -m pip install --upgrade --index-url https://download.pytorch.org/whl/cu128 torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0
} else {
    Write-Host "No NVIDIA GPU detected. Keeping the CPU PyTorch installation."
}

Write-Host "Python pipeline setup complete."
