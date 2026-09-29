"""Test-only explicit row/copy oracle. No C2 metric/inference helpers are used.

Count probabilities use recurrence to 500, independent of the production lgamma
PMF and analytic stopping rule. Ranking explicitly materializes (ID, copy) rows.
"""
import hashlib
import importlib.util
import math
import random
from functools import lru_cache
from pathlib import Path
from fpl_decision_engine.research.c2.contracts import (
    CountDistribution,Forecast,PredictionBatch,JoinedOutcomes,FixtureOutcome,OutcomeRecord,canonical_bytes,
)

POINTS={'GK':10,'DEF':6,'MID':5,'FWD':4}
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('oracle_fixture_runner',ROOT/'scripts/task033c2_synthetic_feasibility.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


def bind(rows,outcomes,stage='DEVELOPMENT'):
    rows=tuple(rows);digest=hashlib.sha256(canonical_bytes([r.model_dump(mode='json') for r in rows])).hexdigest()
    return (PredictionBatch(authority='SYNTHETIC_ONLY',stage=stage,rows=rows,prediction_bytes_sha256=digest),
        JoinedOutcomes(authority='GENERATED_SYNTHETIC_OUTCOMES',rows=tuple(outcomes),prediction_bytes_sha256=digest))


def replace_fixtures(row,fixtures,uncertain=False):
    """Generate coherent forecasts; blanks, one fixture and DGWs share no data I/O."""
    count=len(fixtures);forecasts=[]
    for old in row.forecasts:
        uncertain_side=old.candidate in ('UM1','UM1UA1')
        single=[0.]*91
        if uncertain and uncertain_side:
            single[0]=.1;single[30]=.2;single[90]=.7
        else:
            single[int(old.expected_minutes)]=1.
        pmf=[1.]
        for _ in fixtures:
            convolved=[0.]*(len(pmf)+90)
            for i,p in enumerate(pmf):
                if p:
                    for j,q in enumerate(single):
                        if q:convolved[i+j]+=p*q
            pmf=convolved
        minutes=sum(i*p for i,p in enumerate(pmf));appearance=count*sum((int(i>0)+int(i>=60))*p for i,p in enumerate(single))
        rates=((row.element_id%11+1)/10,(row.element_id%7+1)/10)
        means=[rate*minutes/90 for rate in rates];distributions=[]
        for rate,mean in zip(rates,means):
            if old.candidate=='UM1':dist=None
            elif old.candidate=='U0':dist=CountDistribution(kind='POISSON',mean=mean)
            else:
                parameters=dict(kind='GAMMA_MIXTURE',mean=mean,weights=(.5,.5),shapes=(rate,10*rate),rates=(1.,10.))
                dist=CountDistribution(**parameters,minute_pmf=tuple(pmf)) if uncertain_side else CountDistribution(**parameters,exposure=minutes/90)
            distributions.append(dist)
        forecasts.append(Forecast(candidate=old.candidate,complete=True,expected_minutes=minutes,appearance_points=appearance,
            goals=means[0],assists=means[1],modeled_points=appearance+POINTS[row.position]*means[0]+3*means[1],
            minute_pmf=tuple(pmf),appearance_probability=1.-pmf[0],start_probability=(1.-pmf[0]) if uncertain_side else None,
            goal_distribution=distributions[0],assist_distribution=distributions[1]))
    return row.model_copy(update={'fixture_ids':fixtures,'forecasts':tuple(forecasts)})


def randomized_fixture():
    prediction,_=runner.synthetic_records(100);rng=random.Random(337204);rows=[];outcomes=[]
    for row in prediction.rows:
        blank=row.element_id%23==0 and row.gameweek%7==0
        double=row.element_id%13==0 and row.gameweek%5==0
        fixtures=() if blank else row.fixture_ids+(row.fixture_ids[0]+1000000,) if double else row.fixture_ids
        row=replace_fixtures(row,fixtures,uncertain=True)
        if fixtures and row.element_id%17==0 and row.gameweek%7==0:
            fs=list(row.forecasts);fs[2]=fs[2].model_copy(update={'complete':False});row=row.model_copy(update={'forecasts':tuple(fs)})
        rows.append(row)
        outcomes.append(OutcomeRecord(season=row.season,gameweek=row.gameweek,element_id=row.element_id,
            fixtures=tuple(FixtureOutcome(fixture_id=f,minutes=rng.choice((0,20,59,60,90,97)),starts=rng.randrange(2),goals=rng.randrange(4),assists=rng.randrange(3)) for f in fixtures)))
    return bind(rows,outcomes)


def actual(row,outcome):
    fixtures=outcome.fixtures
    mins=sum(min(f.minutes,90) for f in fixtures);appearance=sum(int(f.minutes>0)+int(f.minutes>=60) for f in fixtures)
    goals=sum(f.goals for f in fixtures);assists=sum(f.assists for f in fixtures)
    return {'minutes':mins,'appearance':appearance,'goal':goals*POINTS[row.position],'assist':3*assists,
        'modeled':appearance+goals*POINTS[row.position]+3*assists,'goals':goals,'assists':assists,'appeared':int(mins>0)}


@lru_cache(maxsize=8192)
def rps(distribution,observed):
    # A long direct recurrence is independent of the production tail algorithm.
    components=[]
    if distribution.kind=='POISSON':components=[(1.,None,distribution.mean)]
    else:
        exposures=[(distribution.exposure,1.)] if distribution.minute_pmf is None else [(i/90,p) for i,p in enumerate(distribution.minute_pmf) if p]
        components=[(mass*w,a,t/(b+t)) for t,mass in exposures for w,a,b in zip(distribution.weights,distribution.shapes,distribution.rates)]
    masses=[0.]*501
    for weight,shape,q in components:
        p=math.exp(-q) if shape is None else (1-q)**shape
        for k in range(501):
            if k:p*=q/k if shape is None else q*(k-1+shape)/k
            masses[k]+=weight*p
    assert abs(sum(masses)-1.)<1e-12
    cdf=0.;loss=0.
    for k,p in enumerate(masses):cdf+=p;loss+=(cdf-int(observed<=k))**2
    return loss


def ranks(values):
    ordered=sorted(values)
    return [1+ordered.index(v)+(ordered.count(v)-1)/2 for v in values]


def rank_metric(records,side,name):
    if not records:return math.nan
    p=[r[1][side].modeled_points for r in records];a=[r[2]['modeled'] for r in records]
    if name=='modeled_spearman':
        x=ranks(p);y=ranks(a);mx=sum(x)/len(x);my=sum(y)/len(y)
        den=math.sqrt(sum((v-mx)**2 for v in x)*sum((v-my)**2 for v in y))
        return sum((v-mx)*(w-my) for v,w in zip(x,y))/den if den else math.nan
    n=int(name.split('top')[1])
    if len(records)<n:return math.nan
    predicted=sorted(range(len(records)),key=lambda i:(-p[i],records[i][0].element_id,records[i][3]))[:n]
    realized=sorted(range(len(records)),key=lambda i:(-a[i],records[i][0].element_id,records[i][3]))[:n]
    return len(set(predicted)&set(realized))/n


class NaiveOracle:
    def __init__(self,predictions,outcomes,candidate):
        c=('U0','UM1','UA1','UM1UA1').index(candidate);joined={o.key:o for o in outcomes.rows}
        self.records=[(row,(row.forecasts[c],row.forecasts[0]),actual(row,joined[row.key]),0) for row in predictions.rows if row.fixture_ids]
        self.seasons=sorted({r[0].season for r in self.records});self.eligible=set()
        for s in self.seasons:
            for g in range(2,39):
                common=[r for r in self.records if (r[0].season,r[0].gameweek)==(s,g) and all(f.complete for f in r[1])]
                if len(common)>=50 and len({r[2]['modeled'] for r in common})>1 and all(len({r[1][side].modeled_points for r in common})>1 for side in (0,1)):
                    self.eligible.add((s,g))

    def difference(self,endpoint,pm,gm):
        seasons=self.seasons if endpoint.season=='AGGREGATE' else [endpoint.season];answers=[]
        for season in seasons:
            rows=[r for r in self.records if r[0].season==season and (endpoint.group=='all' or endpoint.group=='position:'+r[0].position)]
            name=endpoint.metric
            if name.startswith('modeled_top') or name=='modeled_spearman':
                values=[];gw_weights=[]
                for s,g in sorted(self.eligible):
                    if s!=season or not gm[s][g]:continue
                    copies=[(r[0],r[1],r[2],copy) for r in rows if r[0].gameweek==g and all(f.complete for f in r[1]) for copy in range(pm[s].get(r[0].element_id,0))]
                    values.append(rank_metric(copies,1,name)-rank_metric(copies,0,name));gw_weights.append(gm[s][g])
                answers.append(sum(v*w for v,w in zip(values,gw_weights))/sum(gw_weights) if gw_weights else math.nan);continue
            copies=[r for r in rows for _ in range(pm[season].get(r[0].element_id,0)*gm[season][r[0].gameweek])]
            if name.startswith('modeled_'):copies=[r for r in copies if all(f.complete for f in r[1])]
            if not copies:answers.append(math.nan);continue
            def measure(side):
                if name=='coverage':return sum(r[1][side].complete for r in copies)/len(copies)
                if name=='appearance_ece':
                    total=0.
                    for b in range(10):
                        selected=[r for r in copies if min(9,int(r[1][side].appearance_probability*10))==b]
                        if selected:total+=abs(sum(r[1][side].appearance_probability-r[2]['appeared'] for r in selected))
                    return total/len(copies)
                losses=[]
                for row,fs,y,_ in copies:
                    f=fs[side]
                    if name=='brier':
                        band=0 if y['minutes']==0 else 1 if y['minutes']<30 else 2 if y['minutes']<60 else 3 if y['minutes']<90 else 4
                        masses=[0.]*5
                        for minutes,p in enumerate(f.minute_pmf):
                            index=0 if minutes==0 else 1 if minutes<30 else 2 if minutes<60 else 3 if minutes<90 else 4
                            masses[index]+=p
                        loss=sum((p-int(i==band))**2 for i,p in enumerate(masses))
                    elif name in ('goal_rps','assist_rps','mean_rps'):
                        events=('goal','assist') if name=='mean_rps' else (name.split('_')[0],)
                        loss=sum(rps(getattr(f,e+'_distribution'),y[e+'s']) for e in events)/len(events)
                    else:
                        component=name.split('_')[0]
                        prediction={'minutes':f.expected_minutes,'appearance':f.appearance_points,'goal':f.goals*POINTS[row.position],'assist':f.assists*3,'modeled':f.modeled_points}[component]
                        delta=prediction-y[component];loss=abs(delta) if name.endswith('_mae') else delta**2 if name.endswith('_rmse') else delta
                    losses.append(loss)
                mean=sum(losses)/len(losses)
                return math.sqrt(mean) if name.endswith('_rmse') else abs(mean) if name.endswith('_absolute_bias') else mean
            a=measure(0);b=measure(1);answers.append(b-a if name=='coverage' else a-b)
        return sum(answers)/len(answers)
