import hashlib
import unittest

from fpl_decision_engine.research.c2.contracts import (
    Identity, Membership, Statistic, ResourceGate, membership_bytes, canonical_bytes,
)


class MembershipContractTests(unittest.TestCase):
    def test_exact_bytes_and_digest_oracle(self):
        identities=(Identity(season='synthetic-b',gameweek=10),Identity(season='synthetic-a',gameweek=2))
        expected=b'[{"gameweek":2,"season":"synthetic-a"},{"gameweek":10,"season":"synthetic-b"}]\n'
        self.assertEqual(membership_bytes(identities),expected)
        result=Membership.create('UM1',identities)
        self.assertEqual(result.sha256,hashlib.sha256(expected).hexdigest())
        self.assertEqual(result.control,'U0')

    def test_duplicates_rejected(self):
        i=Identity(season='x',gameweek=2)
        with self.assertRaises(ValueError):
            membership_bytes((i,i))

    def test_numeric_gameweek_order(self):
        result=Membership.create('UA1',(Identity(season='é',gameweek=10),Identity(season='é',gameweek=2)))
        self.assertEqual([i.gameweek for i in result.included],[2,10])
        self.assertIn('é'.encode(),membership_bytes(result.included))

    def test_unknown_duplicate_malformed_and_nonfinite_json(self):
        for raw in (b'{"season":"x","gameweek":2,"extra":0}',b'{"season":"x","gameweek":2,"gameweek":3}',
                    b'{"season":"x","gameweek":NaN}',b'{"season":"x","gameweek":true}',b'[]',b'\xff'):
            with self.subTest(raw=raw),self.assertRaises((ValueError,UnicodeError)):
                Identity.from_bytes(raw)

    def test_digest_cannot_be_relabelled_without_identity_in_result(self):
        one=Membership.create('UM1',(Identity(season='x',gameweek=2),))
        two=Membership.create('UA1',(Identity(season='x',gameweek=3),))
        self.assertNotEqual(one.sha256,two.sha256)
        self.assertNotEqual(one.candidate,two.candidate)
        with self.assertRaises(ValueError):
            Membership(candidate='UM1',included=one.included,sha256='0'*64)

    def test_infinity_retained_as_explicit_status(self):
        score=Statistic.number(float('inf'))
        self.assertEqual(score.status,'POSITIVE_INFINITY')
        self.assertIsNone(score.value)
        self.assertIn(b'POSITIVE_INFINITY',canonical_bytes(score))
        with self.assertRaises(ValueError):
            canonical_bytes({'score':float('inf')})

    def test_resource_success_requires_supplied_evidence(self):
        with self.assertRaises(ValueError):
            ResourceGate(candidate='UM1',status='PASS',scope='SYNTHETIC_C2_EVALUATION',evidence_sha256=None)

    def test_result_status_must_agree_with_value(self):
        with self.assertRaises(ValueError):
            Statistic(status='UNDEFINED',value=0.)


if __name__=='__main__':
    unittest.main()
