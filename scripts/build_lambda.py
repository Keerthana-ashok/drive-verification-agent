"""Package the Lambda code and its Python libraries into build/lambda.

Downloads Linux (x86_64, Python 3.12) wheels so it works from a Mac without Docker.
Usage: python scripts/build_lambda.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "lambda"


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "app" / "requirements.txt"),
         "--target", str(OUT), "--platform", "manylinux2014_x86_64", "--implementation", "cp",
         "--python-version", "3.12", "--only-binary=:all:", "--upgrade"],
        check=True,
    )
    shutil.copytree(ROOT / "app", OUT / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "requirements.txt"))
    size_mb = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1e6
    print(f"Built {OUT.relative_to(ROOT)} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
