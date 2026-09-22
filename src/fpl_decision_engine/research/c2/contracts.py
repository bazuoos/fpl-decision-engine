"""Strict, in-memory Task033C2 contracts. No source resolution or prediction API."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

Candidate = Literal['U0', 'UM1', 'UA1', 'UM1UA1']
Position = Literal['GK', 'DEF', 'MID', 'FWD']
Number = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
PositiveInt = Annotated[int, Field(ge=1)]
Count = Annotated[int, Field(ge=0)]
GW = Annotated[int, Field(ge=2, le=38)]
Text = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
GOAL_POINTS = {'GK': 10, 'DEF': 6, 'MID': 5, 'FWD': 4}
CANDIDATES = ('U0', 'UM1', 'UA1', 'UM1UA1')
FormulaIdentities = tuple[Literal['xfp_v01'], Literal['UM1-joint-minute-dirichlet-v1'], Literal['UA1-gamma-mixture-loss-update-v1'], Literal['UM1UA1-v1']]
FORMULA_IDENTITIES = ('xfp_v01','UM1-joint-minute-dirichlet-v1','UA1-gamma-mixture-loss-update-v1','UM1UA1-v1')


def canonical_bytes(value) -> bytes:
    """Finite JSON only; infinities are represented by explicit statistic status."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    def check(v):
        if isinstance(v, dict):
            if any(type(k) is not str for k in v):
                raise ValueError('NONSTRING_KEY')
            for x in v.values():
                check(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                check(x)
        elif v is not None and type(v) not in (str, int, float, bool):
            raise ValueError('INVALID_JSON_VALUE')
    check(value)
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')

    @classmethod
    def from_bytes(cls, raw: bytes):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError('DUPLICATE_JSON_KEY')
                result[key] = value
            return result
        def constant(_):
            raise ValueError('NONFINITE_JSON')
        parsed = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        return cls.model_validate_json(canonical_bytes(parsed))


class Identity(Strict):
    season: Text
    gameweek: GW


def membership_bytes(identities: tuple[Identity, ...]) -> bytes:
    if type(identities) is not tuple or any(type(i) is not Identity for i in identities):
        raise ValueError('TYPED_MEMBERSHIP_REQUIRED')
    keys = [(i.season, i.gameweek) for i in identities]
    if len(set(keys)) != len(keys):
        raise ValueError('DUPLICATE_MEMBERSHIP')
    return canonical_bytes([{'season': s, 'gameweek': g} for s, g in sorted(keys)])


class Membership(Strict):
    candidate: Literal['UM1', 'UA1', 'UM1UA1']
    control: Literal['U0'] = 'U0'
    included: tuple[Identity, ...]
    sha256: Digest
    claim: Literal['BYTE_IDENTITY_ONLY'] = 'BYTE_IDENTITY_ONLY'

    @model_validator(mode='after')
    def validate_digest(self):
        raw = membership_bytes(self.included)
        if tuple(sorted(self.included, key=lambda i: (i.season, i.gameweek))) != self.included:
            raise ValueError('NONCANONICAL_MEMBERSHIP')
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError('MEMBERSHIP_DIGEST_MISMATCH')
        return self

    @classmethod
    def create(cls, candidate, identities):
        ordered = tuple(sorted(identities, key=lambda i: (i.season, i.gameweek)))
        return cls(candidate=candidate, included=ordered,
                   sha256=hashlib.sha256(membership_bytes(ordered)).hexdigest())


class CountDistribution(Strict):
    """Already-published Poisson or Gamma-mixture predictive parameters."""
    kind: Literal['POISSON', 'GAMMA_MIXTURE']
    mean: Number
    weights: tuple[Probability, ...] = ()
    shapes: tuple[Annotated[float, Field(gt=0, allow_inf_nan=False)], ...] = ()
    rates: tuple[Annotated[float, Field(gt=0, allow_inf_nan=False)], ...] = ()
    exposure: Number | None = None
    minute_pmf: tuple[Probability, ...] | None = None

    @model_validator(mode='after')
    def coherent(self):
        if self.kind == 'POISSON':
            if self.weights or self.shapes or self.rates or self.exposure is not None or self.minute_pmf is not None:
                raise ValueError('POISSON_EXTRA_PARAMETERS')
        else:
            if not self.weights or not len(self.weights) == len(self.shapes) == len(self.rates):
                raise ValueError('MIXTURE_LENGTH')
            normalized(self.weights)
            if (self.exposure is None) == (self.minute_pmf is None):
                raise ValueError('EXACTLY_ONE_EXPOSURE')
            if self.minute_pmf is not None:
                normalized(self.minute_pmf)
                exposure = math.fsum(i*p/90 for i, p in enumerate(self.minute_pmf))
            else:
                exposure = self.exposure
            close(self.mean, exposure*math.fsum(w*a/b for w, a, b in zip(self.weights, self.shapes, self.rates)))
        return self


def normalized(p):
    if not p or abs(math.fsum(p)-1) > 1e-12:
        raise ValueError('PMF_NORMALIZATION')


def close(a, b):
    if not math.isfinite(a) or not math.isfinite(b) or abs(a-b) > 1e-10:
        raise ValueError('INCONSISTENT_MOMENT')


class Forecast(Strict):
    candidate: Candidate
    complete: bool
    expected_minutes: Number | None
    appearance_points: Number | None
    goals: Number | None
    assists: Number | None
    modeled_points: Number | None
    minute_pmf: tuple[Probability, ...] | None
    appearance_probability: Probability | None
    start_probability: Probability | None
    goal_distribution: CountDistribution | None
    assist_distribution: CountDistribution | None

    @model_validator(mode='after')
    def coherence(self):
        if self.complete and any(x is None for x in (self.expected_minutes, self.appearance_points,
                                                    self.goals, self.assists, self.modeled_points)):
            raise ValueError('FALSE_COMPLETENESS')
        if self.minute_pmf is not None:
            normalized(self.minute_pmf)
            if self.expected_minutes is None or self.appearance_probability is None:
                raise ValueError('MISSING_MINUTE_MOMENT')
            close(self.expected_minutes, math.fsum(i*p for i,p in enumerate(self.minute_pmf)))
            close(self.appearance_probability, 1-self.minute_pmf[0])
        if self.candidate in ('U0', 'UA1') and self.start_probability is not None:
            raise ValueError('START_NOT_DEFINED_FOR_CONTROL_MINUTES')
        if self.candidate == 'UM1' and (self.goal_distribution is not None or self.assist_distribution is not None):
            raise ValueError('UM1_COUNT_DISTRIBUTION_NOT_REGISTERED')
        for count, distribution in ((self.goals,self.goal_distribution),(self.assists,self.assist_distribution)):
            if distribution is not None:
                if count is None:
                    raise ValueError('MISSING_COUNT_MEAN')
                close(count, distribution.mean)
        return self


class PredictionRecord(Strict):
    season: Text
    gameweek: GW
    element_id: PositiveInt
    player_code: Text | None
    position: Position
    fixture_ids: tuple[PositiveInt, ...]
    status: Literal['a','d','i','s','u'] | None
    chance: Annotated[int, Field(ge=0, le=100)] | None
    availability_known: bool
    chance_target_bound: bool
    prior_minutes: Count | None
    previous_minutes: Count | None
    previous_reason: Literal['OBSERVED','BLANK','NEW_ENTRANT','MISSING_CONTEXT']
    new_entrant: bool
    promoted: bool
    forecasts: tuple[Forecast, ...]

    @model_validator(mode='after')
    def coherent(self):
        if tuple(sorted(set(self.fixture_ids))) != self.fixture_ids:
            raise ValueError('FIXTURE_ID_ORDER_OR_DUPLICATE')
        if tuple(f.candidate for f in self.forecasts) != CANDIDATES:
            raise ValueError('EXACT_ORDERED_FOUR_PUBLISHED_FORECASTS_REQUIRED')
        if (self.previous_minutes is not None) != (self.previous_reason == 'OBSERVED'):
            raise ValueError('PREVIOUS_CONTEXT_INCONSISTENT')
        u0,um1,ua1,combined=self.forecasts
        for f in self.forecasts:
            if f.candidate=='U0' and f.minute_pmf is not None and any(p not in (0.,1.) for p in f.minute_pmf):
                raise ValueError('U0_MINUTES_ADAPTER_NOT_POINT_MASS')
            if f.candidate in ('U0','UA1','UM1UA1'):
                for event in ('goal','assist'):
                    distribution=getattr(f,event+'_distribution')
                    mean=getattr(f,event+'s')
                    if mean is not None and distribution is None:
                        raise ValueError('MISSING_REGISTERED_COUNT_DISTRIBUTION')
                    if distribution is not None:
                        if distribution.kind!=('POISSON' if f.candidate=='U0' else 'GAMMA_MIXTURE'):
                            raise ValueError('WRONG_COUNT_DISTRIBUTION_FAMILY')
                        if self.fixture_ids and f.candidate!='U0' and len(distribution.weights)!=2:
                            raise ValueError('REGISTERED_TWO_COMPONENT_MIXTURE_REQUIRED')
                        if f.candidate=='UA1':
                            if distribution.exposure is None or f.expected_minutes is None:
                                raise ValueError('UA1_EXPOSURE_MISSING')
                            close(distribution.exposure,f.expected_minutes/90)
                        if f.candidate=='UM1UA1' and distribution.minute_pmf!=f.minute_pmf:
                            raise ValueError('COMBINED_COUNT_MINUTES_MISMATCH')
            if f.minute_pmf is not None and len(f.minute_pmf) != 90*len(self.fixture_ids)+1:
                raise ValueError('MINUTE_SUPPORT')
            if f.modeled_points is not None and f.appearance_points is not None:
                if f.candidate == 'UM1UA1' and (f.goals is None or f.assists is None):
                    raise ValueError('COMBINATION_HIDES_MISSING_COMPONENT')
                close(f.modeled_points, f.appearance_points+GOAL_POINTS[self.position]*(f.goals or 0)+3*(f.assists or 0))
            if not self.fixture_ids and (not f.complete or any(v != 0 for v in
                    (f.expected_minutes,f.appearance_points,f.goals,f.assists,f.modeled_points,f.appearance_probability))):
                raise ValueError('BLANK_NOT_ZERO')
        for f,source in ((ua1,u0),(combined,um1)):
            for field in ('expected_minutes','appearance_points','minute_pmf','appearance_probability','start_probability'):
                if getattr(f,field)!=getattr(source,field):
                    raise ValueError('FROZEN_MINUTES_COMPONENT_CHANGED')
        for event in ('goal','assist'):
            a=getattr(ua1,event+'_distribution');b=getattr(combined,event+'_distribution')
            if a is not None and b is not None:
                for field in ('weights','shapes','rates'):
                    if getattr(a,field)!=getattr(b,field):
                        raise ValueError('FROZEN_RATE_COMPONENT_CHANGED')
        return self

    @property
    def key(self):
        return self.season, self.gameweek, self.element_id

    def strata(self):
        if not self.availability_known:
            available = 'unknown'
        elif self.status in ('s','u') or (self.chance_target_bound and self.chance == 0):
            available = 'forced-zero'
        elif self.status in ('d','i') or (self.chance_target_bound and self.chance is not None and 0 < self.chance < 100):
            available = 'doubtful/chance-limited'
        elif self.status == 'a':
            available = 'available'
        else:
            available = 'unknown'
        def band(value, edges, labels, missing):
            if value is None:
                return missing
            return labels[sum(value > edge for edge in edges)]
        return {'position':self.position, 'availability':available,
                'prior_minutes':band(self.prior_minutes,(0,90,270,450,900),('0','1-90','91-270','271-450','451-900','901+'),'UNKNOWN'),
                'previous_minutes':band(self.previous_minutes,(0,29,59,89),('0','1-29','30-59','60-89','90+'),'MISSING_CONTEXT'),
                'fixture_count':'blank' if not self.fixture_ids else 'single' if len(self.fixture_ids)==1 else 'double+',
                'universe':'new entrant' if self.new_entrant else 'established', 'promoted':'yes' if self.promoted else 'no'}


class FixtureOutcome(Strict):
    @field_validator('starts', mode='before')
    @classmethod
    def strict_start(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError('START_MUST_BE_INTEGER')
        return value

    fixture_id: PositiveInt
    minutes: Count | None
    starts: Literal[0,1] | None
    goals: Count | None
    assists: Count | None


class OutcomeRecord(Strict):
    season: Text
    gameweek: GW
    element_id: PositiveInt
    fixtures: tuple[FixtureOutcome, ...]

    @property
    def key(self):
        return self.season, self.gameweek, self.element_id

    @model_validator(mode='after')
    def fixtures_unique(self):
        keys = tuple(f.fixture_id for f in self.fixtures)
        if keys != tuple(sorted(set(keys))):
            raise ValueError('OUTCOME_FIXTURE_ORDER_OR_DUPLICATE')
        return self


class PredictionBatch(Strict):
    protocol_identity: Literal['TASK033B1B-candidate-freeze-d6716bc'] = 'TASK033B1B-candidate-freeze-d6716bc'
    clarification_commit: Literal['7874ded703c2173827738d2fc23f1d456826f95a'] = '7874ded703c2173827738d2fc23f1d456826f95a'
    formula_identities: FormulaIdentities = FORMULA_IDENTITIES
    authority: Literal['SYNTHETIC_ONLY']
    stage: Literal['DEVELOPMENT','CONFIRMATION']
    rows: tuple[PredictionRecord, ...]
    prediction_bytes_sha256: Digest

    @model_validator(mode='after')
    def coherent(self):
        keys = tuple(r.key for r in self.rows)
        if not keys or tuple(sorted(set(keys))) != keys:
            raise ValueError('ROW_ORDER_OR_DUPLICATE')
        seasons = sorted({r.season for r in self.rows})
        if len(seasons) != (2 if self.stage == 'DEVELOPMENT' else 1):
            raise ValueError('SEASON_COUNT')
        if any({r.gameweek for r in self.rows if r.season==s} != set(range(2,39)) for s in seasons):
            raise ValueError('EXACT_GAMEWEEKS_2_TO_38_REQUIRED')
        counts={}
        for r in self.rows:
            key=(r.season,r.gameweek);counts[key]=counts.get(key,0)+1
        if any(n>700 for n in counts.values()):
            raise ValueError('PUBLIC_PLAYER_CEILING')
        identities = {}; codes = {}
        for r in self.rows:
            key=(r.season,r.element_id)
            if key in identities and identities[key] != r.player_code:
                raise ValueError('CONFLICTING_PLAYER_IDENTITY')
            identities[key]=r.player_code
            if r.player_code is not None:
                codekey=(r.season,r.player_code)
                if codekey in codes and codes[codekey] != r.element_id:
                    raise ValueError('DUPLICATE_PLAYER_CODE')
                codes[codekey]=r.element_id
        if hashlib.sha256(canonical_bytes([r.model_dump(mode='json') for r in self.rows])).hexdigest() != self.prediction_bytes_sha256:
            raise ValueError('PREDICTION_DIGEST_MISMATCH')
        return self


class JoinedOutcomes(Strict):
    authority: Literal['GENERATED_SYNTHETIC_OUTCOMES']
    prediction_bytes_sha256: Digest
    rows: tuple[OutcomeRecord, ...]

    @model_validator(mode='after')
    def unique(self):
        keys = tuple(r.key for r in self.rows)
        if tuple(sorted(set(keys))) != keys:
            raise ValueError('OUTCOME_ORDER_OR_DUPLICATE')
        return self


class ResourceGate(Strict):
    candidate: Literal['UM1','UA1','UM1UA1']
    status: Literal['PASS','FAIL','NOT_APPLICABLE','MISSING']
    evidence_sha256: Digest | None
    scope: Literal['SYNTHETIC_C2_EVALUATION']

    @model_validator(mode='after')
    def evidence(self):
        if self.status == 'PASS' and self.evidence_sha256 is None:
            raise ValueError('RESOURCE_SUCCESS_WITHOUT_EVIDENCE')
        return self


class Statistic(Strict):
    status: Literal['FINITE','POSITIVE_INFINITY','UNDEFINED','NUMERICAL_FAILURE','NOT_DEFINED_FOR_CONTROL','NOT_APPLICABLE','INSUFFICIENT_EVIDENCE','STRUCTURALLY_IDENTICAL']
    value: Annotated[float, Field(allow_inf_nan=False)] | None
    reason: Text | None = None

    @model_validator(mode='after')
    def consistency(self):
        if (self.status in ('FINITE','STRUCTURALLY_IDENTICAL')) != (self.value is not None):
            raise ValueError('STATISTIC_STATUS_MISMATCH')
        return self

    @classmethod
    def number(cls, value, reason=None):
        if value is None or math.isnan(value):
            return cls(status='UNDEFINED',value=None,reason=reason or 'EMPTY_OR_CONSTANT')
        if value == math.inf:
            return cls(status='POSITIVE_INFINITY',value=None,reason='ZERO_FORECAST_MASS')
        if not math.isfinite(value):
            return cls(status='NUMERICAL_FAILURE',value=None,reason='NONFINITE_RESULT')
        return cls(status='FINITE',value=float(value))
