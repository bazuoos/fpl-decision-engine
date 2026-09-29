import hashlib
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fpl_decision_engine.research.c2.contracts import canonical_bytes, PredictionBatch, JoinedOutcomes, Forecast, Membership
from fpl_decision_engine.research.c2.evaluation import evaluate,_Pair as Pair
from fpl_decision_engine.research.c2.results import EvaluationResult,PackageResult

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('c2_spike',ROOT/'scripts/task033c2_synthetic_feasibility.py')
spike=importlib.util.module_from_spec(spec);spec.loader.exec_module(spike)


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pred,cls.actual=spike.synthetic_records(4)

    def test_undersized_evaluation_fails_without_combination_digest(self):
        result=evaluate(self.pred,self.actual,())
        self.assertEqual(result.decision,'DO_NOT_CONFIRM')
        self.assertEqual(result.packages[2].evaluation_status,'NOT_EVALUATED')
        self.assertIsNone(result.packages[2].membership)
        self.assertEqual(result.decision_corpus,'NOT_EVALUATED')
        self.assertFalse(result.eligibility_established)
        raw=canonical_bytes(result)
        self.assertEqual(canonical_bytes(EvaluationResult.from_bytes(raw)),raw)
        self.assertTrue(all(e.population_status in ('INSUFFICIENT_EVIDENCE','NOT_APPLICABLE') for e in result.packages[0].endpoints))

    def test_deterministic_repeat_and_outcome_perturbation_do_not_change_predictions(self):
        before=canonical_bytes(self.pred)
        first=evaluate(self.pred,self.actual,());second=evaluate(self.pred,self.actual,())
        self.assertEqual(canonical_bytes(first),canonical_bytes(second))
        row=self.actual.rows[0];f=row.fixtures[0].model_copy(update={'goals':100})
        changed=self.actual.model_copy(update={'rows':(row.model_copy(update={'fixtures':(f,)}),)+self.actual.rows[1:]})
        after=evaluate(self.pred,changed,())
        self.assertNotEqual(first.outcomes_sha256,after.outcomes_sha256)
        self.assertEqual(canonical_bytes(self.pred),before)
        self.assertEqual(first.packages[2].membership,after.packages[2].membership)

    def test_combination_evaluator_not_called_before_both_components_pass(self):
        calls=[];original=Pair.__init__
        def traced(instance,pred,actual,candidate,authorization=None):
            calls.append(candidate);original(instance,pred,actual,candidate,authorization)
        with patch.object(Pair,'__init__',traced):evaluate(self.pred,self.actual,())
        self.assertEqual(calls,['UM1','UA1'])

    def test_direct_combination_evaluation_rejected(self):
        with self.assertRaisesRegex(ValueError,'COMBINATION_NOT_AUTHORIZED'):
            Pair(self.pred,self.actual,'UM1UA1')

    def test_reject_prediction_outcome_hash_mismatch(self):
        actual=self.actual.model_copy(update={'prediction_bytes_sha256':'0'*64})
        with self.assertRaises(ValueError):evaluate(self.pred,actual,())

    def test_unknown_outcomes_and_inconsistent_fixture_rejected(self):
        row=self.actual.rows[0].model_copy(update={'element_id':999})
        with self.assertRaises(ValueError):evaluate(self.pred,self.actual.model_copy(update={'rows':(row,)}),())
        row=self.actual.rows[0].model_copy(update={'fixtures':()})
        with self.assertRaises(ValueError):evaluate(self.pred,self.actual.model_copy(update={'rows':(row,)}),())

    def test_duplicate_and_unknown_predictions_rejected(self):
        raw=self.pred.model_dump(mode='json');raw['rows'].append(raw['rows'][0])
        with self.assertRaises(ValueError):PredictionBatch.from_bytes(canonical_bytes(raw))
        raw=self.pred.model_dump(mode='json');raw['rows'][0]['future_target']=90
        with self.assertRaises(ValueError):PredictionBatch.from_bytes(canonical_bytes(raw))

    def test_no_fabricated_empty_combination_membership(self):
        with self.assertRaises(ValueError):
            PackageResult(candidate='UM1UA1',evaluation_status='NOT_EVALUATED',membership=Membership.create('UM1UA1',()),resource_status='NOT_EVALUATED')

    def test_pair_specific_membership(self):
        pred,actual=spike.synthetic_records(50)
        rows=[]
        for row in pred.rows:
            if row.element_id==1:
                forecasts=list(row.forecasts)
                forecasts[2]=forecasts[2].model_copy(update={'complete':False})
                row=row.model_copy(update={'forecasts':tuple(forecasts)})
            rows.append(row)
        digest=hashlib.sha256(canonical_bytes([r.model_dump(mode='json') for r in rows])).hexdigest()
        pred=pred.model_copy(update={'rows':tuple(rows),'prediction_bytes_sha256':digest})
        actual=actual.model_copy(update={'prediction_bytes_sha256':digest})
        a=Pair(pred,actual,'UM1').membership;b=Pair(pred,actual,'UA1').membership
        self.assertEqual(len(a.included),74);self.assertEqual(len(b.included),0)
        self.assertNotEqual(a.sha256,b.sha256)

    def test_required_positions_and_absent_availability_states(self):
        pair=Pair(self.pred,self.actual,'UM1')
        for s in pair.seasons:
            for p in ('GK','DEF','MID','FWD'):
                self.assertEqual(pair.populations[s,'position:'+p].status,'INSUFFICIENT_EVIDENCE')
            for availability in ('forced-zero','doubtful/chance-limited','unknown'):
                self.assertEqual(pair.populations[s,'availability:'+availability].status,'NOT_APPLICABLE')

    def test_forged_incomplete_numeric_totals_do_not_enter_pairs(self):
        f=self.pred.rows[0].forecasts[0]
        self.assertIsNotNone(f.modeled_points)
        with self.assertRaises(ValueError):
            Forecast.model_validate(f.model_copy(update={'complete':True,'goals':None}))

class AdditionalEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pred,cls.actual=spike.synthetic_records(4)

    def test_conditional_dispatch_for_both_component_pass_cases(self):
        for combined_status,expected in [('PASS','UM1UA1'),('FAIL','UM1')]:
            calls=[]
            def fake_run(instance,resource):
                calls.append(instance.candidate)
                return SimpleNamespace(candidate=instance.candidate,evaluation_status=combined_status if instance.candidate=='UM1UA1' else 'PASS')
            with patch.object(Pair,'run',fake_run),patch('fpl_decision_engine.research.c2.evaluation.EvaluationResult',side_effect=lambda **kw:kw):
                result=evaluate(self.pred,self.actual,())
            self.assertEqual(calls,['UM1','UA1','UM1UA1'])
            self.assertEqual(result['selected'],expected)
            self.assertEqual(result['decision'],'AWAITING_AUTHORIZED_CONFIRMATION')

    def test_confirmation_uses_only_frozen_selected_package(self):
        rows=tuple(r for r in self.pred.rows if r.season=='synthetic-a')
        digest=hashlib.sha256(canonical_bytes([r.model_dump(mode='json') for r in rows])).hexdigest()
        pred=self.pred.model_copy(update={'rows':rows,'stage':'CONFIRMATION','prediction_bytes_sha256':digest})
        actual=self.actual.model_copy(update={'rows':tuple(r for r in self.actual.rows if r.season=='synthetic-a'),'prediction_bytes_sha256':digest})
        result=evaluate(pred,actual,(),confirmation_candidate='UM1UA1')
        self.assertEqual([p.evaluation_status for p in result.packages],['NOT_EVALUATED','NOT_EVALUATED','FAIL'])
        self.assertEqual(result.decision,'DO_NOT_PROMOTE')
        self.assertIsNone(result.packages[0].membership)

    def test_output_schema_validates_actual_result(self):
        import json,jsonschema
        result=evaluate(self.pred,self.actual,())
        schema=json.loads((ROOT/'contracts/research/task033c2_result.schema.json').read_text())
        jsonschema.validate(result.model_dump(mode='json'),schema)

    def test_availability_precedence_all_classes(self):
        row=self.pred.rows[0]
        for fields,expected in [({'status':'s'},'forced-zero'),({'status':'a','chance':0},'forced-zero'),
                ({'status':'d','chance':None},'doubtful/chance-limited'),({'chance':50},'doubtful/chance-limited'),
                ({'availability_known':False,'status':'s'},'unknown'),({'status':None},'unknown'),({},'available')]:
            self.assertEqual(row.model_copy(update=fields).strata()['availability'],expected)

    def test_strata_boundaries_and_missing_context(self):
        row=self.pred.rows[0]
        for minutes,expected in [(0,'0'),(1,'1-90'),(90,'1-90'),(91,'91-270'),(270,'91-270'),(271,'271-450'),(451,'451-900'),(901,'901+'),(None,'UNKNOWN')]:
            self.assertEqual(row.model_copy(update={'prior_minutes':minutes}).strata()['prior_minutes'],expected)
        self.assertEqual(row.model_copy(update={'previous_minutes':None}).strata()['previous_minutes'],'MISSING_CONTEXT')

    def test_no_filesystem_or_socket_calls_during_evaluation(self):
        import builtins,socket
        with patch.object(builtins,'open',side_effect=AssertionError('filesystem access')),patch.object(socket,'socket',side_effect=AssertionError('network access')):
            result=evaluate(self.pred,self.actual,())
        self.assertEqual(result.decision,'DO_NOT_CONFIRM')

    def test_vectorized_deciles_match_explicit_copies(self):
        import json, math
        from fpl_decision_engine.research.c2.metrics import count_deciles
        pair=Pair(self.pred,self.actual,'UA1')
        pm={season:{1:0,2:3,3:1,4:2} for season in pair.seasons}
        gm={season:{g:g%3 for g in range(2,39)} for season in pair.seasons}
        table=json.loads(pair.calibration_draw(pm,gm))
        # First 2 seasons x 2 sides x 2 binary events x 10 fixed bins.
        count_rows=table[80:];plans=pair.calibration_plan[0];expected=[]
        tables=[]
        for season,gw,event,side,selected in plans:
            forecasts=pair.forecasts if side==0 else pair.controls
            tables.append(count_deciles([getattr(forecasts[i],event+'s') for i in selected],
                pair.actual_columns[event+'s'][selected],pair.ids[selected],
                [pm[season][pair.rows[i].element_id] for i in selected]))
        for b in range(10):
            for (season,gw,_,_,_),bins in zip(plans,tables):
                v=bins[b];g=gm[season][gw]
                expected.append([v['weight']*g,v['forecast'] if g and math.isfinite(v['forecast']) else None,
                    v['observed'] if g and math.isfinite(v['observed']) else None])
        self.assertEqual(len(count_rows),len(expected))
        for got,want in zip(count_rows,expected):
            self.assertEqual(got[0],want[0])
            for actual,reference in zip(got[1:],want[1:]):
                if reference is None:self.assertIsNone(actual)
                else:self.assertAlmostEqual(actual,reference,places=14)

    def test_resource_duplicates_and_malformed_fail_before_scoring(self):
        from fpl_decision_engine.research.c2.contracts import ResourceGate
        gate=ResourceGate(candidate='UM1',status='FAIL',evidence_sha256=None,scope='SYNTHETIC_C2_EVALUATION')
        for resources in ((gate,gate),({'candidate':'UM1','status':'PASS'},),[]):
            with self.assertRaises(ValueError):evaluate(self.pred,self.actual,resources)

    def test_boolean_starts_nonfinite_values_and_inconsistent_means_rejected(self):
        from fpl_decision_engine.research.c2.contracts import FixtureOutcome,CountDistribution
        with self.assertRaises(ValueError):FixtureOutcome(fixture_id=1,minutes=0,starts=True,goals=0,assists=0)
        with self.assertRaises(ValueError):CountDistribution(kind='POISSON',mean=float('inf'))
        with self.assertRaises(ValueError):CountDistribution(kind='GAMMA_MIXTURE',mean=3.,weights=(1.,),shapes=(1.,),rates=(1.,),exposure=1.)

    def test_absent_in_one_season_has_no_aggregate_subgroup_claim(self):
        from fpl_decision_engine.research.c2.inference import endpoint_family
        rows=tuple(r.model_copy(update={'status':'d'}) if r.season=='synthetic-a' else r for r in self.pred.rows)
        digest=hashlib.sha256(canonical_bytes([r.model_dump(mode='json') for r in rows])).hexdigest()
        pred=self.pred.model_copy(update={'rows':rows,'prediction_bytes_sha256':digest})
        actual=self.actual.model_copy(update={'prediction_bytes_sha256':digest})
        pair=Pair(pred,actual,'UM1')
        endpoint=next(e for e in endpoint_family('UM1',pair.seasons) if e.group=='availability:doubtful/chance-limited' and e.season=='AGGREGATE')
        self.assertEqual(pair._endpoint_status(endpoint),'NOT_APPLICABLE')
        self.assertEqual(pair.populations['synthetic-a','availability:doubtful/chance-limited'].status,'INSUFFICIENT_EVIDENCE')

if __name__=='__main__':unittest.main()
