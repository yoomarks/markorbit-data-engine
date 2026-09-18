"""CNIPA judgment LIST structured source facts."""

from .model import CnipaJudgmentListFact, CnipaJudgmentWindowObservation
from .parser import parse_cnipa_list_fact_projection
from .repository import ingest_cnipa_judgment_window

__all__ = [
    "CnipaJudgmentListFact",
    "CnipaJudgmentWindowObservation",
    "ingest_cnipa_judgment_window",
    "parse_cnipa_list_fact_projection",
]
