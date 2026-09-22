"""Task033C2 pure, synthetic-only research evaluation. No production authority."""
from .contracts import (
    CountDistribution, Forecast, PredictionRecord, PredictionBatch, FixtureOutcome,
    OutcomeRecord, JoinedOutcomes, ResourceGate, Membership, Identity, Statistic,
    canonical_bytes, membership_bytes,
)
from .evaluation import evaluate
from .results import EvaluationResult

__all__ = [
    'CountDistribution','Forecast','PredictionRecord','PredictionBatch','FixtureOutcome',
    'OutcomeRecord','JoinedOutcomes','ResourceGate','Membership','Identity','Statistic',
    'canonical_bytes','membership_bytes','evaluate','EvaluationResult',
]
