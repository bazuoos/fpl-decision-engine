"""Focused C1/C2 contract parity examples, not a complete C3 adapter."""
import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from fpl_decision_engine.research import task033c1 as c1
from fpl_decision_engine.research.c2.contracts import Forecast,CountDistribution,PredictionRecord,GOAL_POINTS,FORMULA_IDENTITIES
from fpl_decision_engine.research.c2.metrics import band_scores
from task033c2_naive_oracle import runner

ROOT=Path(__file__).resolve().parents[1]


class CompatibilityTests(unittest.TestCase):
    def test_lowercase_c1_availability_contract_maps_to_c2_statuses(self):
        row=runner.synthetic_records(1)[0].rows[0]
        for status,group in [('a','available'),('d','doubtful/chance-limited'),('i','doubtful/chance-limited'),('s','forced-zero'),('u','forced-zero')]:
            availability=c1.Availability.from_mapping({'status':status,'chance_of_playing_next_round':None,'known_pre_deadline':True,'is_target_next_round':True})
            raw=dataclasses.asdict(availability)
            c2=PredictionRecord.model_validate(row.model_copy(update={'status':raw['status'],'chance':raw['chance_of_playing_next_round'],
                'availability_known':raw['known_pre_deadline'],'chance_target_bound':raw['is_target_next_round']}))
            self.assertEqual(c2.status,status);self.assertEqual(c2.strata()['availability'],group)
        # C1 accepts uppercase aliases; C2 deliberately requires normalization by
        # a future adapter. This test does not add a runtime dependency or adapter.
        self.assertEqual(c1.Availability('A',None,True,True).multiplier_and_flags(),(1.,()))
        with self.assertRaises(ValueError):PredictionRecord.model_validate(row.model_copy(update={'status':'A'}))

    def test_scoring_constants_and_u0_contract_outputs_match(self):
        self.assertEqual(GOAL_POINTS,c1.GOAL_POINTS)
        self.assertEqual(FORMULA_IDENTITIES,(c1.U0_IDENTITY,c1.UM1_IDENTITY,c1.UA1_IDENTITY,c1.COMBINED_IDENTITY))
        for position in c1.POSITIONS:
            for fixture_count in (0,1,2):
                inputs=tuple(c1.U0FixtureInput(i+1,position,90,1.,1.,c1.Availability('a',None,True,True)) for i in range(fixture_count))
                p=c1.build_u0(inputs)
                f=Forecast(candidate='U0',complete=p.prediction_complete,expected_minutes=p.expected_minutes_for_evaluation,
                    appearance_points=p.appearance_points_for_evaluation,goals=p.expected_goals_for_evaluation,assists=p.expected_assists_for_evaluation,
                    modeled_points=p.modeled_points,minute_pmf=p.minute_pmf,appearance_probability=p.appearance_probability,start_probability=p.start_probability,
                    goal_distribution=CountDistribution(kind='POISSON',mean=p.goal_count_prediction.mean),
                    assist_distribution=CountDistribution(kind='POISSON',mean=p.assist_count_prediction.mean))
                self.assertEqual(f.modeled_points,fixture_count*(2+GOAL_POINTS[position]+3))
                self.assertEqual(band_scores(f.minute_pmf,90*fixture_count),(0.,0.))
                self.assertEqual(tuple(sum(f.minute_pmf[lo:hi+1 if hi is not None else None]) for lo,hi in c1.REGISTERED_MINUTE_BANDS),p.band_probabilities)
        for minutes in (0,1,29,30,59,60,89,90):
            pmf=tuple(float(i==minutes) for i in range(91))
            self.assertEqual(c1.expected_appearance_points(pmf),int(minutes>0)+int(minutes>=60))

    def test_importing_runner_does_not_mutate_subprocess_environment(self):
        # Explicit env argument, intentionally distinct from benchmark settings.
        env=dict(os.environ);env['OPENBLAS_NUM_THREADS']='2';env['VECLIB_MAXIMUM_THREADS']='3'
        code="""import importlib.util,os,json
before=dict(os.environ)
spec=importlib.util.spec_from_file_location('c2_runner','scripts/task033c2_synthetic_feasibility.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
assert dict(os.environ)==before
print(json.dumps({'unchanged':True}))
"""
        result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),{'unchanged':True})

if __name__=='__main__':unittest.main()
