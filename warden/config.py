import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load() -> dict:
    with open(ROOT / "warden.toml", "rb") as f:
        return tomllib.load(f)


CFG = load()
