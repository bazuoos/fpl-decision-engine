"""Independent serial stream examples with deliberately conflicting ID/code order."""
import hashlib
from types import SimpleNamespace
import unittest
import numpy as np
from fpl_decision_engine.research.c2.contracts import canonical_bytes
from fpl_decision_engine.research.c2.inference import resamples


def universe():
    # Ascending IDs put c2 before c1; the frozen code ordering does the reverse.
    mapping={'a':((1,'c2'),(2,'c1'),(88,'zz')), 'b':((2,'c3'),(9,'c2'),(88,'zz'))}
    return tuple(SimpleNamespace(season=s,gameweek=g,element_id=i,player_code=c,
        fixture_ids=() if c=='zz' else (g*100+i,))
        for s,items in mapping.items() for g in range(2,39) for i,c in items)


class SerialUnionTests(unittest.TestCase):
    def assert_stream(self,rows,stage,codes,seed):
        self.assertEqual(codes,sorted({r.player_code for r in rows},key=lambda c:c.encode('utf-8')))
        rng=np.random.Generator(np.random.PCG64(seed));digest=hashlib.sha256();count=0
        for pm,gm in resamples(rows,stage,'serial'):
            copies=np.bincount(rng.integers(0,len(codes),size=len(codes),dtype=np.int64),minlength=len(codes))
            expected=dict(zip(codes,map(int,copies)))
            for season in sorted(pm):
                for row in rows:
                    if row.season==season:self.assertEqual(pm[season][row.element_id],expected[row.player_code])
                starts=rng.integers(0,34,size=10,dtype=np.int64)+2
                expanded=[int(g) for start in starts for g in range(start,start+4)][:37]
                self.assertEqual(gm[season],{g:expanded.count(g) for g in range(2,39)})
            payload=[[s,[[i,int(v)] for i,v in sorted(pm[s].items())],[[g,int(v)] for g,v in sorted(gm[s].items())]] for s in sorted(pm)]
            digest.update(canonical_bytes(payload));count+=1
        self.assertEqual(count,9999)
        return digest.hexdigest()

    def test_union_four_includes_blank_only_and_repeats_exactly(self):
        first=self.assert_stream(universe(),'DEVELOPMENT',['c1','c2','c3','zz'],330431)
        self.assertEqual(first,self.assert_stream(universe(),'DEVELOPMENT',['c1','c2','c3','zz'],330431))

    def test_blank_only_changes_stream_and_code_order_controls_draw(self):
        pm,gm=next(resamples(universe(),'DEVELOPMENT','serial'))
        wrong_pm,wrong_gm=next(resamples(tuple(r for r in universe() if r.fixture_ids),'DEVELOPMENT','serial'))
        rng=np.random.Generator(np.random.PCG64(330431))
        counts=np.bincount(rng.integers(0,4,size=4,dtype=np.int64),minlength=4)
        self.assertEqual(pm['a'][2],counts[0]);self.assertEqual(pm['a'][1],counts[1])
        self.assertNotEqual(counts[0],counts[1])
        self.assertEqual(pm['a'][88],counts[3]);self.assertEqual(pm['b'][9],pm['a'][1])
        self.assertNotEqual((pm,gm),(wrong_pm,wrong_gm));self.assertNotEqual(gm,wrong_gm)

    def test_confirmation_union_is_single_season_including_blank_only(self):
        rows=tuple(r for r in universe() if r.season=='a')
        self.assert_stream(rows,'CONFIRMATION',['c1','c2','zz'],330432)

    def test_missing_or_invalid_bridge_on_blank_row_fails_closed(self):
        for invalid in (None,'',False):
            rows=list(universe());blank=next(i for i,r in enumerate(rows) if not r.fixture_ids)
            rows[blank]=SimpleNamespace(**{**vars(rows[blank]),'player_code':invalid})
            with self.assertRaisesRegex(ValueError,'MISSING_IDENTITY_BRIDGE'):
                next(resamples(rows,'DEVELOPMENT','serial'))

if __name__=='__main__':unittest.main()
