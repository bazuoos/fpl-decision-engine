import json
from pathlib import Path
import unittest
import jsonschema
from fpl_decision_engine.research.c2.contracts import PredictionBatch,JoinedOutcomes,ResourceGate
from fpl_decision_engine.research.c2.results import EvaluationResult

ROOT=Path(__file__).resolve().parents[1]
MODELS={'predictions':PredictionBatch,'outcomes':JoinedOutcomes,'resources':ResourceGate,'result':EvaluationResult}


class SchemaTests(unittest.TestCase):
    def test_schemas_are_valid_closed_and_match_strict_runtime_contracts(self):
        for name,model in MODELS.items():
            schema=json.loads((ROOT/f'contracts/research/task033c2_{name}.schema.json').read_text())
            jsonschema.Draft202012Validator.check_schema(schema)
            self.assertEqual(schema,model.model_json_schema())
            self.assertFalse(schema['additionalProperties'])
            for obj in schema.get('$defs',{}).values():
                if obj.get('type')=='object':self.assertFalse(obj['additionalProperties'])

if __name__=='__main__':unittest.main()
