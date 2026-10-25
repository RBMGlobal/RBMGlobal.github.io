"""Prove the actual workflow test entrypoint cannot pollute inherited job files."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNNER=Path(__file__).resolve().parents[1]/'run_tests.py'


class RunnerTests(unittest.TestCase):
    def test_inherited_summary_and_output_are_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);suite=root/'fixture';suite.mkdir()
            summary=root/'summary';output=root/'output';marker=root/'executed'
            summary.write_text('real summary\n');output.write_text('real_output=true\n')
            # A real child suite attempts the same writes as production helpers.
            (suite/'test_fixture.py').write_text('''import os
from pathlib import Path
import unittest
class Fixture(unittest.TestCase):
    def test_production_style_side_effect(self):
        for key in ('GITHUB_STEP_SUMMARY','GITHUB_OUTPUT'):
            if os.environ.get(key):
                with open(os.environ[key],'a') as stream:stream.write('FAKE FIXTURE EVIDENCE\\n')
        Path(os.environ['FIXTURE_MARKER']).write_text('executed')
''')
            env=dict(os.environ,GITHUB_STEP_SUMMARY=str(summary),GITHUB_OUTPUT=str(output),FIXTURE_MARKER=str(marker),PYTHONDONTWRITEBYTECODE='1')
            result=subprocess.run([sys.executable,str(RUNNER),'--start-directory',str(suite)],env=env,text=True,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(marker.read_text(),'executed')
            self.assertEqual(summary.read_text(),'real summary\n')
            self.assertEqual(output.read_text(),'real_output=true\n')

if __name__=='__main__':unittest.main()
