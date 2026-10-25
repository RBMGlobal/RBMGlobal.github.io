import copy
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import site_health as h
import sync_sentiment_heat as sync

TODAY = date(2026, 10, 25)
HEAT = {'from':'2026-10-01','to':'2026-10-23','count':1,'points':[[9,7,3]],'method':'Machine-coded news, not polling'}
BODY = json.dumps(HEAT).encode()

def response(data, status=200):
    return h.Response('https://rbmglobal.co.uk/test', status, json.dumps(data).encode())

def release():
    files={name:{'bytes':len(BODY),'sha256':h.sha(BODY)} for name in ['index.html','lastweek.png','sentiment-gdelt.json']}
    return {'schema_version':1,'generated_at':'2026-10-25T11:45:00Z','coverage':{
      'gdelt':{'input_from':'2026-10-01','input_through':'2026-10-24','max_event_date':'2026-10-23'},
      'ucdp':{'min_event_date':'2025-01-01','max_event_date':'2026-09-30'},
      'heat':{'from':'2026-10-01','to':'2026-10-23'}},'files':files}

class HealthTests(unittest.TestCase):
    def test_all_37_states_six_zones_and_no_duplicates(self):
        data={'assessment_updated':'2026-09-27','states':[{'name':s,'zone':'NC','president':'mixed','nsa':'cold'} for s in sorted(h.STATES)],'zones':[{'id':z} for z in sorted(h.ZONES)]}
        self.assertIs(h.validate_sentiment(data),data)
        data['states'][-1]=data['states'][0]
        with self.assertRaisesRegex(ValueError,'37 unique'):h.validate_sentiment(data)

    def test_missing_zone_rejected(self):
        data={'states':[{'name':s} for s in h.STATES],'zones':[{'id':z} for z in h.ZONES if z!='SW']}
        with self.assertRaisesRegex(ValueError,'six unique'):h.validate_sentiment(data)

    def test_heat_rejects_empty_nan_bool_mismatched_count(self):
        for mutate in [lambda d:d.update(points=[]),lambda d:d['points'][0].__setitem__(0,float('nan')),lambda d:d['points'][0].__setitem__(1,True),lambda d:d.update(count=2),lambda d:d.update(to='2026-09-01')]:
            data=copy.deepcopy(HEAT);mutate(data)
            with self.subTest(data=data), self.assertRaises(ValueError):h.validate_heat(data)

    def test_input_coverage_is_not_max_event_date(self):
        through,ucdp=h.validate_release(release(),TODAY)
        self.assertEqual(through,date(2026,10,24));self.assertEqual(ucdp,date(2026,9,30))
        data,meta=sync.verified_heat(response(release()),h.Response('',200,BODY),response(release()),TODAY)
        self.assertEqual(data['to'],'2026-10-23')

    def test_future_and_reversed_coverage_rejected(self):
        for field,value in [('input_through','2026-10-26'),('input_from','2026-10-25'),('max_event_date','2026-10-25')]:
            meta=release();meta['coverage']['gdelt'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):h.validate_release(meta,TODAY)

    def test_release_hash_rejects_changed_or_truncated_file(self):
        for body in [BODY[:-1],BODY.replace(b'9',b'8')]:
            with self.assertRaisesRegex(ValueError,'bytes/hash'):h.verify_file(release(),'sentiment-gdelt.json',body)

    def test_safe_array_parser_handles_brackets_in_strings(self):
        body='const EVENTS = [{"d":"2026-01-01","la":1,"lo":2,"text":"literal ]; text and object }; text"}]; const GDELT_EVENTS = [{"d":"2026-10-23","la":2,"lo":3}];'
        self.assertEqual(h.validate_events(body)['EVENTS'][0]['text'],'literal ]; text and object }; text')
        with self.assertRaises(ValueError):h.parse_array('const EVENTS = [];','EVENTS')

    def test_retired_redirect_needs_exact_target_and_no_old_app(self):
        good=h.Response('https://rbmglobal.co.uk/ped-tool/',200,b'<meta http-equiv="refresh" content="0;url=beta/">')
        h.check_redirect(good,good.url,'https://rbmglobal.co.uk/ped-tool/beta/')
        with self.assertRaises(ValueError):h.check_redirect(good,good.url,'https://example.org/')
        good.body+=b'<div id="map"></div>'
        with self.assertRaisesRegex(ValueError,'working application'):h.check_redirect(good,good.url,'https://rbmglobal.co.uk/ped-tool/beta/')

    def test_http_error_is_not_html_success(self):
        with self.assertRaisesRegex(ValueError,'HTTP 404'):h.check_html(h.Response('',404,b'<html></html>'))

    def test_pdf_requires_complete_document(self):
        h.check_pdf(b'%PDF-1.7\n'+b'0'*6000+b'\n%%EOF')
        with self.assertRaises(ValueError):h.check_pdf(b'%PDF-1.7\n'+b'0'*6000)

    def test_issue_and_publication_dates_remain_separate(self):
        issue,published=h.brief_dates('<p>Issue date: 19 October 2026. Published: 21 October 2026.</p>')
        self.assertEqual((issue,published),(date(2026,10,19),date(2026,10,21)))

    def test_monday_editorial_deadline(self):
        self.assertEqual(h.due_brief(date(2026,10,26),12),date(2026,10,19))
        self.assertEqual(h.due_brief(date(2026,10,26),13),date(2026,10,26))
        self.assertEqual(h.due_brief(TODAY,16),date(2026,10,19))

    def test_failure_does_not_stop_other_checks(self):
        report=h.Report();report.check('one',lambda:h.require(False,'broken'));report.check('two',lambda:42)
        self.assertTrue(report.failed);self.assertEqual(report.items[1]['status'],'PASS')

class SyncTests(unittest.TestCase):
    def validate(self,meta=None,body=BODY,confirm=None,today=TODAY):
        meta=meta or release()
        return sync.verified_heat(response(meta),h.Response('',200,body),response(confirm or meta),today)

    def test_stale_source_does_not_pass_with_new_generation_time(self):
        meta=release();meta['coverage']['gdelt']['input_through']='2026-10-23'
        with self.assertRaisesRegex(ValueError,'retain current'):self.validate(meta)

    def test_publication_race_rejected(self):
        confirm=release();confirm['generated_at']='2026-10-25T11:46:00Z'
        with self.assertRaisesRegex(ValueError,'changed during'):self.validate(confirm=confirm)

    def test_falsely_declared_heat_window_rejected(self):
        meta=release();meta['coverage']['heat']['to']='2026-10-24'
        with self.assertRaisesRegex(ValueError,'window differs'):self.validate(meta)

    def test_missing_release_and_invalid_heat_keep_last_good(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gdelt.json';target.write_bytes(b'last good')
            for replies in [ [response({},404),h.Response('',200,BODY),response(release())], [response(release()),h.Response('',200,BODY[:-1]),response(release())] ]:
                with patch.object(sync,'fetch',side_effect=replies),self.assertRaises(ValueError):sync.sync(target)
                self.assertEqual(target.read_bytes(),b'last good')
                self.assertEqual(list(Path(directory).iterdir()),[target])

    def test_verified_file_is_written_exactly_and_second_sync_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gdelt.json';meta=release()
            # Fixture source remains current independently of the actual test clock.
            actual=sync.datetime.now(sync.ZoneInfo('Europe/London')).date()
            meta['generated_at']=actual.isoformat()+'T00:00:00Z';meta['coverage']['gdelt']['input_through']=(actual-timedelta(days=1)).isoformat()
            replies=[response(meta),h.Response('',200,BODY),response(meta)]
            with patch.object(sync,'fetch',side_effect=replies):self.assertTrue(sync.sync(target)['changed'])
            self.assertEqual(target.read_bytes(),BODY)
            with patch.object(sync,'fetch',side_effect=replies):self.assertFalse(sync.sync(target)['changed'])

if __name__=='__main__':unittest.main()
