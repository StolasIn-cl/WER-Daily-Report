import importlib.util
import unittest
from pathlib import Path


class ReportTests(unittest.TestCase):
    def module(self):
        script = Path(__file__).with_name('wer_daily_report.py')
        self.assertTrue(script.exists(), 'The standalone report script is not implemented yet')
        spec = importlib.util.spec_from_file_location('wer_daily_report', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_processing_time_includes_all_cycles_in_timeline(self):
        m = self.module()
        report = {'timelines': [{'total_seconds': 3600}, {'total_seconds': 120}],
                  'stages': [{'elapsed_seconds': 10}]}
        self.assertEqual(m.processing_time(report), (3720, 'timeline'))

    def test_missing_timeline_does_not_use_latest_cycle_as_whole_run(self):
        m = self.module()
        self.assertEqual(m.processing_time({'stages': [{'elapsed_seconds': 5}]}), (None, 'missing'))

    def test_unknown_cost_is_not_zero(self):
        m = self.module()
        rows = [{'cost_usd': None, 'processing_seconds': None},
                {'cost_usd': 3.5, 'cost_basis': 'measured', 'processing_seconds': 60}]
        s = m.summarize(rows)
        self.assertEqual(s['cost_usd'], 3.5)
        self.assertEqual(s['cost_missing'], 1)
        self.assertEqual(s['time_missing'], 1)

    def test_report_keeps_estimate_visible(self):
        m = self.module()
        s = m.summarize([{'cost_usd': 2, 'cost_basis': 'estimate', 'processing_seconds': 2}])
        self.assertEqual(s['cost_estimated'], 1)

    def test_averages_exclude_missing_values_but_include_zero(self):
        m = self.module()
        s = m.summarize([dict(cost_usd=0, processing_seconds=0),
                         dict(cost_usd=6, processing_seconds=120),
                         dict(cost_usd=None, processing_seconds=None)])
        self.assertEqual(s['average_seconds'], 60)
        self.assertEqual(s['average_cost_usd'], 3)
        self.assertIsNone(m.summarize([])['average_seconds'])
        self.assertIsNone(m.summarize([])['average_cost_usd'])

    def test_oldest_delivery_is_first_even_when_recently_edited(self):
        m = self.module()
        rows = [dict(number=1, status='Handled', delivered_at='2026-11-09T00:00:00Z'),
                dict(number=2, status='Handled', delivered_at='2026-11-01T00:00:00Z', modified_at='2026-11-10T00:00:00Z'),
                dict(number=3, status='Handled')]
        self.assertEqual([r['number'] for r in m.group_rows(rows, 'daily', '2026-11-10')['Handled']], [2, 1, 3])

    def test_daily_view_keeps_yesterday_deliveries_that_are_done_now(self):
        m = self.module()
        rows = [dict(number=1, status='Done', modified_at='2026-11-09T15:59:59Z'),
                dict(number=2, status='Handled', modified_at='2026-11-09T16:00:00Z'),
                dict(number=3, status='Handling'), dict(number=4, status='Init')]
        groups = m.group_rows(rows, 'daily', '2026-11-09')
        self.assertEqual([r['number'] for r in groups['Done']], [1])
        self.assertEqual([r['number'] for r in groups['Handled']], [2])
        self.assertEqual(len(groups['Handling']), 1)
        self.assertEqual(len(groups['Init']), 1)

    def test_snapshot_view_does_not_mix_done_into_handled(self):
        m = self.module()
        groups = m.group_rows([{'status': 'Done'}, {'status': 'Handled'}], 'snapshot', '2026-11-09')
        self.assertEqual(len(groups['Handled']), 1)

    def test_done_is_separate_and_never_duplicated(self):
        m = self.module()
        rows = [dict(number=1, status='Done', modified_at='2026-11-09T15:59:59Z', closed_at='2026-10-01T00:00:00Z'),
                dict(number=2, status='Done', modified_at='2026-11-09T16:00:00Z'),
                dict(number=3, status='Done', modified_at='2026-11-08T15:59:59Z'),
                dict(number=4, status='Done', delivered_at='2026-11-09T10:00:00Z')]
        for view in ['snapshot', 'daily']:
            groups = m.group_rows(rows, view, '2026-11-09')
            self.assertEqual(list(groups), ['Handled', 'Done', 'Handling', 'Init'])
            self.assertEqual(len(groups['Done']), 4 if view == 'snapshot' else 1)
            self.assertEqual(groups['Handled'], [])

    def test_modified_handled_uses_latest_run_even_if_published_earlier(self):
        m = self.module()
        row = dict(number=1, status='Handled', modified_at='2026-11-09T01:00:00Z',
                   delivered_at='2026-11-01T00:00:00Z', cost_usd=12, processing_seconds=3600)
        self.assertEqual(m.group_rows([row], 'daily', '2026-11-09')['Handled'], [row])
        self.assertEqual(m.group_rows([row], 'daily', '2026-01-01')['Handled'], [row])
        self.assertEqual(m.latest_modified('2026-11-09T01:00:00Z', '2026-11-10T01:00:00Z'),
                         '2026-11-10T01:00:00Z')

    def test_new_crashes_use_issue_creation_day_not_modified_day(self):
        m = self.module()
        rows = [dict(status='Init', created_at='2026-11-08T16:00:00Z'),
                dict(status='Handling', created_at='2026-11-09T15:59:59Z'),
                dict(status='Handled', created_at='2026-11-09T05:00:00Z'),
                dict(status='Done', created_at='2026-11-09T06:00:00Z'),
                dict(status='Init', created_at='2026-11-09T16:00:00Z'),
                dict(status='Handled', created_at='2026-10-01T00:00:00Z', modified_at='2026-11-09T00:00:00Z')]
        s = m.intake_summary(rows, '2026-11-09')
        self.assertEqual(s['total'], 4)
        self.assertEqual([s['counts'][x] for x in ['Init', 'Handling', 'Handled', 'Done']], [1, 1, 1, 1])
        self.assertEqual(s['missing'], 0)
        self.assertEqual(m.intake_summary([dict(status='Init')], '2026-11-09')['missing'], 1)

    def test_boomerang_preserves_source_range_and_only_uses_known_propid(self):
        m = self.module()
        url = 'https://clwerbackend.cyberlink.com/boomerang/RawLog.html?id=4855001&startDate=123&endDate=456'
        self.assertEqual(m.boomerang_link('[RawLog](' + url + ')', ''), url)
        self.assertEqual(m.boomerang_link('PropID: 4855001', ''),
                         'https://clwerbackend.cyberlink.com/boomerang/RawLog.html?id=4855001')
        self.assertIsNone(m.boomerang_link('', '[WER] occurrence 12163271'))
        self.assertIsNone(m.boomerang_link('https://evil.example/boomerang/RawLog.html?id=1', ''))

    def test_latest_publish_is_selected_by_run_without_duplicate_rows(self):
        m = self.module()
        comments = [dict(created_at='2026-11-08T00:00:00Z', body='<!-- werfix:publish bug=WER-1 run=old -->'),
                    dict(created_at='2026-11-09T00:00:00Z', body='<!-- werfix:publish bug=WER-1 run=new -->'),
                    dict(created_at='2026-11-10T00:00:00Z', body='ordinary comment')]
        selected = m.publish_runs(comments)
        self.assertEqual([x['run_id'] for x in selected], ['new', 'old'])

    def test_changed_old_comment_does_not_change_delivery_date(self):
        m = self.module()
        c = dict(created_at='2026-11-08T00:00:00Z', updated_at='2026-11-10T00:00:00Z',
                 body='<!-- werfix:publish bug=WER-1 run=a -->')
        self.assertEqual(m.publish_runs([c])[0]['delivered_at'], c['created_at'])

    def test_unsafe_link_and_title_are_not_executed(self):
        m = self.module()
        data = m.demo_data()
        data['rows'][0].update(title='<script>alert(1)</script>', url='javascript:alert(1)')
        rendered = m.render(data, 'snapshot', '2026-11-09')
        self.assertNotIn('<script>alert(1)</script>', rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertIn('&lt;script&gt;', rendered)

    def test_init_is_unstarted_and_handling_does_not_claim_final_result(self):
        m = self.module()
        rendered = m.render(m.demo_data(), 'snapshot', '2026-11-09')
        self.assertIn('尚未執行', rendered)
        self.assertIn('等待額度', rendered)
        self.assertNotIn('L3', rendered)
        self.assertNotIn('做對了', rendered)
        self.assertIn('處理時間', rendered)

    def test_empty_day_still_has_three_sections(self):
        m = self.module()
        rendered = m.render({'rows': [], 'captured_at': '2026-11-10T08:00:00+08:00',
                             'source': 'test', 'warnings': []}, 'daily', '2026-11-09')
        for name in ['Handled', 'Done', 'Handling', 'Init']:
            self.assertIn('id="' + name + '"', rendered)

    def test_pdf_tables_have_repeating_semantic_headers(self):
        m = self.module()
        rendered = m.render(m.demo_data(), 'snapshot', '2026-11-09')
        self.assertEqual(rendered.count('<thead>'), 4)
        self.assertEqual(rendered.count('<tbody>'), 4)
        self.assertEqual(rendered.count('<tr class="print-group-title">'), 4)


if __name__ == '__main__':
    unittest.main()
