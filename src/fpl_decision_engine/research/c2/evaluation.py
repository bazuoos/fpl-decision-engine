"""Pure research evaluation of separately supplied frozen predictions/outcomes.

No file, database, network, clock, environment, prediction construction or source
resolution API. Resource evidence is supplied; synthetic labels are declarations,
not source attestation. The public orchestrator owns conditional combination access.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np

from .contracts import (PredictionBatch, JoinedOutcomes, ResourceGate, Identity, Membership,
                        Statistic, canonical_bytes, CANDIDATES)
from .inference import (endpoint_family, population_gate, resamples, simultaneous_bounds,
                        percentile_interval, strict_pass, conditional_choice, REPLICATES)
from .metrics import (actual_values, row_metrics, weighted_mean, calibration, spearman,
                      top_overlap, count_deciles, autocorrelation, RankingPlan)
from .results import (EvaluationResult, PackageResult, Population, Metric, Bound, GateEndpoint,
                      ExcludedGameweek, ClusterSummary, CalibrationBin, SerialDiagnostic, DiagnosticInterval)

ERRORS=('minutes','raw_minutes','appearance','goal','assist','attacking','modeled')
SCALAR=('brier','minutes_log_loss','goal_rps','assist_rps','mean_rps','goal_log_loss','assist_log_loss',
        'minutes_interval_coverage','minutes_interval_width','goal_interval_coverage','goal_interval_width',
        'assist_interval_coverage','assist_interval_width','goal_tail_bound','assist_tail_bound')
METRICS=tuple(c+'_'+m for c in ERRORS for m in ('mae','rmse','bias','absolute_bias'))+SCALAR+('appearance_ece','start_ece','appearance_binary_brier','start_binary_brier')
COMPONENT_RANKING=('goal_spearman','assist_spearman','attacking_spearman')
RANKING=('modeled_spearman','modeled_top10','modeled_top25','modeled_top50')


def _stat(value, reason=None):
    return Statistic.number(value,reason)


def _not_applicable():
    return Statistic(status='NOT_APPLICABLE',value=None,reason='STRUCTURALLY_ABSENT_AVAILABILITY_GROUP')


_COMBINATION_AUTHORITY=object()


class _Pair:
    def __init__(self, predictions, outcomes, candidate, authorization=None):
        if candidate=='UM1UA1' and authorization is not _COMBINATION_AUTHORITY:
            raise ValueError('COMBINATION_NOT_AUTHORIZED')
        self.candidate=candidate;self.stage=predictions.stage;self.rows=predictions.rows
        self.seasons=tuple(sorted({r.season for r in self.rows}));self.n=len(self.rows)
        ci=CANDIDATES.index(candidate)
        self.forecasts=tuple(r.forecasts[ci] for r in self.rows);self.controls=tuple(r.forecasts[0] for r in self.rows)
        joined={r.key:r for r in outcomes.rows}
        self.actual=[actual_values(r,joined.get(r.key)) for r in self.rows]
        self.actual_columns={k:np.array([a[k] for a in self.actual]) for k in self.actual[0]}
        self.missing_outcome=np.array([r.key not in joined for r in self.rows])
        self.blanks=np.array([not r.fixture_ids for r in self.rows])
        self.complete=(np.array([f.complete for f in self.forecasts]),np.array([f.complete for f in self.controls]))
        self.columns=[];self.numerical=[]
        for forecasts in (self.forecasts,self.controls):
            results=[row_metrics(r,f,a) for r,f,a in zip(self.rows,forecasts,self.actual)]
            keys=sorted({k for scores,_ in results for k in scores})
            self.columns.append({k:np.array([scores.get(k,math.nan) for scores,_ in results]) for k in keys})
            self.numerical.append(np.array([bool(failure) for _,failure in results]))
        self.strata=[r.strata() for r in self.rows]
        self.groups={'all':np.ones(self.n,dtype=bool)}
        for field in self.strata[0]:
            values=sorted({s[field] for s in self.strata})
            if field=='position':
                values=['GK','DEF','MID','FWD']
            if field=='availability':
                values=['forced-zero','doubtful/chance-limited','available','unknown']
            for value in values:
                self.groups[field+':'+value]=np.array([s[field]==value for s in self.strata])
        self.season_masks={s:np.array([r.season==s for r in self.rows]) for s in self.seasons}
        self.gw_masks={(s,g):self.season_masks[s]&np.array([r.gameweek==g for r in self.rows]) for s in self.seasons for g in range(2,39)}
        self.ids=np.array([r.element_id for r in self.rows])
        self.membership,self.excluded=self._membership()
        self.eligible={(i.season,i.gameweek) for i in self.membership.included}
        self.populations={}
        for s in self.seasons:
            for group,mask in self.groups.items():
                self.populations[s,group]=self._population(s,group,mask&self.season_masks[s])
        self.endpoints=endpoint_family(candidate,self.seasons)
        self.endpoint_status=tuple(self._endpoint_status(e) for e in self.endpoints)
        self.compiled=self._compile()
        self.diagnostic_plan=self._diagnostic_compile()
        self.calibration_plan=self._calibration_compile()
        self.ranking_plans={}
        for season,gw in sorted(self.eligible):
            mask=self.gw_masks[season,gw]&~self.blanks&self._valid('modeled_spearman',0)&self._valid('modeled_spearman',1)
            ix=np.flatnonzero(mask)
            self.ranking_plans[season,gw]=(ix,tuple(RankingPlan(self.columns[side]['modeled_prediction'][ix],self.actual_columns['modeled'][ix],self.ids[ix]) for side in (0,1)))

    def _membership(self):
        included=[];excluded=[]
        for key,mask in self.gw_masks.items():
            common=mask&~self.blanks&self.complete[0]&self.complete[1]&np.isfinite(self.actual_columns['modeled'])
            indexes=np.flatnonzero(common);reasons=[]
            if len(indexes)<50:
                reasons.append('FEWER_THAN_50_COMMON_PLAYERS')
            for values,reason in ((self.actual_columns['modeled'],'CONSTANT_ACTUAL'),
                                  (self.columns[0]['modeled_prediction'],'CONSTANT_CANDIDATE'),
                                  (self.columns[1]['modeled_prediction'],'CONSTANT_CONTROL')):
                if len(indexes)==0 or np.unique(values[indexes]).size<2:
                    reasons.append(reason)
            identity=Identity(season=key[0],gameweek=key[1])
            if reasons:
                excluded.append(ExcludedGameweek(identity=identity,common_players=len(indexes),reasons=tuple(reasons)))
            else:
                included.append(identity)
        return Membership.create(self.candidate,tuple(included)),tuple(excluded)

    def _population(self,season,group,mask):
        fullmask=mask.copy()
        mask=mask&~self.blanks
        actual=np.isfinite(self.actual_columns['modeled'])
        common=mask&actual&self.complete[0]&self.complete[1]
        selected=[self.rows[i] for i in np.flatnonzero(common)]
        counts=dict(universe=int(mask.sum()),common=len(selected),players=len({r.element_id for r in selected}),
                    gameweeks=len({r.gameweek for r in selected}),candidate_complete=int(np.sum(mask&self.complete[0])),
                    control_complete=int(np.sum(mask&self.complete[1])),actual_complete=int(np.sum(mask&actual)))
        status=population_gate(**counts,subgroup=group!='all',availability=group.startswith('availability:') and not np.any(self.groups[group]&self.season_masks[season]&~self.blanks))
        return Population(season=season,group=group,**counts,status=status,
            missing_outcome=int(np.sum(mask&self.missing_outcome)),missing_start=int(np.sum(mask&~np.isfinite(self.actual_columns['start_binary']))),
            missing_goal=int(np.sum(mask&~np.isfinite(self.actual_columns['goals']))),missing_assist=int(np.sum(mask&~np.isfinite(self.actual_columns['assists']))),
            blanks=int(np.sum(self.groups[group]&self.season_masks[season]&self.blanks)),
            universe_including_blanks=int(fullmask.sum()),candidate_complete_including_blanks=int(np.sum(fullmask&self.complete[0])),
            control_complete_including_blanks=int(np.sum(fullmask&self.complete[1])),actual_complete_including_blanks=int(np.sum(fullmask&actual)),
            common_membership_sha256=hashlib.sha256(canonical_bytes([list(r.key) for r in selected])).hexdigest(),
            overflow_count=int(np.nansum(self.actual_columns['overflow_count'][mask])))

    def _score_column(self,side,name):
        cols=self.columns[side]
        for suffix,key in (('_mae','_ae'),('_rmse','_se'),('_absolute_bias','_bias')):
            if name.endswith(suffix):
                return cols[name[:-len(suffix)]+key]
        return cols.get(name,np.full(self.n,math.nan))

    def _valid(self,name,side):
        if name=='coverage':
            return np.ones(self.n,dtype=bool)
        if name in RANKING or name in COMPONENT_RANKING:
            component=name.split('_')[0]
            valid=np.isfinite(self.columns[side][component+'_prediction'])&np.isfinite(self.actual_columns[component])
            return valid & self.complete[side] if component=='modeled' else valid
        if name.endswith('_ece') or name.endswith('_binary_brier'):
            event=name.split('_')[0]
            return np.isfinite(self.columns[side][event+'_probability'])&np.isfinite(self.actual_columns[event+'_binary'])
        return ~np.isnan(self._score_column(side,name))

    def _endpoint_status(self,e):
        seasons=self.seasons if e.season=='AGGREGATE' else (e.season,)
        states=[self.populations[s,e.group].status for s in seasons]
        if 'NOT_APPLICABLE' in states:
            return 'NOT_APPLICABLE'
        if 'INSUFFICIENT_EVIDENCE' in states:
            return 'INSUFFICIENT_EVIDENCE'
        if e.metric in RANKING:
            if any(sum(ss==s for ss,g in self.eligible)<30 for s in seasons):
                return 'INSUFFICIENT_EVIDENCE'
        if e.metric in ('goal_rps','assist_rps','mean_rps'):
            if any(np.any((self.numerical[0]|self.numerical[1])&self.season_masks[s]&self.groups[e.group]&~self.blanks) for s in seasons):
                return 'NUMERICAL_FAILURE'
        # Every required accuracy metric has its own complete common population.
        if e.metric not in RANKING and e.metric!='coverage':
            common=self._valid(e.metric,0)&self._valid(e.metric,1)
            for s in seasons:
                mask=self.season_masks[s]&self.groups[e.group]&~self.blanks
                idx=np.flatnonzero(mask&common)
                if len(idx)<(1000 if e.group=='all' else 200) or len({self.rows[i].element_id for i in idx})<(100 if e.group=='all' else 20) or len({self.rows[i].gameweek for i in idx})<(30 if e.group=='all' else 20):
                    return 'INSUFFICIENT_EVIDENCE'
        return 'PASS'

    def measure(self,name,side,mask,weights,common=True,player_counts=None,gw_counts=None):
        valid=self._valid(name,side)
        if common and name!='coverage':
            valid=valid&self._valid(name,1-side)
        mask=mask&valid
        w=weights*mask
        if name=='coverage':
            return weighted_mean(self.complete[side].astype(float),w)
        if name in RANKING or name in COMPONENT_RANKING:
            component=name.split('_')[0]
            values=[];gw_weights=[]
            population=self.eligible if name in RANKING else self.gw_masks
            for s,g in sorted(population):
                gm=1 if gw_counts is None else gw_counts[s].get(g,0)
                if not gm:
                    continue
                ix=np.flatnonzero(mask&self.gw_masks[s,g])
                if not len(ix):
                    continue
                multiplicities=np.ones(len(ix),dtype=np.int64) if player_counts is None else np.array([player_counts[s].get(int(self.ids[i]),0) for i in ix])
                pred=self.columns[side][component+'_prediction'][ix];actual=self.actual_columns[component][ix]
                value=spearman(pred,actual,multiplicities) if name.endswith('spearman') else top_overlap(pred,actual,self.ids[ix],multiplicities,int(name.split('top')[1]))
                if not math.isfinite(value):
                    return math.nan
                values.append(value);gw_weights.append(gm)
            return weighted_mean(values,gw_weights)
        if name.endswith('_ece') or name.endswith('_binary_brier'):
            event=name.split('_')[0]
            values=calibration(self.columns[side][event+'_probability'],self.actual_columns[event+'_binary'],w)
            return values['ece' if name.endswith('_ece') else 'brier']
        result=weighted_mean(self._score_column(side,name),w)
        return math.sqrt(result) if name.endswith('_rmse') else abs(result) if name.endswith('_absolute_bias') else result

    def _compile(self):
        """Batch linear sufficient sums; nonlinear transforms are repeated per draw."""
        specs={};columns=[]
        for e in self.endpoints:
            if e.season=='AGGREGATE' or e.metric in RANKING or e.metric.endswith('_ece'):
                continue
            key=(e.season,e.group,e.metric)
            if key in specs:
                continue
            mask=self.season_masks[e.season]&self.groups[e.group]&~self.blanks
            if e.metric=='coverage':
                v0=self.complete[0].astype(float);v1=self.complete[1].astype(float)
            else:
                mask=mask&self._valid(e.metric,0)&self._valid(e.metric,1)
                v0=self._score_column(0,e.metric);v1=self._score_column(1,e.metric)
            start=len(columns);specs[key]=start
            columns.extend((mask.astype(float),np.where(mask,v0,0),np.where(mask,v1,0)))
        return specs,np.ascontiguousarray(np.array(columns).T)

    def _diagnostic_compile(self):
        plans=[];keys=[]
        # Store sparse group-local matrices, not a dense group-by-row tensor.
        for season in self.seasons:
            for group,groupmask in self.groups.items():
                population=self.populations[season,group]
                if population.common<200 or population.players<20 or population.gameweeks<20:
                    continue
                ix=np.flatnonzero(groupmask&self.season_masks[season]&~self.blanks)
                columns=[];names=[]
                for name in METRICS:
                    if name.endswith('_ece') or name.endswith('_binary_brier'):
                        continue
                    valid=(self._valid(name,0)&self._valid(name,1))[ix]
                    a=self._score_column(0,name)[ix];b=self._score_column(1,name)[ix]
                    if not valid.any() or not np.isfinite(a[valid]).all() or not np.isfinite(b[valid]).all():
                        continue
                    columns.extend((valid.astype(float),np.where(valid,a,0),np.where(valid,b,0)))
                    names.append(name);keys.append((season,group,name))
                if columns:
                    plans.append((season,group,ix,tuple(names),np.ascontiguousarray(np.array(columns).T)))
        return tuple(keys),tuple(plans)

    def diagnostic_draw(self,weights):
        result=[]
        for season,group,ix,names,matrix in self.diagnostic_plan[1]:
            sums=np.einsum('i,ij->j',weights[ix],matrix,optimize=False).reshape(-1,3)
            for name,(den,a,b) in zip(names,sums):
                if not den:
                    result.append(math.nan);continue
                a=a/den;b=b/den
                if name.endswith('_rmse'):
                    a=math.sqrt(a);b=math.sqrt(b)
                if name.endswith('_absolute_bias'):
                    a=abs(a);b=abs(b)
                result.append(a-b)
        return result

    def _calibration_compile(self):
        plans=[]
        for season in self.seasons:
            for side in (0,1):
                forecasts=self.forecasts if side==0 else self.controls
                for gw in range(2,39):
                    for event in ('goal','assist'):
                        selected=[i for i in np.flatnonzero(self.gw_masks[season,gw]&~self.blanks)
                                  if getattr(forecasts[i],event+'s') is not None and math.isfinite(self.actual_columns[event+'s'][i])]
                        selected.sort(key=lambda i:(getattr(forecasts[i],event+'s'),int(self.ids[i])))
                        plans.append((season,gw,event,side,selected))
        width=max((len(p[4]) for p in plans),default=0)
        indexes=np.zeros((len(plans),width),dtype=int);valid=np.zeros_like(indexes,dtype=bool)
        means=np.zeros_like(indexes,dtype=float);actual=np.zeros_like(means)
        for j,(_,_,event,side,selected) in enumerate(plans):
            indexes[j,:len(selected)]=selected;valid[j,:len(selected)]=True
            forecasts=self.forecasts if side==0 else self.controls
            means[j,:len(selected)]=[getattr(forecasts[i],event+'s') for i in selected]
            actual[j,:len(selected)]=self.actual_columns[event+'s'][selected]
        return tuple(plans),indexes,valid,means,actual

    def calibration_draw(self,pm,gm):
        # Exact copied-rank interval intersections recompute deciles without
        # constructing a player-by-GW-by-replicate tensor or caching outcomes
        # dependent bin membership across draws.
        table=[]
        player_weights=np.array([pm[r.season].get(r.element_id,0) for r in self.rows],dtype=np.int64)
        for season in self.seasons:
            ix=np.flatnonzero(self.season_masks[season]&~self.blanks)
            weights=player_weights[ix]*np.array([gm[season].get(self.rows[i].gameweek,0) for i in ix])
            for side in (0,1):
                for event in ('appearance','start'):
                    bins=calibration(self.columns[side][event+'_probability'][ix],self.actual_columns[event+'_binary'][ix],weights)['bins']
                    table.extend((b['weight'],None if not math.isfinite(b['forecast']) else b['forecast'],None if not math.isfinite(b['observed']) else b['observed']) for b in bins)
        plans,indexes,valid,means,actual=self.calibration_plan
        copies=player_weights[indexes]*valid
        end=np.cumsum(copies,axis=1);begin=end-copies;n=copies.sum(axis=1)
        gw_weights=np.array([gm[season].get(gw,0) for season,gw,_,_,_ in plans])
        for bin in range(10):
            low=(bin*n+9)//10;high=((bin+1)*n+9)//10
            retained=np.maximum(0,np.minimum(end,high[:,None])-np.maximum(begin,low[:,None]))
            count=retained.sum(axis=1)
            predicted=np.sum(retained*means,axis=1);observed=np.sum(retained*actual,axis=1)
            table.extend((int(c*g),float(p/c) if c and g else None,float(a/c) if c and g else None)
                         for c,p,a,g in zip(count,predicted,observed,gw_weights))
        return canonical_bytes(table)

    def differences(self,weights,pm=None,gm=None):
        specs,matrix=self.compiled
        sums=np.einsum('i,ij->j',weights,matrix,optimize=False)
        cache={}
        for season in self.seasons:
            total=np.zeros(4);den=0
            for (s,g),(ix,plans) in self.ranking_plans.items():
                if s!=season:
                    continue
                gw_weight=1 if gm is None else gm[s].get(g,0)
                if not gw_weight:
                    continue
                copies=np.ones(len(ix),dtype=np.int64) if pm is None else np.array([pm[s].get(int(self.ids[i]),0) for i in ix],dtype=np.int64)
                total+=gw_weight*(plans[1].calculate(copies)-plans[0].calculate(copies));den+=gw_weight
            for name,value in zip(RANKING,total/den if den else np.full(4,math.nan)):
                cache[season,'all',name]=value
        for e in self.endpoints:
            if e.season=='AGGREGATE':
                continue
            key=(e.season,e.group,e.metric)
            if key in cache:
                continue
            if key in specs:
                i=specs[key];den=sums[i]
                c=sums[i+1]/den if den else math.nan;b=sums[i+2]/den if den else math.nan
                if e.metric.endswith('_rmse'):
                    c=math.sqrt(c) if math.isfinite(c) else c;b=math.sqrt(b) if math.isfinite(b) else b
                if e.metric.endswith('_absolute_bias'):
                    c=abs(c);b=abs(b)
            else:
                mask=self.season_masks[e.season]&self.groups[e.group]&~self.blanks
                c=self.measure(e.metric,0,mask,weights,player_counts=pm,gw_counts=gm)
                b=self.measure(e.metric,1,mask,weights,player_counts=pm,gw_counts=gm)
            cache[key]=(b-c) if e.metric in RANKING or e.metric=='coverage' else (c-b)
        return np.array([float(np.mean([cache[s,e.group,e.metric] for s in self.seasons])) if e.season=='AGGREGATE' else cache[e.season,e.group,e.metric] for e in self.endpoints])

    def diagnostics(self):
        result=[];bins=[];serial=[]
        scopes=[(s,g,self.season_masks[s]&m) for s in self.seasons for g,m in self.groups.items()]
        scopes += [(s,'gameweek:'+str(g),mask) for (s,g),mask in self.gw_masks.items()]
        weights=np.ones(self.n)
        for season,group,scope in scopes:
            mask=scope if group=='fixture_count:blank' else scope&~self.blanks
            names=METRICS+COMPONENT_RANKING+(RANKING if group=='all' else ())
            for name in names:
                common=mask&self._valid(name,0)&self._valid(name,1)
                values=[self.measure(name,side,mask,weights,common=paired) for paired in (False,True) for side in (0,1)]
                diff=values[2]-values[3]
                if name in RANKING or name in COMPONENT_RANKING:
                    diff=-diff
                control_status=Statistic(status='NOT_DEFINED_FOR_CONTROL',value=None,reason='U0_HAS_NO_START_PROBABILITY') if name.startswith('start_') else None
                result.append(Metric(season=season,group=group,name=name,
                    candidate_natural=_stat(values[0]),control_natural=control_status or _stat(values[1]),
                    candidate_common=_stat(values[2]),control_common=control_status or _stat(values[3]),adverse_difference=_stat(diff),
                    common_rows=int(common.sum()),candidate_rows=int((mask&self._valid(name,0)).sum()),control_rows=int((mask&self._valid(name,1)).sum()),
                    candidate_infinities=int(np.sum(mask&np.isinf(self._score_column(0,name)))),control_infinities=int(np.sum(mask&np.isinf(self._score_column(1,name))))))
            if group!='all':
                continue
            for side,candidate in ((0,self.candidate),(1,'U0')):
                for event in ('appearance','start'):
                    table=calibration(self.columns[side][event+'_probability'],self.actual_columns[event+'_binary'],mask.astype(float))
                    for bin in table['bins']:
                        bins.append(CalibrationBin(season=season,gameweek=None,group='all',candidate=candidate,event=event,bin=bin['bin'],weight=bin['weight'],forecast=_stat(bin['forecast']),observed=_stat(bin['observed'])))
                for g in range(2,39):
                    gwmask=mask&self.gw_masks[season,g]
                    for event in ('goal','assist'):
                        pred=np.array([getattr(f,event+'s') if getattr(f,event+'s') is not None else math.nan for f in (self.forecasts if side==0 else self.controls)])
                        ix=np.flatnonzero(gwmask&np.isfinite(pred)&np.isfinite(self.actual_columns[event+'s']))
                        table=count_deciles(pred[ix],self.actual_columns[event+'s'][ix],self.ids[ix],np.ones(len(ix),dtype=int))
                        for bin in table:
                            bins.append(CalibrationBin(season=season,gameweek=g,group='all',candidate=candidate,event=event,bin=bin['bin'],weight=float(bin['weight']),forecast=_stat(bin['forecast']),observed=_stat(bin['observed'])))
            for primary in (('brier',) if self.candidate=='UM1' else ('mean_rps',) if self.candidate=='UA1' else ('brier','mean_rps')):
                series=[]
                for g in range(2,39):
                    gwmask=mask&self.gw_masks[season,g]
                    series.append(self.measure(primary,0,gwmask,weights)-self.measure(primary,1,gwmask,weights))
                serial.append(SerialDiagnostic(season=season,primary=primary,lag1=_stat(autocorrelation(series,1)),lag2=_stat(autocorrelation(series,2))))
        lookup={(m.season,m.group,m.name):m for m in result}
        for group in self.groups:
            names=METRICS+COMPONENT_RANKING+(RANKING if group=='all' else ())
            for name in names:
                members=[lookup[s,group,name] for s in self.seasons]
                absent=group.startswith('availability:') and any(self.populations[s,group].status=='NOT_APPLICABLE' for s in self.seasons)
                def average(field):
                    stats=[getattr(m,field) for m in members]
                    if absent:
                        return _not_applicable()
                    if all(t.status=='NOT_DEFINED_FOR_CONTROL' for t in stats):
                        return stats[0]
                    if any(t.status not in ('FINITE','POSITIVE_INFINITY') for t in stats):
                        return _stat(math.nan)
                    return _stat(math.inf if any(t.status=='POSITIVE_INFINITY' for t in stats) else math.fsum(t.value for t in stats)/len(stats))
                result.append(Metric(season='AGGREGATE',group=group,name=name,
                    candidate_natural=average('candidate_natural'),control_natural=average('control_natural'),
                    candidate_common=average('candidate_common'),control_common=average('control_common'),adverse_difference=average('adverse_difference'),
                    **{field:sum(getattr(m,field) for m in members) for field in ('common_rows','candidate_rows','control_rows','candidate_infinities','control_infinities')}))
        return tuple(result),tuple(bins),tuple(serial)

    def structurally_identical(self,e):
        mask=self.groups[e.group]&~self.blanks
        if e.season!='AGGREGATE':
            mask=mask&self.season_masks[e.season]
        if e.metric=='coverage':
            return np.array_equal(self.complete[0][mask],self.complete[1][mask])
        if e.metric in RANKING:
            a=self.columns[0]['modeled_prediction'];b=self.columns[1]['modeled_prediction']
        elif e.metric.endswith('_ece'):
            a=self.columns[0]['appearance_probability'];b=self.columns[1]['appearance_probability']
        else:
            a=self._score_column(0,e.metric);b=self._score_column(1,e.metric)
        return np.array_equal(a[mask],b[mask],equal_nan=True)

    def _replicate_stream(self,system):
        """Private numerical seam; normal evaluation always uses the frozen stream.

        Tests may replace this method with fixed replicate statistics to test
        orchestration independently of metric kernels. No public override exists.
        """
        for pm,gm in resamples(self.rows,self.stage,system):
            weights=np.array([pm[r.season].get(r.element_id,0)*gm[r.season].get(r.gameweek,0) if r.fixture_ids else 0 for r in self.rows],dtype=float)
            yield pm,gm,self.differences(weights,pm,gm),self.diagnostic_draw(weights),self.calibration_draw(pm,gm)

    def run(self,resource):
        observed=self.differences(np.ones(self.n));bounds={};clusters=[];failures=[];diagnostic_intervals=[];calibration_hashes=[]
        statuses=self.endpoint_status
        for system in ('crossed','serial'):
            matrix=np.full((REPLICATES,len(self.endpoints)),math.nan)
            counts={s:[[],[]] for s in self.seasons}
            diagnostic_replicates=np.full((REPLICATES,len(self.diagnostic_plan[0])),math.nan)
            calibration_digest=hashlib.sha256()
            # Population failure is reported without inventing an interval. Do not run
            # resampling where even the registered universe/identity bridge is absent.
            runnable=any(s=='PASS' for s in statuses)
            if runnable:
                try:
                    for b,(pm,gm,delta,diagnostic,calibration_bytes) in enumerate(self._replicate_stream(system)):
                        matrix[b]=delta
                        diagnostic_replicates[b]=diagnostic
                        calibration_digest.update(calibration_bytes)
                        for s in self.seasons:
                            counts[s][0].append(sum(v>0 for v in pm[s].values()));counts[s][1].append(sum(v>0 for v in gm[s].values()))
                except ValueError as error:
                    failures.append(system+':'+str(error));runnable=False
            for s in self.seasons:
                if len(counts[s][0])==REPLICATES:
                    summary=lambda x: (int(min(x)),int(np.median(x)),int(max(x)))
                    rows=[r for r in self.rows if r.season==s and r.fixture_ids]
                    clusters.append(ClusterSummary(system=system,season=s,observed_players=len({r.element_id for r in rows}),observed_gameweeks=len({r.gameweek for r in rows}),retained_players=summary(counts[s][0]),retained_gameweeks=summary(counts[s][1]),replicates=9999))
            if runnable:
                calibration_hashes.append(calibration_digest.hexdigest())
                for column,(season,group,metric) in enumerate(self.diagnostic_plan[0]):
                    interval=percentile_interval(diagnostic_replicates[:,column])
                    diagnostic_intervals.append(DiagnosticInterval(system=system,season=season,group=group,metric=metric,
                        status='FINITE' if interval is not None else 'UNDEFINED',low=_stat(interval[0] if interval else math.nan),high=_stat(interval[1] if interval else math.nan)))
                diag_index={key:i for i,key in enumerate(self.diagnostic_plan[0])}
                for group in self.groups:
                    for metric in METRICS:
                        keys=[(season,group,metric) for season in self.seasons]
                        if not all(key in diag_index for key in keys):
                            continue
                        values=np.mean(diagnostic_replicates[:,[diag_index[key] for key in keys]],axis=1)
                        interval=percentile_interval(values)
                        diagnostic_intervals.append(DiagnosticInterval(system=system,season='AGGREGATE',group=group,metric=metric,
                            status='FINITE' if interval is not None else 'UNDEFINED',low=_stat(interval[0] if interval else math.nan),high=_stat(interval[1] if interval else math.nan)))
            for family in ('global','subgroup'):
                indexes=[i for i,e in enumerate(self.endpoints) if e.family==family and statuses[i]!='NOT_APPLICABLE']
                valid=runnable and bool(indexes) and all(statuses[i]=='PASS' for i in indexes)
                uppers={};q=math.nan
                if valid:
                    try:
                        values,q=simultaneous_bounds(observed[indexes],matrix[:,indexes],[self.endpoints[i].scale for i in indexes])
                        uppers=dict(zip(indexes,values))
                    except ValueError:
                        failures.append(system+':'+family+':UNDEFINED_REQUIRED_STATISTIC_OR_REPLICATE')
                for i,e in enumerate(self.endpoints):
                    if e.family!=family:
                        continue
                    interval=percentile_interval(matrix[:,i]) if statuses[i]=='PASS' else None
                    upper=uppers.get(i,math.nan);passed=strict_pass(upper,e.threshold)
                    if statuses[i]=='NOT_APPLICABLE':
                        state='NOT_APPLICABLE';passed=True
                    elif statuses[i]!='PASS':
                        state=statuses[i]
                    elif i not in uppers:
                        state='UNDEFINED' if runnable else 'INSUFFICIENT_EVIDENCE'
                    elif observed[i]==0 and np.all(matrix[:,i]==0) and self.structurally_identical(e):
                        state='STRUCTURALLY_IDENTICAL'
                    else:
                        state='PASS' if passed else 'FAIL'
                    bounds[system,i]=Bound(system=system,status=state,upper=_stat(upper),family_q=_stat(q),
                         percentile_low=_stat(interval[0] if interval else math.nan),percentile_high=_stat(interval[1] if interval else math.nan),passed=passed)
        endpoints=tuple(GateEndpoint(family=e.family,group=e.group,season=e.season,metric=e.metric,scale=e.scale,threshold=e.threshold,improvement=e.improvement,
            observed_adverse=_not_applicable() if statuses[i]=='NOT_APPLICABLE' else _stat(observed[i]),population_status=statuses[i],
            crossed=bounds['crossed',i],serial=bounds['serial',i],passed=bounds['crossed',i].passed and bounds['serial',i].passed) for i,e in enumerate(self.endpoints))
        if any(e.crossed.passed != e.serial.passed for e in endpoints):
            failures.append('SERIAL_SENSITIVITY_FAIL')
        metrics,bins,serial=self.diagnostics()
        resource_status=resource.status if resource is not None else 'MISSING'
        passed=resource_status=='PASS' and not failures and all(e.passed for e in endpoints)
        return PackageResult(candidate=self.candidate,evaluation_status='PASS' if passed else 'FAIL',membership=self.membership,
            excluded_gameweeks=self.excluded,populations=tuple(self.populations.values()),metrics=metrics,calibration=bins,
            endpoints=endpoints,clusters=tuple(clusters),diagnostic_intervals=tuple(diagnostic_intervals),resampled_calibration_sha256=tuple(calibration_hashes),serial_diagnostics=serial,failures=tuple(sorted(set(failures))),resource_status=resource_status,resource_evidence_sha256=resource.evidence_sha256 if resource is not None else None)


def evaluate(predictions: PredictionBatch, outcomes: JoinedOutcomes,
             resources: tuple[ResourceGate,...], *, confirmation_candidate=None) -> EvaluationResult:
    """Evaluate supplied synthetic records, conditionally entering the combination."""
    predictions=PredictionBatch.model_validate(predictions)
    outcomes=JoinedOutcomes.model_validate(outcomes)
    if predictions.prediction_bytes_sha256!=outcomes.prediction_bytes_sha256:
        raise ValueError('OUTCOMES_BOUND_TO_DIFFERENT_PREDICTIONS')
    universe={r.key:r for r in predictions.rows}
    for row in outcomes.rows:
        if row.key not in universe or tuple(f.fixture_id for f in row.fixtures)!=universe[row.key].fixture_ids:
            raise ValueError('OUTCOME_UNIVERSE_OR_FIXTURE_MISMATCH')
    if type(resources) is not tuple:
        raise ValueError('STRICT_RESOURCE_COLLECTION_REQUIRED')
    resource_map={}
    for resource in resources:
        resource=ResourceGate.model_validate(resource)
        if resource.candidate in resource_map:
            raise ValueError('DUPLICATE_RESOURCE_GATE')
        resource_map[resource.candidate]=resource
    def run(candidate):
        return _Pair(predictions,outcomes,candidate,_COMBINATION_AUTHORITY if candidate=='UM1UA1' else None).run(resource_map.get(candidate))
    def untouched(candidate):
        return PackageResult(candidate=candidate,evaluation_status='NOT_EVALUATED',membership=None,resource_status='NOT_EVALUATED')
    if predictions.stage=='DEVELOPMENT':
        if confirmation_candidate is not None:
            raise ValueError('DEVELOPMENT_CANNOT_PRESELECT')
        first=run('UM1');second=run('UA1')
        both=first.evaluation_status==second.evaluation_status=='PASS'
        third=run('UM1UA1') if both else untouched('UM1UA1')
        selected=conditional_choice(first.evaluation_status=='PASS',second.evaluation_status=='PASS',third.evaluation_status=='PASS' if both else None)
        packages=(first,second,third);decision='AWAITING_AUTHORIZED_CONFIRMATION' if selected else 'DO_NOT_CONFIRM'
    else:
        if confirmation_candidate not in ('UM1','UA1','UM1UA1'):
            raise ValueError('FROZEN_CONFIRMATION_PACKAGE_REQUIRED')
        evaluated=run(confirmation_candidate)
        packages=tuple(evaluated if c==confirmation_candidate else untouched(c) for c in CANDIDATES[1:])
        selected=confirmation_candidate;decision='SYNTHETIC_CONFIRMATION_PASS' if evaluated.evaluation_status=='PASS' else 'DO_NOT_PROMOTE'
    return EvaluationResult(stage=predictions.stage,prediction_sha256=predictions.prediction_bytes_sha256,
        outcomes_sha256=hashlib.sha256(canonical_bytes(outcomes)).hexdigest(),packages=packages,selected=selected,decision=decision)
