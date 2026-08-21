"""Country-native trademark store foundations.

The package deliberately does not force country sources into one lossy common
schema.  Each jurisdiction keeps source-native facts and can later project the
small cross-jurisdiction subset needed by Entity Hub, search, and MO Brain.
"""

from app.global_trademarks.catalog import COUNTRY_SOURCES, CountrySourcePlan

__all__ = ["COUNTRY_SOURCES", "CountrySourcePlan"]
