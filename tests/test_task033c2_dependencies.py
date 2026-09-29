"""Check the exact installed graph using parsers shipped by the pinned dev pip."""
import importlib.metadata
from pathlib import Path
import unittest
from pip._vendor import tomli
from pip._vendor.packaging.markers import Marker
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name

ROOT=Path(__file__).resolve().parents[1]


def locked_environment(lock):
    expected={};pending=[{'name':'fpl-decision-engine'}]
    while pending:
        edge=pending.pop()
        if 'marker' in edge and not Marker(edge['marker']).evaluate():continue
        candidates=[p for p in lock['package'] if p['name']==edge['name'] and
            ('version' not in edge or p['version']==edge['version']) and
            ('resolution-markers' not in p or any(Marker(m).evaluate() for m in p['resolution-markers']))]
        if len(candidates)!=1:raise AssertionError('Ambiguous locked dependency: '+edge['name'])
        package=candidates[0];name=canonicalize_name(package['name'])
        if name in expected:
            if expected[name]!=package['version']:raise AssertionError('Conflicting version')
            continue
        expected[name]=package['version'];pending.extend(package.get('dependencies',[]))
        if name=='fpl-decision-engine':pending.extend(package['dev-dependencies']['dev'])
    return expected


class DependencyTests(unittest.TestCase):
    def test_numpy_is_direct_in_project_lock_and_installed_metadata(self):
        project=tomli.loads((ROOT/'pyproject.toml').read_text())
        lock=tomli.loads((ROOT/'uv.lock').read_text())
        self.assertIn('numpy>=2.2,<3',project['project']['dependencies'])
        self.assertEqual(project['tool']['uv']['required-version'],'==0.12.7')
        self.assertEqual(project['tool']['uv']['python-downloads'],'never')
        root=next(p for p in lock['package'] if p['name']=='fpl-decision-engine')
        numpy=[p for p in root['dependencies'] if p['name']=='numpy']
        self.assertEqual({p['version'] for p in numpy},{'2.2.6','2.4.6','2.5.2'})
        self.assertEqual(sum(Marker(p['marker']).evaluate() for p in numpy),1)
        req=next(Requirement(r) for r in importlib.metadata.requires('fpl-decision-engine') if Requirement(r).name=='numpy')
        self.assertEqual(str(req.specifier),'<3,>=2.2')

    def test_installed_environment_exactly_matches_current_lock(self):
        expected=locked_environment(tomli.loads((ROOT/'uv.lock').read_text()))
        installed={canonicalize_name(d.metadata['Name']):d.version for d in importlib.metadata.distributions()}
        self.assertEqual(installed,expected)

if __name__=='__main__':unittest.main()
