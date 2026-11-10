import unittest
from pathlib import Path
import tempfile
import daily_run as m
import json
from unittest.mock import patch


class ManifestTests(unittest.TestCase):
    def test_wer_manifest_preserves_copy_and_attachment(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / 'WER-Daily-Report-2026-11-09.pdf'
            pdf.write_bytes(b'%PDF-1.7\n'+b'x'*1200)
            manifest = m.delivery_manifest(dict(date='2026-11-09', generation='g', prepared_at=m.now(),
                recipients=['rd@example.com'], subject='WER Daily Report — 2026-11-09', body='WER original body',
                pdf_path=str(pdf)), 'stolas_in@cyberlink.com')
            self.assertEqual(manifest['subject'], 'WER Daily Report — 2026-11-09')
            self.assertEqual(manifest['body'], 'WER original body')
            self.assertEqual(manifest['recipients'], ['rd@example.com'])
            self.assertEqual(manifest['report_id'], 'wer-daily-report')
            self.assertEqual(manifest['attachments'][0]['filename'], pdf.name)

    def test_sync_rejects_non_sent_receipt(self):
        with self.assertRaises(ValueError): m.apply_receipt({'date':'2026-11-09'}, {'status':'send_unknown'})

    def test_prepare_publishes_manifest_without_setting_delivery_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'state').mkdir()
            shared = root/'shared'; shared.mkdir()
            (shared/'config.local.json').write_text(json.dumps({'sender_account':'Stolas_In@cyberlink.com'}))
            source = root/'recipients.json'
            source.write_text(json.dumps({'recipients':['rd@example.com']}))
            (root/'mail-config.local.json').write_text(json.dumps(dict(recipients_source=str(source),
                extra_recipients=['elvis_chu@cyberlink.com'],subject_prefix='WER Daily Report')))
            class EmptyStore:
                def list(self): return []
            def render(args, **kwargs):
                out = Path(args[args.index('--out')+1]); out.mkdir(parents=True)
                (out/'data.json').write_text(json.dumps(dict(rows=[],captured_at=m.now())))
                (out/'report.pdf').write_bytes(b'%PDF-1.7\n'+b'x'*1200)
            with patch.object(m,'ROOT',root), patch.object(m,'SHARED',shared), patch.object(m,'Store',EmptyStore), patch.object(m.subprocess,'run',side_effect=render):
                result = m.prepare('2026-11-09', root/'state/2026-11-09.json')
            self.assertEqual(result['action'],'ready')
            self.assertFalse(result['email_sent'])
            manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
            self.assertEqual(manifest['subject'],'WER Daily Report — 2026-11-09')
            self.assertEqual(manifest['recipients'],['rd@example.com','elvis_chu@cyberlink.com'])
            self.assertEqual(len(manifest['attachments']),1)


if __name__ == '__main__': unittest.main()
