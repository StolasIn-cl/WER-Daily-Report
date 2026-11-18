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

    def test_empty_day_still_has_all_sections(self):
        m = self.module()
        rendered = m.render({'rows': [], 'captured_at': '2026-11-10T08:00:00+08:00',
                             'source': 'test', 'warnings': []}, 'daily', '2026-11-09')
        for name in ['New', 'Handling', 'Handled']:
            self.assertIn('id="' + name + '"', rendered)
        for name in ['Done', 'Init']:
            self.assertNotIn('id="' + name + '"', rendered)

    def test_pdf_tables_have_repeating_semantic_headers(self):
        m = self.module()
        rendered = m.render(m.demo_data(), 'snapshot', '2026-11-09')
        self.assertEqual(rendered.count('<thead>'), 3)
        self.assertEqual(rendered.count('<tbody>'), 3)
        self.assertEqual(rendered.count('<tr class="print-group-title">'), 2)

    def rows_for_overview(self):
        return [dict(number=1, status='Init', created_at='2026-11-09T01:00:00Z'),
                dict(number=2, status='Handling', created_at='2026-11-09T02:00:00Z'),
                dict(number=3, status='Handled', created_at='2026-11-09T03:00:00Z', ending='fixed',
                     delivered_at='2026-11-08T00:00:00Z'),
                dict(number=4, status='Done', created_at='2026-11-09T04:00:00Z',
                     closed_at='2026-11-09T05:00:00Z', modified_at='2026-11-09T05:00:00Z'),
                dict(number=5, status='Done', created_at='2026-10-01T00:00:00Z',
                     closed_at='2026-11-09T06:00:00Z'),
                dict(number=6, status='Done', created_at='2026-10-01T00:00:00Z',
                     closed_at='2026-10-02T00:00:00Z', modified_at='2026-11-09T06:00:00Z'),
                dict(number=7, status='Done', created_at='2026-10-01T00:00:00Z', modified_at='2026-11-09T07:00:00Z'),
                dict(number=8, status='Init', created_at='2026-10-01T00:00:00Z'),
                dict(number=9, status='Handled', created_at='2026-10-01T00:00:00Z',
                     delivered_at='2026-10-20T00:00:00Z')]

    def test_overview_counts_yesterday_intake_and_current_queues(self):
        m = self.module()
        ov = m.daily_overview(self.rows_for_overview(), '2026-11-09')
        self.assertEqual([r['number'] for r in ov['new']], [3, 2, 1, 4])
        self.assertEqual(ov['counts'], dict(Handled=1, Handling=1, Init=1, Done=1))
        self.assertEqual(ov['init_total'], 2)
        self.assertEqual([r['number'] for r in ov['handling']], [2])
        self.assertEqual([r['number'] for r in ov['handled']], [9, 3])

    def test_done_on_day_uses_close_time_before_modification_time(self):
        m = self.module()
        ov = m.daily_overview(self.rows_for_overview(), '2026-11-09')
        # 4 and 5 closed that day; 6 only edited; 7 has no close time so modification time is used.
        self.assertEqual(ov['done_on_day'], 3)

    def test_waiting_days_is_counted_to_capture_time(self):
        m = self.module()
        row = dict(delivered_at='2026-11-01T00:00:00Z')
        self.assertEqual(m.waiting_days(row, '2026-11-04T09:00:00+08:00'), 3)
        self.assertIsNone(m.waiting_days({}, '2026-11-04T09:00:00+08:00'))
        self.assertEqual(m.waiting_days(row, '2026-10-01T00:00:00Z'), 0)

    def test_report_front_shows_numbers_only_for_init_and_done_details(self):
        m = self.module()
        data = dict(captured_at='2026-11-10T08:00:00+08:00', source='test', warnings=[],
                    rows=self.rows_for_overview() + [dict(number=10, status='Init', title='ONLY-IN-COUNT-INIT',
                                                          created_at='2026-10-01T00:00:00Z'),
                                                     dict(number=11, status='Done', title='ONLY-IN-COUNT-DONE',
                                                          created_at='2026-10-01T00:00:00Z',
                                                          closed_at='2026-11-09T08:00:00Z')])
        rendered = m.render(data, 'daily', '2026-11-09')
        for label in ['昨日新增 crash', '昨日新進 Done', '目前 Init 待解', '目前 Handling', '目前 Handled 等 RD']:
            self.assertIn(label, rendered)
        self.assertNotIn('ONLY-IN-COUNT-INIT', rendered)
        self.assertNotIn('ONLY-IN-COUNT-DONE', rendered)
        self.assertLess(rendered.index('id="New"'), rendered.index('id="Handling"'))
        self.assertLess(rendered.index('id="Handling"'), rendered.index('id="Handled"'))

    def test_handled_row_shows_root_cause_pr_and_waiting_so_rd_need_not_open_boomerang(self):
        m = self.module()
        data = dict(captured_at='2026-11-10T08:00:00+08:00', source='test', warnings=[], rows=[
            dict(number=1, title='T', url='https://github.com/x/y/issues/1', status='Handled', ending='fixed',
                 created_at='2026-11-09T01:00:00Z', delivered_at='2026-11-05T00:00:00Z', assignees=['rd1'],
                 root_cause='savePiPToBuffer 失敗時回傳空字串', root_cause_loc='adaptor.dart:192', processing_seconds=5520,
                 pr_urls=['https://github.com/x/y/pull/6340'], pr_state='draft')])
        rendered = m.render(data, 'daily', '2026-11-09')
        for text in ['savePiPToBuffer 失敗時回傳空字串', 'adaptor.dart:192', 'PR #6340', 'Draft', '已等 5 天', 'rd1', '處理時間', '1 小時 32 分']:
            self.assertIn(text, rendered)

    def test_normalized_report_keeps_one_line_root_cause_and_pr_state(self):
        m = self.module()
        run = dict(run_id='r', bug='WER-1', delivered_at='2026-11-09T00:00:00Z')
        report = dict(run_id='r', bug='WER-1', outcome='fixed', timelines=[{'total_seconds': 1}],
                      ending=dict(name='fixed', pr='ready', root_cause=dict(
                          bad_state_origin=dict(file=r'lib\a\b.dart', line=7, mechanism='boom'))))
        row = m.normalize_report(report, run)
        self.assertEqual((row['root_cause'], row['root_cause_loc'], row['pr_state']), ('boom', 'b.dart:7', 'ready'))
        report['agent_report'] = dict(plain=dict(root_cause='傳給 SIMBA 的 script 大小比實際 buffer 長'))
        plain = m.normalize_report(report, run)
        self.assertEqual((plain['root_cause'], plain['root_cause_loc']), ('傳給 SIMBA 的 script 大小比實際 buffer 長', 'b.dart:7'))
        bare = m.normalize_report(dict(run_id='r', bug='WER-1', outcome='fixed'), run)
        self.assertIsNone(bare['root_cause'])
        self.assertIsNone(bare['pr_state'])


    def test_version_table_in_issue_body_is_parsed_for_latest_and_owned_versions(self):
        m = self.module()
        body = ('| 版本 | |\r\n|---|---|\r\n| 最新一次當機 | 2026-11-15 05:16:02（Boomerang 時間） |\r\n'
                '| 最新一次當機的版本 | `8.22.5626.0` |\r\n| 本票承接的版本 | `8.21.5508.0`（2 次） |\r\n')
        self.assertEqual(m.version_info(body), dict(latest_crash_at='2026-11-15 05:16:02（Boomerang 時間）',
                         latest_crash_version='8.22.5626.0', owned_versions='8.21.5508.0（2 次）'))
        old = m.version_info('| 版本 | 8.21.5508.0 |\n| 錯誤 | `0xc0000005` |')
        self.assertEqual((old['latest_crash_version'], old['owned_versions']), (None, '8.21.5508.0'))
        empty = m.version_info('PropID: 1')
        self.assertEqual(set(empty.values()), {None})

    def test_version_is_shown_in_every_list_and_missing_is_explicit(self):
        m = self.module()
        base = dict(title='T', url='https://github.com/x/y/issues/1', created_at='2026-11-09T01:00:00Z',
                    delivered_at='2026-11-08T00:00:00Z')
        rows = [dict(base, number=1, status='Handled', ending='fixed', latest_crash_version='8.22.5626.0',
                     latest_crash_at='2026-11-15 05:16:02', owned_versions='8.22.5626.0（1 次）'),
                dict(base, number=2, status='Handling', owned_versions='8.21.5508.0'),
                dict(base, number=3, status='Init')]
        rendered = m.render(dict(captured_at='2026-11-10T08:00:00+08:00', source='test', warnings=[], rows=rows),
                            'daily', '2026-11-09')
        self.assertGreaterEqual(rendered.count('8.22.5626.0'), 2)
        self.assertIn('最新當機 2026-11-15 05:16:02', rendered)
        self.assertIn('承接版本，無最新當機資料', rendered)
        self.assertIn('尚無版本資料', rendered)


if __name__ == '__main__':
    unittest.main()
