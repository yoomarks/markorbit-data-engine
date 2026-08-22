"""Generate a safe skeleton for a new trademark jurisdiction.

Generation creates development scaffolding only. It never marks a country as
ready or production-current.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScaffoldPlan:
    jurisdiction: str
    files: tuple[str, ...]


DEFAULT_FILES = (
    "country.yaml",
    "source.yaml",
    "identity.py",
    "acquisition.py",
    "parser.py",
    "mapping.py",
    "schema.py",
    "current.py",
    "assets.py",
    "acceptance.py",
    "fixtures/.gitkeep",
)


def build_plan(jurisdiction: str) -> ScaffoldPlan:
    code = jurisdiction.lower()
    return ScaffoldPlan(
        jurisdiction=code,
        files=tuple(f"jurisdictions/{code}/{name}" for name in DEFAULT_FILES),
    )


def write_plan(root: Path, plan: ScaffoldPlan) -> tuple[Path, ...]:
    created: list[Path] = []
    for relative in plan.files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(path)
        path.write_text("# generated scaffold\n", encoding="utf-8")
        created.append(path)
    return tuple(created)
