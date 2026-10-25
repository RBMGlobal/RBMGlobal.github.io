import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import build_company_site as builder
import site_health as h
import verify_company_live as live

class PublicationTests(unittest.TestCase):
    def test_build_excludes_all_unlisted_files_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'source';root.mkdir();out=Path(d)/'output';manifest=Path(d)/'manifest.json'
            for name in builder.PUBLIC_FILES:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode())
            (root/'sentiment/tests').mkdir();(root/'sentiment/tests/private.txt').write_text('excluded')
            (root/'credentials.txt').write_text('excluded')
            entries=builder.build(root,out,manifest)
            self.assertEqual({str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()},set(builder.PUBLIC_FILES))
            metadata=json.loads(manifest.read_text())
            self.assertEqual(metadata['files'],{n:i for n,i in entries.items() if n!='CNAME'})
            self.assertEqual(metadata['hosting_configuration'],{'CNAME':entries['CNAME']})

    def test_bad_build_does_not_delete_last_good_build(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'source';root.mkdir();out=Path(d)/'output';out.mkdir();old=out/'last-good';old.write_text('keep')
            with self.assertRaises(ValueError):builder.build(root,out)
            self.assertEqual(old.read_text(),'keep')

    def test_live_verifier_checks_every_byte(self):
        manifest={'files':{'index.html':{'bytes':3,'sha256':h.sha(b'abc')},'sentiment/data.json':{'bytes':2,'sha256':h.sha(b'{}')}}}
        def fetch(url,**kwargs):return h.Response(url,200,b'abc' if '/index.html?' in url else b'{}')
        with patch.object(live,'fetch',side_effect=fetch) as call:
            self.assertEqual(len(live.verify(manifest,timeout=1)),2);self.assertEqual(call.call_count,2)
        with patch.object(live,'fetch',return_value=h.Response('',200,b'bad')):
            with self.assertRaisesRegex(ValueError,'not verified'):live.verify(manifest,timeout=0)

    def test_manifest_rejects_traversal(self):
        with self.assertRaisesRegex(ValueError,'Unsafe'):live.validate_manifest({'files':{'../private':{'bytes':2,'sha256':h.sha(b'xx')}}})

    def test_manual_accepts_existing_optional_tags_but_not_empty_page(self):
        h.check_manual(h.Response('',200,b'<title>PED Field Manual</title><h1>Manual</h1>'+b' '*10001))
        with self.assertRaises(ValueError):h.check_manual(h.Response('',200,b'<title>PED Field Manual</title>'))

if __name__=='__main__':unittest.main()
