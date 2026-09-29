import math
import unittest
import numpy as np
from fpl_decision_engine.research.c2.contracts import CountDistribution
from fpl_decision_engine.research.c2.metrics import (
    band_scores,count_scores,excess_tail_bound,NumericalFailure,weighted_error,
    calibration,spearman,top_overlap,count_deciles,autocorrelation,minute_interval,
)


class MetricTests(unittest.TestCase):
    def test_five_band_brier_manually_derived(self):
        pmf=[0.]*181;pmf[0]=.1;pmf[20]=.2;pmf[45]=.3;pmf[70]=.15;pmf[120]=.25
        score,log=band_scores(pmf,110)
        self.assertAlmostEqual(score,.1**2+.2**2+.3**2+.15**2+.75**2)
        self.assertAlmostEqual(log,math.log(4))

    def test_zero_mass_retains_infinity(self):
        self.assertEqual(band_scores((1.,)+(0.,)*90,30),(2.,math.inf))
        self.assertEqual(count_scores(CountDistribution(kind='POISSON',mean=0.),3)['log_loss'],math.inf)

    def test_degenerate_count_rps_exact(self):
        for observed in (0,1,7):
            result=count_scores(CountDistribution(kind='POISSON',mean=0.),observed)
            self.assertEqual(result['rps'],observed)
            self.assertEqual(result['tail_bound'],0)
            self.assertEqual((result['low'],result['high']),(0,0))

    def test_geometric_rps_independent_closed_form(self):
        # Gamma(shape=1,rate=1), exposure=1 gives P(Y=k)=2^(-k-1).
        # At y=0, RPS=sum_{k>=0}4^(-k-1)=1/3.
        dist=CountDistribution(kind='GAMMA_MIXTURE',mean=1.,weights=(1.,),shapes=(1.,),rates=(1.,),exposure=1.)
        result=count_scores(dist,0)
        self.assertLessEqual(abs(result['rps']-1/3),1e-10)
        self.assertLessEqual(result['tail_bound'],1e-10)
        self.assertAlmostEqual(result['log_loss'],math.log(2))
        self.assertEqual((result['low'],result['high']),(0,3))

    def test_geometric_tail_bound_is_exact_excess(self):
        for k in (0,2,10,30):
            actual=2.**(-k-1)
            bound=excess_tail_bound(((1.,1.,.5),),k)
            self.assertGreaterEqual(bound,actual*(1-1e-13))
            self.assertAlmostEqual(bound,actual)

    def test_poisson_score_against_direct_enumeration(self):
        dist=CountDistribution(kind='POISSON',mean=2.)
        masses=[math.exp(-2)*2**k/math.factorial(k) for k in range(100)]
        expected=sum((sum(masses[:k+1])-int(3<=k))**2 for k in range(100))
        self.assertAlmostEqual(count_scores(dist,3)['rps'],expected,places=10)

    def test_tail_ceiling_failure(self):
        with self.assertRaises(NumericalFailure):
            count_scores(CountDistribution(kind='POISSON',mean=1e6),0)
        with self.assertRaises(NumericalFailure):
            count_scores(CountDistribution(kind='POISSON',mean=0.),10001)

    def test_integrated_count_mean_and_score(self):
        pmf=(.5,)+(0.,)*89+(.5,)
        dist=CountDistribution(kind='GAMMA_MIXTURE',mean=.5,weights=(1.,),shapes=(1.,),rates=(1.,),minute_pmf=pmf)
        # For observed zero, mixture survival=.5*(.5**(k+1)).
        self.assertAlmostEqual(count_scores(dist,0)['rps'],1/12,places=10)

    def test_weighted_errors_sign_and_absolute_bias(self):
        result=weighted_error([1,5],[2,2],[3,1])
        self.assertEqual(result['mae'],1.5)
        self.assertAlmostEqual(result['rmse'],math.sqrt(3))
        self.assertEqual(result['bias'],0)
        self.assertEqual(result['absolute_bias'],0)

    def test_fixed_calibration_bins_and_constant_actuals(self):
        result=calibration([0,.1,.9,1],[0,0,0,0],[1,2,1,1])
        self.assertAlmostEqual(result['ece'],.42)
        self.assertEqual(result['bins'][1]['weight'],2)
        self.assertEqual(result['bins'][9]['weight'],2)
        self.assertTrue(math.isnan(calibration([],[],[])['ece']))

    def test_copied_ranks_match_explicit_midrank_oracle(self):
        p=[1.,1.,3.,2.];a=[1.,3.,2.,2.];weights=[2,1,3,1]
        def ranks(values):
            return np.array([1+sum(v<x for v in values)+(sum(v==x for v in values)-1)/2 for x in values])
        pp=np.repeat(p,weights);aa=np.repeat(a,weights)
        expected=float(np.corrcoef(ranks(pp),ranks(aa))[0,1])
        self.assertAlmostEqual(spearman(p,a,weights),expected)
        self.assertTrue(math.isnan(spearman([1,1],[1,2],[1,1])))

    def test_strict_topn_copied_ids_and_ties(self):
        self.assertEqual(top_overlap([1,1,1],[3,2,1],[3,2,1],[1,1,1],2),.5)
        self.assertEqual(top_overlap([3,2],[2,3],[1,2],[3,2],3),1/3)
        self.assertTrue(math.isnan(top_overlap([1],[1],[1],[1],2)))

    def test_deciles_recomputed_for_copies(self):
        table=count_deciles([1,2],[4,5],[1,2],[9,1])
        self.assertEqual([b['forecast'] for b in table],[1.]*9+[2.])
        self.assertEqual(sum(b['weight'] for b in count_deciles([1,2],[4,5],[1,2],[1,1])),2)

    def test_interval_and_autocorrelation(self):
        self.assertEqual(minute_interval((.1,.7,.2)),(0,2))
        self.assertAlmostEqual(autocorrelation([1,2,3,4,5],1),1)
        self.assertTrue(math.isnan(autocorrelation([1,1,1],1)))
        self.assertTrue(math.isnan(autocorrelation([1,math.nan,2],1)))

class ExtraMetricTests(unittest.TestCase):
    def test_dgw_actual_cap_per_fixture_and_modeled_scope(self):
        from types import SimpleNamespace
        from fpl_decision_engine.research.c2.metrics import actual_values
        row=SimpleNamespace(position='GK')
        outcome=SimpleNamespace(fixtures=(SimpleNamespace(minutes=95,starts=1,goals=1,assists=0),SimpleNamespace(minutes=100,starts=0,goals=0,assists=1)))
        actual=actual_values(row,outcome)
        self.assertEqual(actual['minutes'],180)
        self.assertEqual(actual['raw_minutes'],195)
        self.assertEqual(actual['appearance'],4)
        self.assertEqual(actual['modeled'],17)
        self.assertEqual(actual['overflow_count'],2)
        self.assertEqual(actual['start_binary'],1)

    def test_optimized_copied_rank_plan_matches_expansion(self):
        from fpl_decision_engine.research.c2.metrics import RankingPlan
        rng=np.random.Generator(np.random.PCG64(4))
        for _ in range(20):
            p=rng.integers(0,10,70);a=rng.integers(0,10,70);m=rng.integers(0,4,70);ids=np.arange(70)
            got=RankingPlan(p,a,ids).calculate(m)
            expected=[spearman(p,a,m)]+[top_overlap(p,a,ids,m,n) for n in (10,25,50)]
            np.testing.assert_allclose(got,expected,atol=1e-14)

    def test_structural_blank_is_not_missing_outcome(self):
        from types import SimpleNamespace
        from fpl_decision_engine.research.c2.metrics import actual_values
        actual=actual_values(SimpleNamespace(position='MID'),SimpleNamespace(fixtures=()))
        self.assertEqual(actual['modeled'],0)
        self.assertTrue(math.isnan(actual_values(SimpleNamespace(position='MID'),None)['modeled']))

if __name__=='__main__':
    unittest.main()
