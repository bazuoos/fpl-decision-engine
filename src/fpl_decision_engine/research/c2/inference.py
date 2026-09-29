"""Frozen random streams, endpoint families, population gates and basic bounds."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

REPLICATES=9999
SEEDS={'crossed':{'DEVELOPMENT':330331,'CONFIRMATION':330332},
       'serial':{'DEVELOPMENT':330431,'CONFIRMATION':330432}}
POSITIONS=('GK','DEF','MID','FWD')
AVAILABILITY=('forced-zero','doubtful/chance-limited','available','unknown')
SCALES={'brier':.01,'mean_rps':.005,'minutes_mae':1.,'minutes_rmse':2.,
        'appearance_mae':.02,'appearance_ece':.02,'goal_rps':.005,'assist_rps':.005,
        'goal_mae':.02,'assist_mae':.02,'modeled_mae':.03,'modeled_rmse':.05,
        'modeled_absolute_bias':.02,'modeled_spearman':.01,
        'modeled_top10':.02,'modeled_top25':.02,'modeled_top50':.02,'coverage':.01}
COMMON=('modeled_mae','modeled_rmse','modeled_absolute_bias','modeled_spearman',
        'modeled_top10','modeled_top25','modeled_top50','coverage')
MINUTES=('brier','minutes_mae','minutes_rmse','appearance_mae','appearance_ece')
COUNTS=('mean_rps','goal_rps','assist_rps','goal_mae','assist_mae')
SUBGROUP_SCALES={'modeled_mae':.05,'modeled_rmse':.1,'modeled_absolute_bias':.05,'coverage':.01}


@dataclass(frozen=True)
class Endpoint:
    family: str
    group: str
    season: str
    metric: str
    scale: float
    threshold: float
    improvement: bool


def endpoint_family(candidate, seasons):
    if candidate not in ('UM1','UA1','UM1UA1'):
        raise ValueError('UNREGISTERED_CANDIDATE')
    metrics=COMMON+(MINUTES if candidate in ('UM1','UM1UA1') else ())+(COUNTS if candidate in ('UA1','UM1UA1') else ())
    endpoints=[]
    for season in ('AGGREGATE',*seasons):
        for metric in metrics:
            improvement=season=='AGGREGATE' and metric in ('brier','mean_rps')
            scale=SCALES[metric]
            endpoints.append(Endpoint('global','all',season,metric,scale,-scale if improvement else scale,improvement))
        for group in tuple('position:'+s for s in POSITIONS)+tuple('availability:'+s for s in AVAILABILITY):
            for metric,scale in SUBGROUP_SCALES.items():
                endpoints.append(Endpoint('subgroup',group,season,metric,scale,scale,False))
    return tuple(endpoints)


def population_gate(*, universe, common, players, gameweeks, candidate_complete,
                    control_complete, actual_complete, subgroup=False, availability=False):
    if universe==0 and subgroup and availability:
        return 'NOT_APPLICABLE'
    if not universe:
        return 'INSUFFICIENT_EVIDENCE'
    required=(200,20,20) if subgroup else (1000,100,30)
    if common<required[0] or players<required[1] or gameweeks<required[2]:
        return 'INSUFFICIENT_EVIDENCE'
    # Integer cross multiplication preserves literal inclusive coverage cutoffs.
    if common*100<90*universe or actual_complete*100<99*universe:
        return 'INSUFFICIENT_EVIDENCE'
    if not subgroup and (candidate_complete*100<95*universe or control_complete*100<95*universe):
        return 'INSUFFICIENT_EVIDENCE'
    return 'PASS'


def resamples(rows, stage, system):
    """Yield all 9,999 draws in the exact reference order, never metric-local RNG."""
    if system not in SEEDS or stage not in SEEDS[system]:
        raise ValueError('UNREGISTERED_RESAMPLER')
    rows=tuple(rows)
    seasons=tuple(sorted({r.season for r in rows}))
    registered={s:tuple(r for r in rows if r.season==s and r.fixture_ids) for s in seasons}
    players={s:tuple(sorted({r.element_id for r in registered[s]})) for s in seasons}
    gameweeks={s:tuple(sorted({r.gameweek for r in registered[s]})) for s in seasons}
    if any(not players[s] or not gameweeks[s] for s in seasons):
        raise ValueError('EMPTY_BOOTSTRAP_UNIVERSE')
    if system=='serial' and any(type(r.player_code) is not str or not r.player_code for r in rows):
        raise ValueError('MISSING_IDENTITY_BRIDGE')
    # The serial union includes verified codes represented only by blank rows.
    codes=tuple(sorted({r.player_code for r in rows if r.player_code is not None},key=lambda c:c.encode('utf-8')))
    rng=np.random.Generator(np.random.PCG64(SEEDS[system][stage]))
    for _ in range(REPLICATES):
        pm={};gm={}
        if system=='serial':
            shared=np.bincount(rng.integers(0,len(codes),size=len(codes),dtype=np.int64),minlength=len(codes))
            code_counts=dict(zip(codes,shared))
        for s in seasons:
            if system=='crossed':
                p=np.bincount(rng.integers(0,len(players[s]),size=len(players[s]),dtype=np.int64),minlength=len(players[s]))
                g=np.bincount(rng.integers(0,len(gameweeks[s]),size=len(gameweeks[s]),dtype=np.int64),minlength=len(gameweeks[s]))
                pm[s]=dict(zip(players[s],p));gm[s]=dict(zip(gameweeks[s],g))
            else:
                starts=rng.integers(0,34,size=10,dtype=np.int64)+2
                expanded=(starts[:,None]+np.arange(4)).reshape(-1)[:37]
                counts=np.bincount(expanded,minlength=39)
                pm[s]={r.element_id:code_counts[r.player_code] for r in rows if r.season==s}
                gm[s]={g:int(counts[g]) for g in range(2,39)}
        yield pm,gm


def simultaneous_bounds(observed, replicates, scales):
    observed=np.asarray(observed,dtype=float);replicates=np.asarray(replicates,dtype=float);scales=np.asarray(scales,dtype=float)
    if replicates.shape != (REPLICATES,len(observed)) or len(scales)!=len(observed):
        raise ValueError('EXACT_REPLICATE_COUNT_REQUIRED')
    if not (np.isfinite(observed).all() and np.isfinite(replicates).all() and np.isfinite(scales).all() and (scales>0).all()):
        raise ValueError('UNDEFINED_REQUIRED_STATISTIC_OR_REPLICATE')
    if not len(observed):
        return np.array([]),0.
    maxima=np.max((observed[None,:]-replicates)/scales[None,:],axis=1)
    q=max(0.,float(np.sort(maxima)[9916]))
    return observed+q*scales,q


def percentile_interval(replicates):
    x=np.asarray(replicates,dtype=float)
    if x.shape!=(REPLICATES,) or not np.isfinite(x).all():
        return None
    ordered=np.sort(x)
    return float(ordered[249]),float(ordered[9749])


def strict_pass(upper, threshold):
    return bool(math.isfinite(upper) and upper < threshold)


def conditional_choice(um1,ua1,combined=None):
    if type(um1) is not bool or type(ua1) is not bool or (combined is not None and type(combined) is not bool):
        raise ValueError('STRICT_GATE_BOOLEAN_REQUIRED')
    if combined is not None and not (um1 and ua1):
        raise ValueError('COMBINATION_NOT_AUTHORIZED')
    if um1 and ua1:
        if combined is None:
            raise ValueError('CONDITIONAL_COMBINATION_RESULT_REQUIRED')
        return 'UM1UA1' if combined else 'UM1'
    return 'UM1' if um1 else 'UA1' if ua1 else None
