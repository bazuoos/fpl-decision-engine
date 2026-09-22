#!/usr/bin/env python3
"""Generated-only C2 feasibility harness; never reads predictions or datasets."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import sys
import time

# Callers supply these explicitly in the subprocess environment before import.
# Importing this fixture/runner must never mutate its caller's environment.
THREAD_VARIABLES=('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS')

from fpl_decision_engine.research.c2.contracts import (
    CountDistribution, Forecast, PredictionRecord, PredictionBatch, FixtureOutcome,
    OutcomeRecord, JoinedOutcomes, ResourceGate, canonical_bytes, GOAL_POINTS,
)
from fpl_decision_engine.research.c2.evaluation import evaluate


def synthetic_records(players=100):
    if type(players) is not int or not 1<=players<=700:
        raise ValueError('PLAYER_CEILING')
    rows=[];outcomes=[]
    for season in ('synthetic-a','synthetic-b'):
        for gw in range(2,39):
            for i in range(1,players+1):
                position=('GK','DEF','MID','FWD')[(i-1)%4]
                actual_minutes=90 if i%7 else 30
                goals=(i+gw)%4;assists=(2*i+gw)%3
                u0_minutes=60 if i%7 else 20
                p0=tuple(float(k==u0_minutes) for k in range(91))
                p1=tuple(float(k==actual_minutes) for k in range(91))
                forecasts=[]
                for candidate in ('U0','UM1','UA1','UM1UA1'):
                    minutes=actual_minutes if candidate in ('UM1','UM1UA1') else u0_minutes
                    appearance=float(int(minutes>0)+int(minutes>=60))
                    goal_mean=(i%11+1)/10*minutes/90
                    assist_mean=(i%7+1)/10*minutes/90
                    distributions=[]
                    for mean,rate_mean in zip((goal_mean,assist_mean),((i%11+1)/10,(i%7+1)/10)):
                        if candidate=='UM1':
                            distribution=None
                        elif candidate=='U0':
                            distribution=CountDistribution(kind='POISSON',mean=mean)
                        else:
                            parameters=dict(kind='GAMMA_MIXTURE',mean=mean,weights=(.5,.5),shapes=(rate_mean,10*rate_mean),rates=(1.,10.))
                            distribution=CountDistribution(**parameters,minute_pmf=p1) if candidate=='UM1UA1' else CountDistribution(**parameters,exposure=minutes/90)
                        distributions.append(distribution)
                    forecasts.append(Forecast(candidate=candidate,complete=True,expected_minutes=float(minutes),appearance_points=appearance,
                        goals=goal_mean,assists=assist_mean,modeled_points=appearance+GOAL_POINTS[position]*goal_mean+3*assist_mean,
                        minute_pmf=p1 if candidate in ('UM1','UM1UA1') else p0,appearance_probability=1.,
                        start_probability=1. if candidate in ('UM1','UM1UA1') else None,
                        goal_distribution=distributions[0],assist_distribution=distributions[1]))
                fixture=gw*1000+i
                rows.append(PredictionRecord(season=season,gameweek=gw,element_id=i,player_code='player-'+str(i),position=position,
                    fixture_ids=(fixture,),status='a',chance=100,availability_known=True,chance_target_bound=True,
                    prior_minutes=90*(gw-1),previous_minutes=u0_minutes,previous_reason='OBSERVED',new_entrant=False,promoted=i%2==0,forecasts=tuple(forecasts)))
                outcomes.append(OutcomeRecord(season=season,gameweek=gw,element_id=i,
                    fixtures=(FixtureOutcome(fixture_id=fixture,minutes=actual_minutes,starts=int(actual_minutes==90),goals=goals,assists=assists),)))
    rows=tuple(rows)
    digest=hashlib.sha256(canonical_bytes([r.model_dump(mode='json') for r in rows])).hexdigest()
    prediction=PredictionBatch(authority='SYNTHETIC_ONLY',stage='DEVELOPMENT',rows=rows,prediction_bytes_sha256=digest)
    actual=JoinedOutcomes(authority='GENERATED_SYNTHETIC_OUTCOMES',prediction_bytes_sha256=digest,rows=tuple(outcomes))
    return prediction,actual


def run(players=100):
    if any(os.environ.get(name)!='1' for name in THREAD_VARIABLES):
        raise ValueError('EXPLICIT_SINGLE_THREAD_SUBPROCESS_ENVIRONMENT_REQUIRED')
    print(json.dumps({'progress':'started','pid':os.getpid()}),file=sys.stderr,flush=True)
    predictions,outcomes=synthetic_records(players)
    # Evidence tokens are generated harness declarations, not C3 execution receipts.
    resources=tuple(ResourceGate(candidate=c,status='PASS',scope='SYNTHETIC_C2_EVALUATION',
        evidence_sha256=hashlib.sha256(('synthetic-harness-resource-input:'+c).encode()).hexdigest()) for c in ('UM1','UA1','UM1UA1'))
    runs=[]
    for repeat in range(2):
        started=time.perf_counter()
        result=evaluate(predictions,outcomes,resources)
        raw=canonical_bytes(result)
        runs.append({'wall_seconds':time.perf_counter()-started,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                     'decision':result.decision,'selected':result.selected,
                     'package_statuses':{p.candidate:{'evaluation_status':p.evaluation_status,'failures':list(p.failures),'resource_status':p.resource_status,'membership_sha256':p.membership.sha256 if p.membership else None} for p in result.packages},
                     'evaluated_packages':[p.candidate for p in result.packages if p.evaluation_status!='NOT_EVALUATED'],
                     'cluster_replicates':[{ 'candidate':p.candidate,'system':c.system,'season':c.season,'replicates':c.replicates} for p in result.packages for c in p.clusters]})
        print(json.dumps({'progress':'repeat_complete','repeat':repeat+1,**runs[-1]},sort_keys=True),flush=True,file=sys.stderr)
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak=int(rss if sys.platform=='darwin' else rss*1024)
    return {'identity':'task033c2-synthetic-feasibility-v1','base_scope':'C2_ONLY_SYNTHETIC',
            'dimensions':{'seasons':2,'gameweeks_per_season':37,'players_per_gameweek':players,'prediction_rows':len(predictions.rows),'outcome_rows':len(outcomes.rows),'replicates_per_resampler':9999},
            'environment':{'os':platform.platform(),'architecture':platform.machine(),'python':platform.python_version(),
                 'dependencies':{d.metadata['Name']:d.version for d in importlib.metadata.distributions()},
                 'processes':1,'configured_computational_threads':1,'thread_environment':{k:os.environ[k] for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS')}},
            'runs':runs,'repeat_identical':runs[0]['sha256']==runs[1]['sha256'],
            'peak_rss_bytes':peak,'rss_method':'resource.RUSAGE_SELF.ru_maxrss platform-normalized',
            'within_c2_offline_measured_limits':peak<=2*1024**3 and sum(r['wall_seconds'] for r in runs)<24*3600,
            'not_exercised':['C3 source loading, I/O and immutable publication','real-source feasibility','full historical study','3000 decision candidates','12 operational views','60-second decision budget','decision corpus','prediction-phase six-hour ceiling','production eligibility'],
            'resource_inputs':'Supplied synthetic PASS declarations only; measurement here is separately reported and not a C3 receipt.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--players',type=int,default=100)
    args=parser.parse_args();report=run(args.players);print(json.dumps(report,sort_keys=True,indent=2))
    return 0 if report['repeat_identical'] and report['within_c2_offline_measured_limits'] else 1


if __name__=='__main__':
    raise SystemExit(main())
