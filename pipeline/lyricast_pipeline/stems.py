from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def separate_audio(input_path: Path, output_directory: Path, model: str = "htdemucs") -> tuple[Path, Path, Path]:
    stems_directory = output_directory / ".stems"
    stems_directory.mkdir(parents=True, exist_ok=True)
    try:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    command = [
        sys.executable,
        "-m",
        "demucs.separate",
        "--two-stems=vocals",
        "-n",
        model,
        "--device",
        device,
        "-o",
        str(stems_directory),
        str(input_path),
    ]
    subprocess.run(command, check=True)

    vocal_candidates = list(stems_directory.rglob("vocals.wav"))
    instrumental_candidates = list(stems_directory.rglob("no_vocals.wav"))
    if not vocal_candidates or not instrumental_candidates:
        raise RuntimeError("Demucs completed but did not produce vocals.wav and no_vocals.wav")

    vocals_path = output_directory / "vocals.wav"
    instrumental_path = output_directory / "instrumental.wav"
    shutil.copy2(vocal_candidates[0], vocals_path)
    shutil.copy2(instrumental_candidates[0], instrumental_path)
    return vocals_path, instrumental_path, stems_directory
