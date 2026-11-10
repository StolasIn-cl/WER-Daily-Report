import json
from pathlib import Path
import tempfile
import unittest

import daily_run as m


class DeliveryTests(unittest.TestCase):
    def test_recipient_merge_is_case_insensitive_without_changing_source(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'recipients.json'
            original = json.dumps({'recipients': ['RD@example.com', 'elvis_chu@cyberlink.com']})
            file.write_text(original, encoding='utf-8')
            result = m.recipients(dict(recipients_source=str(file),
                                       extra_recipients=['rd@example.com', 'ELVIS_CHU@cyberlink.com']))
            self.assertEqual(result, ['RD@example.com', 'elvis_chu@cyberlink.com'])
            self.assertEqual(file.read_text(encoding='utf-8'), original)

    def test_invalid_or_empty_recipients_fail_before_send(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'recipients.json'
            for addresses in [[], ['bad'], ['ok@example.com;hidden@example.com']]:
                file.write_text(json.dumps({'recipients': addresses}), encoding='utf-8')
                with self.assertRaises(ValueError):
                    m.recipients(dict(recipients_source=str(file)))



if __name__ == '__main__':
    unittest.main()
