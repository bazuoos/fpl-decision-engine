"""Registered scores and weighted diagnostics; pure functions on supplied values."""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from .contracts import CountDistribution, GOAL_POINTS


class NumericalFailure(ValueError):
    pass


def minute_band(minutes):
    return 0 if minutes == 0 else 1 if minutes < 30 else 2 if minutes < 60 else 3 if minutes < 90 else 4


def band_scores(pmf, minutes):
    probabilities = (pmf[0], math.fsum(pmf[1:30]), math.fsum(pmf[30:60]),
                     math.fsum(pmf[60:90]), math.fsum(pmf[90:]))
    observed = minute_band(minutes)
    return (math.fsum((p-(b==observed))**2 for b,p in enumerate(probabilities)),
            -math.log(probabilities[observed]) if probabilities[observed] else math.inf)


def _components(distribution):
    if distribution.kind == 'POISSON':
        return ((1.0, None, distribution.mean),)
    exposures = ((distribution.exposure, 1.0),) if distribution.minute_pmf is None else tuple(
        (i/90, mass) for i,mass in enumerate(distribution.minute_pmf) if mass)
    return tuple((mass*w, a, t/(b+t)) for t,mass in exposures
                 for w,a,b in zip(distribution.weights,distribution.shapes,distribution.rates) if w)


def _pmf(count, shape, parameter):
    if parameter == 0:
        return float(count == 0)
    if shape is None:
        logp = -parameter+count*math.log(parameter)-math.lgamma(count+1)
    else:
        logp = (math.lgamma(count+shape)-math.lgamma(shape)-math.lgamma(count+1)
                +shape*math.log1p(-parameter)+count*math.log(parameter))
    try:
        p=math.exp(logp)
    except (OverflowError, ValueError) as error:
        raise NumericalFailure('COUNT_PMF_FAILURE') from error
    if not math.isfinite(p) or p < 0 or p > 1+1e-12:
        raise NumericalFailure('COUNT_PMF_FAILURE')
    return p


def excess_tail_bound(components, k):
    """Analytic upper bound on E[(Y-k-1)+], without subtracting near-one CDFs.

    Starting at j=k+2, bound all subsequent pmf ratios by r<1. Then
    sum_{l>=0}(l+1)*p[j+l] <= p[j]/(1-r)^2. Apply per mixture component.
    For NB, ratios decrease to q if shape>=1 and increase to q otherwise.
    A ratio not below one is uncertified, never a discarded tail.
    """
    terms=[]
    for weight, shape, p in components:
        if p == 0:
            terms.append(0.0)
            continue
        ratio = p/(k+3) if shape is None else max(p,p*(k+2+shape)/(k+3))
        if ratio >= 1:
            return math.inf
        terms.append(weight*_pmf(k+2,shape,p)/(1-ratio)**2)
    bound=math.fsum(terms)
    return math.nextafter(bound, math.inf) if bound else 0.0


@lru_cache(maxsize=8192)
def count_scores(distribution: CountDistribution, observed: int):
    if type(observed) is not int or observed < 0:
        raise ValueError('INVALID_COUNT')
    if observed > 10000:
        raise NumericalFailure('RPS_TAIL_CEILING')
    components=_components(distribution)
    cdf=0.0; losses=[]; low=high=None; observed_p=None
    for k in range(10001):
        prob=math.fsum(w*_pmf(k,a,p) for w,a,p in components)
        cdf=math.fsum((cdf,prob))
        if not math.isfinite(cdf) or cdf > 1+1e-10:
            raise NumericalFailure('COUNT_CDF_FAILURE')
        # This only absorbs CDF summation roundoff; it does not smooth masses.
        bounded_cdf=min(1.0,cdf)
        losses.append((bounded_cdf-float(observed <= k))**2)
        if k == observed:
            observed_p=prob
        if low is None and bounded_cdf >= .1:
            low=k
        if high is None and bounded_cdf >= .9:
            high=k
        tail=excess_tail_bound(components,k)
        if k >= observed and tail <= 1e-10 and high is not None:
            return {'rps':math.fsum(losses), 'log_loss':-math.log(observed_p) if observed_p else math.inf,
                    'k':k,'tail_bound':tail,'low':low,'high':high}
    raise NumericalFailure('RPS_TAIL_CEILING')


def minute_interval(pmf):
    cdf=0.0; low=high=None
    for i,p in enumerate(pmf):
        cdf+=p
        if low is None and cdf >= .1:
            low=i
        if high is None and cdf >= .9:
            high=i
    if high is None:
        raise NumericalFailure('MINUTE_QUANTILE_FAILURE')
    return low,high


def weighted_mean(values, weights):
    values=np.asarray(values,dtype=float);weights=np.asarray(weights,dtype=float)
    valid=(weights > 0) & ~np.isnan(values)
    if not np.any(valid):
        return math.nan
    return float(np.sum(values[valid]*weights[valid])/np.sum(weights[valid]))


def weighted_error(predicted, actual, weights):
    delta=np.asarray(predicted)-np.asarray(actual)
    bias=weighted_mean(delta,weights)
    return {'mae':weighted_mean(np.abs(delta),weights),
            'rmse':math.sqrt(weighted_mean(delta**2,weights)),
            'bias':bias,'absolute_bias':abs(bias)}


def calibration(probabilities, outcomes, weights):
    p=np.asarray(probabilities,dtype=float);y=np.asarray(outcomes,dtype=float);w=np.asarray(weights,dtype=float)
    valid=~np.isnan(p) & ~np.isnan(y) & (w > 0)
    p=p[valid];y=y[valid];w=w[valid]
    bins=[];den=float(w.sum()); ece=0.0
    for i in range(10):
        mask=(p >= i/10) & ((p < (i+1)/10) if i < 9 else (p <= 1))
        count=float(w[mask].sum())
        forecast=weighted_mean(p[mask],w[mask]); observed=weighted_mean(y[mask],w[mask])
        bins.append({'bin':i,'weight':count,'forecast':forecast,'observed':observed})
        if count:
            ece+=count*abs(forecast-observed)
    return {'ece':ece/den if den else math.nan,'brier':weighted_mean((p-y)**2,w),'bins':bins}


def copied_ranks(values, multiplicities):
    """Each score tie's rank is the midpoint across all its retained copies."""
    values=np.asarray(values);m=np.asarray(multiplicities)
    order=np.argsort(values,kind='stable');v=values[order];weights=m[order]
    starts=np.r_[0,np.flatnonzero(v[1:] != v[:-1])+1]
    ends=np.r_[starts[1:],len(v)]
    cumulative=np.cumsum(weights)
    upper=cumulative[ends-1]
    lower=np.r_[0,upper[:-1]]
    ranks=np.repeat((upper+lower+1)/2,ends-starts)
    result=np.empty(len(v));result[order]=ranks
    return result


def spearman(predicted, actual, multiplicities):
    p=np.asarray(predicted);a=np.asarray(actual);m=np.asarray(multiplicities)
    valid=(m > 0) & np.isfinite(p) & np.isfinite(a)
    p=p[valid];a=a[valid];m=m[valid]
    if len(p)<2 or m.sum()<2:
        return math.nan
    x=copied_ranks(p,m);y=copied_ranks(a,m)
    x=x-weighted_mean(x,m);y=y-weighted_mean(y,m)
    den=math.sqrt(float(np.sum(m*x*x)*np.sum(m*y*y)))
    return float(np.sum(m*x*y)/den) if den else math.nan


def top_overlap(predicted, actual, ids, multiplicities, n):
    p=np.asarray(predicted);a=np.asarray(actual);ids=np.asarray(ids);m=np.asarray(multiplicities)
    if m.sum()<n:
        return math.nan
    def selected(values):
        order=np.lexsort((ids,-values));counts=np.zeros(len(m),dtype=np.int64)
        ordered=m[order]
        counts[order]=np.minimum(ordered,np.maximum(0,n-(np.cumsum(ordered)-ordered)))
        return counts
    return float(np.minimum(selected(p),selected(a)).sum()/n)


def count_deciles(predicted, actual, ids, multiplicities):
    order=np.lexsort((np.asarray(ids),np.asarray(predicted)))
    # This diagnostic explicitly reconstructs copies so bins are recomputed.
    copies=np.repeat(order,np.asarray(multiplicities,dtype=np.int64)[order])
    result=[];n=len(copies)
    for b in range(10):
        selected=copies[np.minimum(9,(10*np.arange(n))//n)==b] if n else copies
        result.append({'bin':b,'weight':len(selected),
                       'forecast':float(np.mean(np.asarray(predicted)[selected])) if len(selected) else math.nan,
                       'observed':float(np.mean(np.asarray(actual)[selected])) if len(selected) else math.nan})
    return result


def autocorrelation(series, lag):
    """Pearson correlation of lagged GW means; missing/constant is undefined."""
    x=np.asarray(series,dtype=float)
    if len(x)<=lag or not np.isfinite(x).all():
        return math.nan
    a=x[:-lag];b=x[lag:];a=a-a.mean();b=b-b.mean()
    den=math.sqrt(float(np.sum(a*a)*np.sum(b*b)))
    return float(np.sum(a*b)/den) if den else math.nan


def actual_values(row, outcome):
    values={}
    fixtures=outcome.fixtures if outcome is not None else None
    def total(field,transform=lambda x:x):
        if fixtures is None or any(getattr(f,field) is None for f in fixtures):
            return math.nan
        return float(sum(transform(getattr(f,field)) for f in fixtures))
    values['minutes']=total('minutes',lambda x:min(x,90))
    values['raw_minutes']=total('minutes')
    values['overflow_count']=total('minutes',lambda x:int(x>90))
    values['appearance']=total('minutes',lambda x:int(x>0)+int(x>=60))
    values['appearance_binary']=float(values['minutes']>0) if math.isfinite(values['minutes']) else math.nan
    starts=total('starts');values['start_binary']=float(starts>0) if math.isfinite(starts) else math.nan
    values['goals']=total('goals');values['assists']=total('assists')
    values['goal']=values['goals']*GOAL_POINTS[row.position];values['assist']=values['assists']*3
    values['attacking']=values['goal']+values['assist'];values['modeled']=values['attacking']+values['appearance']
    return values


def row_metrics(row, forecast, actual):
    p={'minutes':forecast.expected_minutes,'raw_minutes':forecast.expected_minutes,
       'appearance':forecast.appearance_points,'goal':None if forecast.goals is None else forecast.goals*GOAL_POINTS[row.position],
       'assist':None if forecast.assists is None else forecast.assists*3,'modeled':forecast.modeled_points}
    p['attacking']=None if p['goal'] is None or p['assist'] is None else p['goal']+p['assist']
    scores={};failures=[]
    for metric,prediction in p.items():
        # Numeric incomplete modeled totals are diagnostic values, not complete predictions.
        if metric=='modeled' and not forecast.complete:
            prediction=None
        delta=math.nan if prediction is None else prediction-actual[metric]
        scores[metric+'_ae']=abs(delta);scores[metric+'_se']=delta*delta;scores[metric+'_bias']=delta
        scores[metric+'_prediction']=math.nan if prediction is None else prediction
    for name,probability in (('appearance',forecast.appearance_probability),('start',forecast.start_probability)):
        scores[name+'_probability']=math.nan if probability is None else probability
    scores['brier']=scores['minutes_log_loss']=math.nan
    if forecast.minute_pmf is not None and math.isfinite(actual['minutes']):
        scores['brier'],scores['minutes_log_loss']=band_scores(forecast.minute_pmf,int(actual['minutes']))
        low,high=minute_interval(forecast.minute_pmf)
        scores['minutes_interval_coverage']=float(low<=actual['minutes']<=high);scores['minutes_interval_width']=float(high-low)
    for event in ('goal','assist'):
        scores[event+'_rps']=scores[event+'_log_loss']=math.nan
        distribution=getattr(forecast,event+'_distribution');observed=actual[event+'s']
        if distribution is not None and math.isfinite(observed):
            try:
                scored=count_scores(distribution,int(observed))
                for key in ('rps','log_loss','k','tail_bound'):
                    scores[event+'_'+key]=scored[key]
                scores[event+'_interval_coverage']=float(scored['low']<=observed<=scored['high'])
                scores[event+'_interval_width']=float(scored['high']-scored['low'])
            except (NumericalFailure, ArithmeticError, ValueError) as error:
                failures.append(event+':COUNT_NUMERICAL_FAILURE')
    scores['mean_rps']=(scores['goal_rps']+scores['assist_rps'])/2
    return scores,tuple(failures)


class RankingPlan:
    """Frozen score order, but copied midranks and selected copies recompute per draw."""
    def __init__(self,predicted,actual,ids):
        self.values=(np.asarray(predicted),np.asarray(actual))
        self.orders=[];self.groups=[];self.top_orders=[]
        for values in self.values:
            order=np.argsort(values,kind='stable');sorted_values=values[order]
            starts=np.r_[0,np.flatnonzero(sorted_values[1:]!=sorted_values[:-1])+1]
            ends=np.r_[starts[1:],len(order)]
            self.orders.append(order);self.groups.append((starts,ends))
            self.top_orders.append(np.lexsort((ids,-values)))

    def calculate(self,m):
        n=int(m.sum())
        ranks=[]
        for order,(starts,ends) in zip(self.orders,self.groups):
            cumulative=np.cumsum(m[order]);upper=cumulative[ends-1];lower=np.r_[0,upper[:-1]]
            rank=np.empty(len(m));rank[order]=np.repeat((upper+lower+1)/2,ends-starts)
            ranks.append(rank-(n+1)/2)
        a,b=ranks;den=math.sqrt(float(np.sum(m*a*a)*np.sum(m*b*b)))
        result=[float(np.sum(m*a*b)/den) if den else math.nan]
        for target in (10,25,50):
            if n<target:
                result.append(math.nan);continue
            selected=[]
            for order in self.top_orders:
                counts=np.zeros(len(m),dtype=np.int64);ordered=m[order]
                counts[order]=np.minimum(ordered,np.maximum(0,target-(np.cumsum(ordered)-ordered)))
                selected.append(counts)
            result.append(float(np.minimum(*selected).sum()/target))
        return np.array(result)
