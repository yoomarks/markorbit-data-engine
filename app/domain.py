from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class DiscoveredPackage:
    jurisdiction: str
    path: Path
    file_name: str
    file_size: int
    sha256: str
    modified_at: datetime
