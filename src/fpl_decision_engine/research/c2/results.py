"""Closed result records. No bare NaN/Infinity and no unevaluated memberships."""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from .contracts import Strict, Statistic, Membership, Identity, Digest, Text, Count, Number, FormulaIdentities, FORMULA_IDENTITIES


class ExcludedGameweek(Strict):
    identity: Identity
    common_players: Count
    reasons: tuple[Literal['FEWER_THAN_50_COMMON_PLAYERS','CONSTANT_ACTUAL','CONSTANT_CANDIDATE','CONSTANT_CONTROL'], ...]


class Population(Strict):
    season: Text
    group: Text
    universe: Count
    common: Count
    players: Count
    gameweeks: Count
    candidate_complete: Count
    control_complete: Count
    actual_complete: Count
    missing_outcome: Count
    missing_start: Count
    missing_goal: Count
    missing_assist: Count
    blanks: Count
    universe_including_blanks: Count
    candidate_complete_including_blanks: Count
    control_complete_including_blanks: Count
    actual_complete_including_blanks: Count
    common_membership_sha256: Digest
    overflow_count: Count
    status: Literal['PASS','NOT_APPLICABLE','INSUFFICIENT_EVIDENCE']

    @model_validator(mode='after')
    def consistent_population(self):
        from .inference import population_gate
        if self.common>min(self.candidate_complete,self.control_complete,self.actual_complete) or max(self.candidate_complete,self.control_complete,self.actual_complete)>self.universe:
            raise ValueError('INCONSISTENT_POPULATION_COUNTS')
        if self.players>self.common or self.gameweeks>self.common or self.universe_including_blanks!=self.universe+self.blanks:
            raise ValueError('INCONSISTENT_POPULATION_CLUSTERS')
        expected=population_gate(**{k:getattr(self,k) for k in ('universe','common','players','gameweeks','candidate_complete','control_complete','actual_complete')},
            subgroup=self.group!='all',availability=self.group.startswith('availability:') and self.universe==0)
        if expected!=self.status:
            raise ValueError('POPULATION_STATUS_MISMATCH')
        return self


class Metric(Strict):
    season: Text
    group: Text
    name: Text
    candidate_natural: Statistic
    control_natural: Statistic
    candidate_common: Statistic
    control_common: Statistic
    adverse_difference: Statistic
    common_rows: Count
    candidate_rows: Count
    control_rows: Count
    candidate_infinities: Count
    control_infinities: Count


class Bound(Strict):
    system: Literal['crossed','serial']
    status: Literal['PASS','FAIL','NOT_APPLICABLE','INSUFFICIENT_EVIDENCE','NUMERICAL_FAILURE','UNDEFINED','STRUCTURALLY_IDENTICAL']
    upper: Statistic
    family_q: Statistic
    percentile_low: Statistic
    percentile_high: Statistic
    passed: bool


class GateEndpoint(Strict):
    family: Literal['global','subgroup']
    group: Text
    season: Text
    metric: Text
    scale: Number
    threshold: float = Field(allow_inf_nan=False)
    improvement: bool
    observed_adverse: Statistic
    population_status: Literal['PASS','NOT_APPLICABLE','INSUFFICIENT_EVIDENCE','NUMERICAL_FAILURE']
    crossed: Bound
    serial: Bound
    passed: bool

    @model_validator(mode='after')
    def gate_consistency(self):
        if self.scale<=0:
            raise ValueError('NONPOSITIVE_SCALE')
        for bound in (self.crossed,self.serial):
            expected=(bound.status=='NOT_APPLICABLE') or (bound.upper.value is not None and bound.upper.value<self.threshold)
            if bound.passed!=expected:
                raise ValueError('BOUND_BOOLEAN_MISMATCH')
            if (self.population_status=='NOT_APPLICABLE')!=(bound.status=='NOT_APPLICABLE'):
                raise ValueError('APPLICABILITY_MISMATCH')
        if self.passed!=(self.crossed.passed and self.serial.passed):
            raise ValueError('BOTH_RESAMPLERS_REQUIRED')
        return self


class ClusterSummary(Strict):
    system: Literal['crossed','serial']
    season: Text
    observed_players: Count
    observed_gameweeks: Count
    retained_players: tuple[Count,Count,Count]
    retained_gameweeks: tuple[Count,Count,Count]
    replicates: Literal[9999]


class CalibrationBin(Strict):
    season: Text
    gameweek: int | None
    group: Text
    candidate: Literal['U0','UM1','UA1','UM1UA1']
    event: Text
    bin: int = Field(ge=0,le=9)
    weight: Number
    forecast: Statistic
    observed: Statistic


class SerialDiagnostic(Strict):
    season: Text
    primary: Literal['brier','mean_rps']
    lag1: Statistic
    lag2: Statistic


class DiagnosticInterval(Strict):
    system: Literal['crossed','serial']
    season: Text
    group: Text
    metric: Text
    status: Literal['FINITE','UNDEFINED','INSUFFICIENT_EVIDENCE','POSITIVE_INFINITY']
    low: Statistic
    high: Statistic


class PackageResult(Strict):
    candidate: Literal['UM1','UA1','UM1UA1']
    control: Literal['U0'] = 'U0'
    evaluation_status: Literal['PASS','FAIL','NOT_EVALUATED']
    membership: Membership | None
    excluded_gameweeks: tuple[ExcludedGameweek,...] = ()
    populations: tuple[Population,...] = ()
    metrics: tuple[Metric,...] = ()
    calibration: tuple[CalibrationBin,...] = ()
    endpoints: tuple[GateEndpoint,...] = ()
    clusters: tuple[ClusterSummary,...] = ()
    diagnostic_intervals: tuple[DiagnosticInterval,...] = ()
    resampled_calibration_sha256: tuple[Digest,...] = ()
    serial_diagnostics: tuple[SerialDiagnostic,...] = ()
    failures: tuple[Text,...] = ()
    resource_status: Literal['PASS','FAIL','MISSING','NOT_APPLICABLE','NOT_EVALUATED']
    resource_evidence_sha256: Digest | None = None
    reserved_alpha: Literal['1/60'] = '1/60'
    global_alpha: Literal['1/120'] = '1/120'
    subgroup_alpha: Literal['1/120'] = '1/120'

    @model_validator(mode='after')
    def coherent(self):
        if self.evaluation_status == 'NOT_EVALUATED':
            if self.membership is not None or self.resource_evidence_sha256 is not None or any((self.excluded_gameweeks,self.populations,self.metrics,self.calibration,
                    self.endpoints,self.clusters,self.diagnostic_intervals,self.resampled_calibration_sha256,self.serial_diagnostics,self.failures)) or self.resource_status != 'NOT_EVALUATED':
                raise ValueError('UNEVALUATED_PACKAGE_HAS_RESULTS')
        else:
            if self.membership is None or self.membership.candidate != self.candidate:
                raise ValueError('PAIR_MEMBERSHIP_MISSING_OR_MISMATCH')
            if self.resource_status=='PASS' and self.resource_evidence_sha256 is None:
                raise ValueError('MISSING_RESOURCE_EVIDENCE_BINDING')
            if not self.endpoints:
                raise ValueError('MISSING_REQUIRED_ENDPOINTS')
            from .inference import endpoint_family
            seasons=tuple(sorted({p.season for p in self.populations}))
            expected_family=endpoint_family(self.candidate,seasons)
            fields=('family','group','season','metric','scale','threshold','improvement')
            if [tuple(getattr(e,k) for k in fields) for e in self.endpoints]!=[tuple(getattr(e,k) for k in fields) for e in expected_family]:
                raise ValueError('INCOMPLETE_OR_CHANGED_ENDPOINT_FAMILY')
            for collection,fields in ((self.populations,('season','group')),(self.metrics,('season','group','name')),(self.clusters,('system','season'))):
                keys=[tuple(getattr(item,k) for k in fields) for item in collection]
                if len(set(keys))!=len(keys):
                    raise ValueError('DUPLICATE_RESULT_IDENTITY')
            expected=self.resource_status=='PASS' and not self.failures and all(e.passed for e in self.endpoints)
            if (self.evaluation_status=='PASS') != expected:
                raise ValueError('INCONSISTENT_PACKAGE_PASS')
        return self


class EvaluationResult(Strict):
    contract: Literal['task033c2-evaluation-v1'] = 'task033c2-evaluation-v1'
    protocol_identity: Literal['TASK033B1B-candidate-freeze-d6716bc'] = 'TASK033B1B-candidate-freeze-d6716bc'
    clarification_commit: Literal['7874ded703c2173827738d2fc23f1d456826f95a'] = '7874ded703c2173827738d2fc23f1d456826f95a'
    formula_identities: FormulaIdentities = FORMULA_IDENTITIES
    authority: Literal['SYNTHETIC_ONLY'] = 'SYNTHETIC_ONLY'
    stage: Literal['DEVELOPMENT','CONFIRMATION']
    prediction_sha256: Digest
    outcomes_sha256: Digest
    packages: tuple[PackageResult,...]
    selected: Literal['UM1','UA1','UM1UA1'] | None
    decision: Literal['DO_NOT_CONFIRM','AWAITING_AUTHORIZED_CONFIRMATION','DO_NOT_PROMOTE','SYNTHETIC_CONFIRMATION_PASS']
    decision_corpus: Literal['NOT_EVALUATED'] = 'NOT_EVALUATED'
    maximum_eligibility: Literal['ELIGIBLE_FOR_TASK033D_COMPONENT_DIAGNOSTICS'] = 'ELIGIBLE_FOR_TASK033D_COMPONENT_DIAGNOSTICS'
    eligibility_established: Literal[False] = False
    source_validity_established: Literal[False] = False
    historical_immutability_established: Literal[False] = False
    production_suitability_established: Literal[False] = False
    better_fpl_decisions_established: Literal[False] = False
    hash_claim: Literal['BYTE_IDENTITY_ONLY'] = 'BYTE_IDENTITY_ONLY'

    @model_validator(mode='after')
    def coherent(self):
        if tuple(p.candidate for p in self.packages) != ('UM1','UA1','UM1UA1'):
            raise ValueError('EXACT_ORDERED_PACKAGE_RESULTS_REQUIRED')
        if self.stage=='DEVELOPMENT':
            from .inference import conditional_choice
            a,b,c=self.packages
            if a.evaluation_status=='NOT_EVALUATED' or b.evaluation_status=='NOT_EVALUATED':
                raise ValueError('COMPONENT_NOT_EVALUATED')
            both=a.evaluation_status==b.evaluation_status=='PASS'
            if both == (c.evaluation_status=='NOT_EVALUATED'):
                raise ValueError('COMBINATION_AUTHORIZATION_MISMATCH')
            choice=conditional_choice(a.evaluation_status=='PASS',b.evaluation_status=='PASS',
                                      c.evaluation_status=='PASS' if both else None)
            if choice!=self.selected or self.decision!=('AWAITING_AUTHORIZED_CONFIRMATION' if choice else 'DO_NOT_CONFIRM'):
                raise ValueError('SELECTION_MISMATCH')
        else:
            evaluated=[p for p in self.packages if p.evaluation_status!='NOT_EVALUATED']
            if len(evaluated)!=1 or evaluated[0].candidate!=self.selected:
                raise ValueError('CONFIRMATION_MUST_EVALUATE_ONLY_SELECTED_PACKAGE')
            if self.decision!=('SYNTHETIC_CONFIRMATION_PASS' if evaluated[0].evaluation_status=='PASS' else 'DO_NOT_PROMOTE'):
                raise ValueError('CONFIRMATION_DECISION_MISMATCH')
        return self
