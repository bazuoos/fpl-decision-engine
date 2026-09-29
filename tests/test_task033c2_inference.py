from types import SimpleNamespace
import unittest
import numpy as np
from fpl_decision_engine.research.c2.inference import (
    population_gate,resamples,endpoint_family,simultaneous_bounds,percentile_interval,
    strict_pass,conditional_choice,SEEDS,
)


def rows():
    return tuple(SimpleNamespace(season=s,element_id=i,gameweek=g,fixture_ids=(1,),player_code=code)
                 for s in ('a','b') for g in range(2,39) for i,code in ((1,'10'),(2,'2'),(3,'é')))


class InferenceTests(unittest.TestCase):
    def test_primary_exact_draw_oracle_all_9999(self):
        rng=np.random.Generator(np.random.PCG64(330331));count=0
        for pm,gm in resamples(rows(),'DEVELOPMENT','crossed'):
            for s in ('a','b'):
                self.assertEqual(list(pm[s].values()),list(np.bincount(rng.integers(0,3,size=3,dtype=np.int64),minlength=3)))
                self.assertEqual(list(gm[s].values()),list(np.bincount(rng.integers(0,37,size=37,dtype=np.int64),minlength=37)))
            count+=1
        self.assertEqual(count,9999)

    def test_serial_exact_draw_order_and_shared_players_all_9999(self):
        rng=np.random.Generator(np.random.PCG64(330431));count=0;unequal=False;below30=False
        for pm,gm in resamples(rows(),'DEVELOPMENT','serial'):
            expected=np.bincount(rng.integers(0,3,size=3,dtype=np.int64),minlength=3)
            self.assertEqual(pm['a'],pm['b'])
            self.assertEqual(list(pm['a'].values()),list(expected))
            for s in ('a','b'):
                starts=rng.integers(0,34,size=10,dtype=np.int64)+2
                sequence=[int(g) for start in starts for g in range(start,start+4)][:37]
                self.assertEqual(gm[s],{g:sequence.count(g) for g in range(2,39)})
                below30 |= sum(v>0 for v in gm[s].values())<30
            unequal |= gm['a']!=gm['b'];count+=1
        self.assertEqual(count,9999);self.assertTrue(unequal);self.assertTrue(below30)

    def test_confirmation_seeds_and_missing_bridge(self):
        self.assertEqual(SEEDS['crossed']['CONFIRMATION'],330332)
        self.assertEqual(SEEDS['serial']['CONFIRMATION'],330432)
        bad=SimpleNamespace(season='a',element_id=1,gameweek=2,fixture_ids=(1,),player_code=None)
        with self.assertRaises(ValueError):
            next(resamples((bad,),'CONFIRMATION','serial'))

    def test_confirmation_draw_oracles_all_9999(self):
        one_season=tuple(r for r in rows() if r.season=='a')
        for system,seed in (('crossed',330332),('serial',330432)):
            rng=np.random.Generator(np.random.PCG64(seed));count=0
            for pm,gm in resamples(one_season,'CONFIRMATION',system):
                expected=np.bincount(rng.integers(0,3,size=3,dtype=np.int64),minlength=3)
                self.assertEqual(list(pm['a'].values()),list(expected))
                if system=='crossed':
                    g=np.bincount(rng.integers(0,37,size=37,dtype=np.int64),minlength=37)
                else:
                    starts=rng.integers(0,34,size=10,dtype=np.int64)+2
                    sequence=[int(x) for start in starts for x in range(start,start+4)][:37]
                    g=[sequence.count(x) for x in range(2,39)]
                self.assertEqual(list(gm['a'].values()),list(g));count+=1
            self.assertEqual(count,9999)

    def test_one_resampler_pass_cannot_make_endpoint_pass(self):
        from fpl_decision_engine.research.c2.contracts import Statistic
        from fpl_decision_engine.research.c2.results import Bound,GateEndpoint
        finite=Statistic.number
        def bound(system,upper):
            return Bound(system=system,status='PASS' if upper<.03 else 'FAIL',upper=finite(upper),
                family_q=finite(0.),percentile_low=finite(0.),percentile_high=finite(upper),passed=upper<.03)
        for a,b in ((.02,.04),(.04,.02)):
            fields=dict(family='global',group='all',season='a',metric='modeled_mae',scale=.03,threshold=.03,
                improvement=False,observed_adverse=finite(0.),population_status='PASS',crossed=bound('crossed',a),serial=bound('serial',b))
            self.assertFalse(GateEndpoint(**fields,passed=False).passed)
            with self.assertRaises(ValueError):GateEndpoint(**fields,passed=True)

    def test_global_population_boundaries(self):
        base=dict(universe=2000,common=1800,players=100,gameweeks=30,candidate_complete=1900,control_complete=1900,actual_complete=1980)
        self.assertEqual(population_gate(**base),'PASS')
        for field in ('common','players','gameweeks','candidate_complete','control_complete','actual_complete'):
            low=dict(base);low[field]-=1
            with self.subTest(field=field):self.assertEqual(population_gate(**low),'INSUFFICIENT_EVIDENCE')
            high=dict(base);high[field]+=1
            self.assertEqual(population_gate(**high),'PASS')
        base.update(universe=1000,common=1000,candidate_complete=1000,control_complete=1000,actual_complete=1000)
        self.assertEqual(population_gate(**base),'PASS')
        base['common']=999;self.assertEqual(population_gate(**base),'INSUFFICIENT_EVIDENCE')

    def test_subgroup_absence_and_minimums(self):
        values=dict(universe=200,common=200,players=20,gameweeks=20,candidate_complete=200,control_complete=200,actual_complete=200,subgroup=True)
        self.assertEqual(population_gate(**values),'PASS')
        for field in ('common','players','gameweeks'):
            bad=dict(values);bad[field]-=1;self.assertEqual(population_gate(**bad),'INSUFFICIENT_EVIDENCE')
        empty={k:0 for k in ('universe','common','players','gameweeks','candidate_complete','control_complete','actual_complete')}
        self.assertEqual(population_gate(**empty,subgroup=True,availability=True),'NOT_APPLICABLE')
        self.assertEqual(population_gate(**empty,subgroup=True),'INSUFFICIENT_EVIDENCE')

    def test_complete_endpoint_families_and_reserved_allocation(self):
        sizes=[]
        for c in ('UM1','UA1','UM1UA1'):
            eps=endpoint_family(c,('a','b'));sizes.append(sum(e.family=='global' for e in eps))
            self.assertEqual(sum(e.family=='subgroup' for e in eps),8*4*3)
            self.assertTrue(all(e.threshold<0 for e in eps if e.improvement))
            self.assertTrue(all(e.season=='AGGREGATE' for e in eps if e.improvement))
        self.assertEqual(sizes,[39,39,54])

    def test_simultaneous_basic_sign_scale_quantile_and_strictness(self):
        replicates=np.zeros((9999,2));replicates[:,0]=-np.arange(9999)*.01;replicates[:,1]=-1.
        upper,q=simultaneous_bounds([0.,0.],replicates,[.01,1.])
        self.assertAlmostEqual(q,9916.)
        self.assertAlmostEqual(upper[0],99.16)
        self.assertFalse(strict_pass(.01,.01));self.assertFalse(strict_pass(-.01,-.01))
        self.assertTrue(strict_pass(-.010001,-.01))
        self.assertIs(type(strict_pass(np.float64(-.010001),-.01)),bool)

    def test_percentile_indices_and_undefined_fail_closed(self):
        self.assertEqual(percentile_interval(np.arange(9999)),(249.,9749.))
        bad=np.zeros((9999,1));bad[77,0]=np.nan
        with self.assertRaises(ValueError):simultaneous_bounds([0.],bad,[1.])
        with self.assertRaises(ValueError):simultaneous_bounds([0.],bad[:9998],[1.])
        self.assertIsNone(percentile_interval(bad[:,0]))

    def test_fixed_selection_all_branches(self):
        self.assertIsNone(conditional_choice(False,False))
        self.assertEqual(conditional_choice(True,False),'UM1')
        self.assertEqual(conditional_choice(False,True),'UA1')
        self.assertEqual(conditional_choice(True,True,True),'UM1UA1')
        self.assertEqual(conditional_choice(True,True,False),'UM1')
        with self.assertRaises(ValueError):conditional_choice(False,True,False)
        with self.assertRaises(ValueError):conditional_choice(True,True)

if __name__=='__main__':
    unittest.main()
