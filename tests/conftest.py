from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text(fixtures_dir: Path):
    def read(name: str) -> str:
        return (fixtures_dir / name).read_text(encoding="utf-8")

    return read

