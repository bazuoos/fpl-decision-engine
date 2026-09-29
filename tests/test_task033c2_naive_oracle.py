import unittest
import numpy as np
from fpl_decision_engine.research.c2.evaluation import _Pair
from fpl_decision_engine.research.c2.inference import resamples,Endpoint
from task033c2_naive_oracle import randomized_fixture,NaiveOracle


class ExpandedOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.pred,cls.outcomes=randomized_fixture()

    def test_differences_against_naive_rows_both_candidates_and_streams(self):
        self.assertTrue(any(not r.fixture_ids for r in self.pred.rows))
        self.assertTrue(any(len(r.fixture_ids)==2 for r in self.pred.rows))
        for candidate in ('UM1','UA1'):
            pair=_Pair(self.pred,self.outcomes,candidate);oracle=NaiveOracle(self.pred,self.outcomes,candidate)
            self.assertEqual(pair.eligible,oracle.eligible)
            # Signed bias is descriptive, not a new registered gate. The private
            # arithmetic test appends it solely to exercise the sufficient-sum path.
            pair.endpoints+=tuple(Endpoint('global','all',s,'modeled_bias',.02,.02,False) for s in pair.seasons)
            pair.compiled=pair._compile()
            for system in ('crossed','serial'):
                stream=resamples(self.pred.rows,'DEVELOPMENT',system)
                for draw in range(2):
                    pm,gm=next(stream)
                    weights=np.array([pm[r.season].get(r.element_id,0)*gm[r.season].get(r.gameweek,0) if r.fixture_ids else 0 for r in self.pred.rows])
                    result=pair.differences(weights,pm,gm)
                    for e,got in zip(pair.endpoints,result):
                        if e.group not in ('all','position:DEF'):continue
                        expected=oracle.difference(e,pm,gm)
                        with self.subTest(candidate=candidate,system=system,draw=draw,endpoint=e):
                            self.assertAlmostEqual(got,expected,delta=2e-10)

if __name__=='__main__':unittest.main()
