from pathlib import Path


SCHEMA_SQL = (
    Path(__file__).parents[2]
    / "database"
    / "postgres"
    / "init"
    / "019_cnipa_judgment_source_fact.sql"
).read_text(encoding="utf-8")
