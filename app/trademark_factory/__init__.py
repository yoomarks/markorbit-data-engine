"""Reusable trademark jurisdiction factory primitives.

This package contains framework-level building blocks used to create country
packs. Country-specific parsing and legal semantics remain outside this layer.
"""

from app.trademark_factory.profile import (
    CountryProfile,
    SourceProfile,
    SourceTransport,
)

__all__ = [
    "CountryProfile",
    "SourceProfile",
    "SourceTransport",
]
