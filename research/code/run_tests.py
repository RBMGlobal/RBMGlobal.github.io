"""Run each independent fixture suite without leaking synthetic status to Actions."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
environment = dict(os.environ)
for key in ('GITHUB_STEP_SUMMARY', 'GITHUB_OUTPUT', 'OPENAI_API_KEY', 'GH_TOKEN',
            'ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'AI_RESEARCH_ENABLED'):
    environment.pop(key, None)
for suite in ('common/tests', 'sentiment/tests', 'brief/tests', 'tests'):
    directory = root / suite
    if directory.is_dir():
        suite_env = dict(environment, PYTHONPATH=os.pathsep.join((str(root), str(directory.parent))))
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(directory), '-v'],
                       cwd=directory.parent, env=suite_env, check=True)
