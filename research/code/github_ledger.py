"""One central, revision-checked budget ledger committed before any paid request."""
import base64
import json
import os
import re
import requests
from common.budget import validate_ledger

OWNER = 'RBMGlobal/RBMGlobal.github.io'
PATH = 'research/state/budget.json'


class GitHubLedgerStore:
    def __init__(self, session=None):
        if (os.environ.get('GITHUB_REPOSITORY') != OWNER
                or os.environ.get('GITHUB_REF') != 'refs/heads/main'
                or os.environ.get('GITHUB_EVENT_NAME') not in ('schedule', 'workflow_dispatch')):
            raise RuntimeError('Paid research is restricted to the central repository main workflow')
        token = os.environ.get('GH_TOKEN', '')
        if not token:
            raise RuntimeError('Repository-scoped ledger credential is missing')
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update(Authorization='Bearer ' + token,
                                    Accept='application/vnd.github+json',
                                    **{'X-GitHub-Api-Version': '2022-11-28'})
        self.url = 'https://api.github.com/repos/' + OWNER + '/contents/' + PATH

    def read(self):
        response = self.session.get(self.url, params={'ref': 'main'}, timeout=30, allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError('Cannot read the committed budget ledger; no paid call permitted')
        try:
            obj = response.json()
            if (obj.get('type') != 'file' or obj.get('encoding') != 'base64'
                    or obj.get('path') != PATH or type(obj.get('size')) is not int
                    or not 0 < obj['size'] <= 1_000_000
                    or not re.fullmatch(r'[0-9a-f]{40}', obj.get('sha', ''))):
                raise ValueError()
            encoded = ''.join(obj['content'].split())
            body = base64.b64decode(encoded, validate=True)
            if len(body) != obj['size']:
                raise ValueError()
            state = validate_ledger(json.loads(body))
        except Exception:
            raise RuntimeError('Committed budget ledger is malformed; no paid call permitted') from None
        return state, obj['sha']

    def persist(self, state, expected_revision):
        validate_ledger(state)
        current, sha = self.read()
        if current['revision'] != expected_revision or state['revision'] != expected_revision + 1:
            raise RuntimeError('Budget ledger changed concurrently')
        content = (json.dumps(state, indent=2) + '\n').encode()
        response = self.session.put(self.url, json={
            'message': 'Account for bounded AI research request', 'sha': sha, 'branch': 'main',
            'content': base64.b64encode(content).decode()}, timeout=30, allow_redirects=False)
        # Never retry an ambiguous mutation, and never proceed on an unconfirmed reserve.
        if response.status_code not in (200, 201):
            raise RuntimeError('Budget ledger commit was not confirmed')
        confirmed, _ = self.read()
        return confirmed == state
