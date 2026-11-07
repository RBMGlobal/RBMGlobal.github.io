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
