"""Fixed numerical draws test the real gate/result/selection orchestration.

Only private observed-statistic and replicate seams are replaced. Contracts,
populations, complete family bounds, statuses and public selection remain real.
The separate naive row oracle tests arithmetic without these seams.
"""
from contextlib import contextmanager
import hashlib
import unittest
from unittest.mock import patch
import numpy as np
from fpl_decision_engine.research.c2.evaluation import _Pair,evaluate
from fpl_decision_engine.research.c2.contracts import ResourceGate,canonical_bytes
from fpl_decision_engine.research.c2.results import EvaluationResult
from task033c2_naive_oracle import runner,bind,replace_fixtures


def resources():
    return tuple(ResourceGate(candidate=c,status='PASS',scope='SYNTHETIC_C2_EVALUATION',evidence_sha256='1'*64) for c in ('UM1','UA1','UM1UA1'))


@contextmanager
def fixed_statistics(mode='PASS',fail_combination=False):
    calls=[]
    def observed(pair,*args,**kwargs):
        values=np.array([-2*e.scale if e.improvement else 0. for e in pair.endpoints])
        if fail_combination and pair.candidate=='UM1UA1':
            values[next(i for i,e in enumerate(pair.endpoints) if e.improvement)]=0.
        return values
    def draws(pair,system):
        calls.append((pair.candidate,system))
        # Resolve the real resampler's bridge validation before fixed arithmetic.
        from fpl_decision_engine.research.c2.inference import resamples
        pm,gm=next(resamples(pair.rows,pair.stage,system))
        values=observed(pair);bad=next(i for i,e in enumerate(pair.endpoints) if e.improvement)
        if mode=='SERIAL' and system=='serial':values[bad]-=.1
        diag=np.zeros(len(pair.diagnostic_plan[0]))
        for b in range(9999):
            delta=values.copy()
            if mode=='UNDEFINED' and system=='crossed' and b==817:delta[bad]=np.nan
            yield pm,gm,delta,diag,b'[]\n'
    with patch.object(_Pair,'differences',observed),patch.object(_Pair,'_replicate_stream',draws):yield calls


class OrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.pred,cls.outcomes=runner.synthetic_records(100)

    def test_complete_pass_authorizes_combination_and_roundtrips(self):
        with fixed_statistics() as calls:
            first=evaluate(self.pred,self.outcomes,resources())
            repeat=evaluate(self.pred,self.outcomes,resources())
        self.assertEqual(first.decision,'AWAITING_AUTHORIZED_CONFIRMATION')
        self.assertEqual(first.selected,'UM1UA1')
        self.assertEqual([p.evaluation_status for p in first.packages],['PASS']*3)
        self.assertEqual(calls[:6],[(p,s) for p in ('UM1','UA1','UM1UA1') for s in ('crossed','serial')])
        self.assertTrue(all(len(p.membership.included)==74 for p in first.packages))
        self.assertEqual(canonical_bytes(first),canonical_bytes(repeat))
        self.assertEqual(canonical_bytes(EvaluationResult.from_bytes(canonical_bytes(first))),canonical_bytes(first))

    def test_failed_authorized_combination_selects_um1(self):
        with fixed_statistics(fail_combination=True):result=evaluate(self.pred,self.outcomes,resources())
        self.assertEqual([p.evaluation_status for p in result.packages],['PASS','PASS','FAIL'])
        self.assertEqual(result.selected,'UM1');self.assertIsNotNone(result.packages[2].membership)

    def test_serial_sensitivity_failure_never_evaluates_combination(self):
        with fixed_statistics('SERIAL') as calls:result=evaluate(self.pred,self.outcomes,resources())
        self.assertEqual(result.decision,'DO_NOT_CONFIRM')
        for p in result.packages[:2]:
            self.assertIn('SERIAL_SENSITIVITY_FAIL',p.failures)
            self.assertTrue(all(e.crossed.passed for e in p.endpoints))
            self.assertTrue(any(not e.serial.passed for e in p.endpoints))
        self.assertEqual(calls,[(p,s) for p in ('UM1','UA1') for s in ('crossed','serial')])
        self.assertEqual(result.packages[2].evaluation_status,'NOT_EVALUATED')
        self.assertIsNone(result.packages[2].membership)
        self.assertEqual(result.packages[2].model_dump()['membership'],None)

    def test_one_undefined_draw_invalidates_entire_global_family(self):
        with fixed_statistics('UNDEFINED'):result=evaluate(self.pred,self.outcomes,resources())
        for p in result.packages[:2]:
            self.assertIn('crossed:global:UNDEFINED_REQUIRED_STATISTIC_OR_REPLICATE',p.failures)
            self.assertTrue(all(e.crossed.upper.value is None for e in p.endpoints if e.family=='global'))
            self.assertTrue(all(e.crossed.passed for e in p.endpoints if e.family=='subgroup'))
            self.assertTrue(all(c.replicates==9999 for c in p.clusters))
        self.assertEqual(result.decision,'DO_NOT_CONFIRM')

    def test_structurally_identical_coverage_still_has_family_adjustment(self):
        with fixed_statistics():result=evaluate(self.pred,self.outcomes,resources())
        endpoint=next(e for e in result.packages[0].endpoints if e.metric=='coverage' and e.season=='AGGREGATE' and e.group=='all')
        self.assertEqual(endpoint.crossed.status,'STRUCTURALLY_IDENTICAL')
        self.assertEqual(endpoint.crossed.upper.value,0.);self.assertTrue(endpoint.passed)
        with fixed_statistics('SERIAL'):result=evaluate(self.pred,self.outcomes,resources())
        endpoint=next(e for e in result.packages[0].endpoints if e.metric=='coverage' and e.season=='AGGREGATE' and e.group=='all')
        self.assertEqual(endpoint.serial.status,'STRUCTURALLY_IDENTICAL')
        self.assertGreater(endpoint.serial.family_q.value,0.);self.assertFalse(endpoint.passed)

    def test_actual_count_ceiling_reports_numerical_failure(self):
        rows=list(self.outcomes.rows);r=rows[0]
        rows[0]=r.model_copy(update={'fixtures':(r.fixtures[0].model_copy(update={'goals':10001}),)})
        pred,outcomes=bind(self.pred.rows,rows)
        with fixed_statistics():result=evaluate(pred,outcomes,resources())
        affected=[e for e in result.packages[1].endpoints if e.metric in ('goal_rps','mean_rps') and e.season in ('synthetic-a','AGGREGATE')]
        self.assertTrue(affected)
        self.assertTrue(all(e.population_status=='NUMERICAL_FAILURE' and e.crossed.status=='NUMERICAL_FAILURE' and not e.passed for e in affected))
        self.assertEqual(result.packages[1].evaluation_status,'FAIL');self.assertIsNone(result.packages[2].membership)

    def test_missing_bridge_on_validated_blank_only_player_fails_selection(self):
        rows=[];outcomes=[]
        for row,actual in zip(self.pred.rows,self.outcomes.rows):
            if row.element_id==1:
                row=replace_fixtures(row,()).model_copy(update={'player_code':None})
                actual=actual.model_copy(update={'fixtures':()})
            rows.append(row);outcomes.append(actual)
        pred,outcomes=bind(rows,outcomes)
        with fixed_statistics():result=evaluate(pred,outcomes,resources())
        self.assertEqual(result.decision,'DO_NOT_CONFIRM')
        self.assertTrue(all('serial:MISSING_IDENTITY_BRIDGE' in p.failures for p in result.packages[:2]))
        self.assertIsNone(result.packages[2].membership)

    def test_blank_only_availability_is_structurally_not_applicable(self):
        pred,actual=runner.synthetic_records(4);rows=[];outcomes=[]
        for row,outcome in zip(pred.rows,actual.rows):
            if row.element_id==1:
                row=replace_fixtures(row,()).model_copy(update={'status':'d'})
                outcome=outcome.model_copy(update={'fixtures':()})
            rows.append(row);outcomes.append(outcome)
        pred,actual=bind(rows,outcomes);result=evaluate(pred,actual,())
        for package in result.packages[:2]:
            populations=[p for p in package.populations if p.group=='availability:doubtful/chance-limited']
            self.assertTrue(all(p.status=='NOT_APPLICABLE' and p.universe==0 and p.universe_including_blanks==37 for p in populations))
            self.assertTrue(all(e.population_status=='NOT_APPLICABLE' for e in package.endpoints if e.group=='availability:doubtful/chance-limited'))
        # One registered nonblank row changes structural absence into insufficiency.
        rows=list(pred.rows);outcomes=list(actual.rows);rows[0]=self.pred.rows[0].model_copy(update={'status':'d'});outcomes[0]=self.outcomes.rows[0]
        pred,actual=bind(rows,outcomes);pair=_Pair(pred,actual,'UM1')
        self.assertEqual(pair.populations['synthetic-a','availability:doubtful/chance-limited'].status,'INSUFFICIENT_EVIDENCE')

if __name__=='__main__':unittest.main()
