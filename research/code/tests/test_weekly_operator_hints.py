import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from run_research import weekly_operator_hints
class LeadTests(unittest.TestCase):
    def test_scope_and_publisher_gates(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); p=root/'research/state/weekly-discovery-leads.json';p.parent.mkdir(parents=True)
            issue,start,end=map(dt.date.fromisoformat,['2026-11-02','2026-10-26','2026-11-01'])
            policy={'publishers':{'example.org':'Example'}}
            self.assertEqual(weekly_operator_hints(root,issue,start,end,policy),[])
            r={'issue_date':str(issue),'reporting_window':{'from':str(start),'to':str(end)},'urls':['https://example.org/report']}
            p.write_text(json.dumps(r));self.assertEqual(weekly_operator_hints(root,issue,start,end,policy),r['urls'])
            self.assertEqual(weekly_operator_hints(root,issue+dt.timedelta(days=7),start,end,policy),[])
            r['urls']=['https://unapproved.test/report'];p.write_text(json.dumps(r))
            with self.assertRaises(ValueError):weekly_operator_hints(root,issue,start,end,policy)
            r['urls']=['https://example.org/report']*11;p.write_text(json.dumps(r))
            with self.assertRaises(ValueError):weekly_operator_hints(root,issue,start,end,policy)
            r['urls']=['https://example.org/report'];r['reporting_window']['to']='2026-11-02';p.write_text(json.dumps(r))
            with self.assertRaises(ValueError):weekly_operator_hints(root,issue,start,end,policy)

class ProposalTests(unittest.TestCase):
    def test_recorded_proposals_stay_bound_to_urls_week_and_retrieval(self):
        from run_research import weekly_operator_proposals
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); p=root/'research/state/weekly-discovery-leads.json';p.parent.mkdir(parents=True)
            issue,start,end=map(dt.date.fromisoformat,['2026-11-02','2026-10-26','2026-11-01'])
            policy={'publishers':{'example.org':'Example'}}
            source={'url':'https://example.org/report','title':'Report','published_at':'2026-10-30',
                    'publication_date_quote':'October 30, 2026','retrieved_at':'2026-11-19T09:00:00+00:00','text_sha256':'a'*64}
            r={'issue_date':str(issue),'reporting_window':{'from':str(start),'to':str(end)},
               'urls':[source['url']],'source_proposals':[source]}
            p.write_text(json.dumps(r))
            got=weekly_operator_proposals(root,issue,start,end,policy)
            self.assertEqual(got[0]['url'],source['url'])
            self.assertNotIn('previous_independent_review_approved',got[0])
            self.assertEqual(weekly_operator_proposals(root,issue+dt.timedelta(days=7),start,end,policy),[])
            source['url']='https://example.org/not-recorded';p.write_text(json.dumps(r))
            with self.assertRaises(ValueError): weekly_operator_proposals(root,issue,start,end,policy)
            source['url']=r['urls'][0];source['text_sha256']='invalid';p.write_text(json.dumps(r))
            with self.assertRaises(ValueError): weekly_operator_proposals(root,issue,start,end,policy)
