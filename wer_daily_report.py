#!/usr/bin/env python3
"""Generate WER HTML reports from read-only GitHub data. Standard library + authenticated gh.

python wer_daily_report.py --live --view snapshot --pdf
python wer_daily_report.py --demo --pdf
python wer_daily_report.py --input output/live/data.json --view daily --date 2026-11-09
"""
import argparse
import concurrent.futures
import datetime as dt
import html
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit, unquote, quote

TAIPEI = dt.timezone(dt.timedelta(hours=8))
REPO = 'CyberLink-Team/promeo-pc-promeo'
ENDINGS = {
    'fixed': '已修復', 'fixed_induced': '誘發重現後修復',
    'fixed_open_risks': '已修復，仍有風險', 'engine_owned': '引擎問題',
    'changed_but_unverified': '已修改，未驗證', 'not_fixed': '未修成',
    'cannot_reproduce': '無法重現', 'possibly_already_fixed': '可能已修復',
    'undetermined': '尚無法判定', 'quota_exhausted': '等待額度',
    'blocked_external': '外部條件阻擋',
}


def number(value):
    return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0 else None)


def instant(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def on_day(value, day):
    parsed = instant(value)
    return bool(parsed and parsed.astimezone(TAIPEI).date().isoformat() == day)


def processing_time(report):
    """Timeline is across cycles. stages contains only the latest cycle: do not sum it."""
    rows = report.get('timelines') or []
    values = [number(r.get('total_seconds')) for r in rows]
    if rows and all(v is not None for v in values):
        return round(sum(values), 3), 'timeline'
    return None, 'missing'


def summarize(rows):
    costs = [number(r.get('cost_usd')) for r in rows]
    times = [number(r.get('processing_seconds')) for r in rows]
    return dict(count=len(rows), cost_usd=round(sum(x for x in costs if x is not None), 4),
                cost_missing=sum(x is None for x in costs),
                cost_estimated=sum(r.get('cost_basis') == 'estimate' for r in rows),
                seconds=sum(x for x in times if x is not None),
                time_missing=sum(x is None for x in times),
                average_seconds=sum(x for x in times if x is not None) / sum(x is not None for x in times)
                if any(x is not None for x in times) else None,
                average_cost_usd=sum(x for x in costs if x is not None) / sum(x is not None for x in costs)
                if any(x is not None for x in costs) else None)


def publish_runs(comments):
    found = {}
    for c in comments:
        marker = re.search(r'<!--\s*werfix:publish\s+bug=(\S+)\s+run=([^\s>]+)\s*-->', c.get('body', ''))
        if not marker:
            continue
        key = marker.group(2)
        candidate = dict(run_id=key, bug=marker.group(1), delivered_at=c.get('created_at'),
                         body=c['body'], comment_url=c.get('html_url'))
        if key not in found or (candidate['delivered_at'] or '') < (found[key]['delivered_at'] or ''):
            found[key] = candidate
    return sorted(found.values(), key=lambda r: r['delivered_at'] or '', reverse=True)


def latest_modified(*values):
    valid = [v for v in values if instant(v)]
    return max(valid, key=instant) if valid else None


def group_rows(rows, view, day):
    groups = {name: [] for name in ['Handled', 'Done', 'Handling', 'Init']}
    for row in rows:
        status = row.get('status')
        if status == 'Done':
            if view == 'snapshot' or on_day(row.get('modified_at'), day):
                groups[status].append(row)
        elif status in ('Handled', 'Handling', 'Init'):
            groups[status].append(row)
    for group in groups.values():
        group.sort(key=lambda r: (r.get('modified_at') or '', r.get('number', 0)), reverse=True)
    groups['Handled'].sort(key=lambda r: (instant(r.get('delivered_at')) is None,
                                         instant(r.get('delivered_at')) or dt.datetime.max.replace(tzinfo=TAIPEI),
                                         r.get('number', 0)))
    return groups


def intake_summary(rows, day):
    counts = {status: 0 for status in ('Init', 'Handling', 'Handled', 'Done')}
    total = 0
    for row in rows:
        if on_day(row.get('created_at'), day):
            total += 1
            status = row.get('status')
            counts[status] = counts.get(status, 0) + 1
    return dict(total=total, counts=counts,
                missing=sum(instant(row.get('created_at')) is None for row in rows))


def project_times():
    result = {}
    cursor = None
    while True:
        query = ('query($cursor:String){organization(login:"CyberLink-Team"){projectV2(number:40){'
                 'items(first:100,after:$cursor){nodes{id updatedAt content{... on Issue{createdAt updatedAt closedAt}}}'
                 'pageInfo{hasNextPage endCursor}}}}}')
        args = ['api', 'graphql', '-f', 'query=' + query]
        if cursor:
            args += ['-f', 'cursor=' + cursor]
        payload = gh(*args)
        if payload.get('errors'):
            raise RuntimeError('Project modification times cannot be read')
        page = payload['data']['organization']['projectV2']['items']
        result.update({node['id']: node for node in page['nodes']})
        if not page['pageInfo']['hasNextPage']:
            return result
        cursor = page['pageInfo']['endCursor']


def gh(*args):
    result = subprocess.run(['gh', *args], capture_output=True, encoding='utf-8', timeout=120)
    if result.returncode:
        raise RuntimeError('GitHub read failed: ' + ' '.join(args[:2]))
    return json.loads(result.stdout)


def report_link(body):
    for url in re.findall(r'https://github\.com/[^\s)<>]+\.json', body):
        parts = unquote(urlsplit(url).path).strip('/').split('/')
        if (len(parts) >= 6 and '/'.join(parts[:2]) == REPO and parts[2] == 'blob'
                and re.fullmatch(r'[0-9a-f]{40}', parts[3])
                and parts[-1].startswith('WER-') and parts[-1].endswith('.json')):
            return url, parts[3], '/'.join(parts[4:])
    return None


def boomerang_link(body, title):
    for url in re.findall(r'https://[^\s)<>|]+', body):
        url = html.unescape(url)
        parsed = urlsplit(url)
        if (parsed.netloc == 'clwerbackend.cyberlink.com'
                and parsed.path == '/boomerang/RawLog.html'
                and re.search(r'(?:^|&)id=\d+(?:&|$)', parsed.query)):
            return url
    prop = (re.search(r'PropID\s*[:|]\s*`?(\d+)', body, re.I)
            or re.search(r'werfix-intake:(\d+)', body)
            or re.match(r'(?:\[WER-|WER-)?(\d{6,})(?:\]|\s+-)', title))
    return ('https://clwerbackend.cyberlink.com/boomerang/RawLog.html?id=' + prop.group(1)
            if prop else None)


def version_info(body):
    """Crash version rows written into the issue body by the intake (new format), with the old single-row fallback."""
    def cell(label):
        match = re.search(r'\|\s*' + label + r'\s*\|\s*([^|\r\n]+?)\s*\|', body or '')
        return match.group(1).replace('`', '').strip() if match else None
    return dict(latest_crash_at=cell('最新一次當機'), latest_crash_version=cell('最新一次當機的版本'),
                owned_versions=cell('本票承接的版本') or cell('版本'))


def normalize_report(report, run):
    if report.get('run_id') != run['run_id'] or report.get('bug') != run['bug']:
        raise ValueError('Published report identity does not match the run marker')
    seconds, basis = processing_time(report)
    ending = report.get('outcome') or (report.get('ending') or {}).get('name')
    full = report.get('full_report') or {}
    ending_info = report.get('ending') or {}
    plain = (report.get('agent_report') or {}).get('plain') if isinstance(report.get('agent_report'), dict) else None
    plain_cause = ((plain or {}).get('root_cause') or '').strip() if isinstance(plain, dict) else ''
    origin = ((ending_info.get('root_cause') or {}).get('bad_state_origin')) or {}
    location = (f'{Path(str(origin["file"]).replace(chr(92), "/")).name}:{origin["line"]}'
                if origin.get('file') and origin.get('line') else None)
    return dict(run_id=run['run_id'], delivered_at=run['delivered_at'], ending=ending,
                root_cause=(plain_cause or origin.get('mechanism') or None), root_cause_loc=location,
                pr_state=ending_info.get('pr') if isinstance(ending_info.get('pr'), str) else None,
                processing_seconds=seconds, time_basis=basis,
                cost_usd=number(report.get('estimated_cost_usd')),
                cost_basis='estimate' if report.get('cost_includes_estimate') else 'measured',
                tokens=report.get('spent_tokens'),
                report_url=full.get('url') if full.get('status') == 'synced' else None,
                report_json_url=run.get('report_json_url'), comment_url=run.get('comment_url'))


def collect(day, cache_dir):
    snapshot = gh('project', 'item-list', '40', '--owner', 'CyberLink-Team', '--limit', '1000', '--format', 'json')
    if snapshot.get('totalCount') != len(snapshot.get('items', [])):
        raise RuntimeError('Project listing is incomplete; refusing a partial report')
    timestamps = project_times()
    captured_at = dt.datetime.now(TAIPEI).isoformat(timespec='seconds')
    cache_dir.mkdir(parents=True, exist_ok=True)

    def one(item):
        content = item.get('content') or {}
        if content.get('type') != 'Issue' or content.get('repository') != REPO:
            return None
        issue = content['number']
        row = dict(number=issue, title=content['title'], url=content['url'], status=item.get('status'),
                   boomerang_url=boomerang_link(content.get('body', ''), content['title']),
                   worker=item.get('worker') or None, assignees=item.get('assignees') or [],
                   pr_urls=item.get('linked pull requests') or [], run_history=[], warnings=[])
        timing = timestamps.get(item['id'])
        if timing is None:
            raise RuntimeError('Project changed during collection; retry the report')
        issue_timing = timing.get('content') or {}
        row.update(project_updated_at=timing.get('updatedAt'),
                   created_at=issue_timing.get('createdAt'),
                   issue_updated_at=issue_timing.get('updatedAt'),
                   closed_at=issue_timing.get('closedAt'),
                   modified_at=latest_modified(timing.get('updatedAt'), issue_timing.get('updatedAt')))
        row.update(version_info(content.get('body', '')))
        if row['status'] not in ('Handled', 'Handling', 'Init', 'Done'):
            row['warnings'].append(f'Unknown Project status: {row["status"]}')
        if row['status'] == 'Init':
            return row
        pages = gh('api', f'repos/{REPO}/issues/{issue}/comments?per_page=100', '--paginate', '--slurp')
        runs = publish_runs([c for page in pages for c in page])
        row['published_run_count'] = len(runs)
        selected = runs[:1]
        for run in selected:
            try:
                link = report_link(run['body'])
                if not link:
                    raise ValueError('No immutable report JSON link')
                url, sha, file_path = link
                run['report_json_url'] = url
                cached = cache_dir / f'{sha}-{Path(file_path).name}'
                if cached.exists():
                    payload = json.loads(cached.read_text(encoding='utf-8'))
                else:
                    response = subprocess.run(['gh', 'api', f'repos/{REPO}/contents/{quote(file_path, safe="/")}?ref={sha}',
                                               '-H', 'Accept: application/vnd.github.raw+json'], capture_output=True,
                                              encoding='utf-8', timeout=120)
                    if response.returncode:
                        raise ValueError('Published JSON cannot be read')
                    payload = json.loads(response.stdout)
                    cached.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
                metrics = normalize_report(payload, run)
            except (ValueError, KeyError) as error:
                row['warnings'].append(f'#{issue}: {error}')
                metrics = {k: run.get(k) for k in ['run_id', 'delivered_at', 'comment_url']}
            row['run_history'].append(metrics)
        if row['run_history']:
            row.update(row['run_history'][0])
        if row['status'] == 'Handling':
            # A published previous run is not progress/cost of the active run.
            for key in ('ending', 'processing_seconds', 'cost_usd', 'delivered_at', 'run_id', 'root_cause', 'root_cause_loc', 'pr_state'):
                row[key] = None
            row['progress'] = '處理中；尚無即時進度與用量資料'
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = [r for r in pool.map(one, snapshot['items']) if r]
    warnings = [w for r in rows for w in r.get('warnings', [])]
    return dict(schema_version=1, captured_at=captured_at, source='GitHub Project #40 + published report JSON',
                source_project='https://github.com/orgs/CyberLink-Team/projects/40',
                project_item_count=len(snapshot['items']), rows=rows, warnings=warnings,
                note='處理時間取跨輪 timeline 加總，不含排隊；修改日不等於 run 執行日。')


def demo_data():
    def row(n, title, status, ending=None, seconds=None, cost=None, **more):
        return dict(number=n, title=title, status=status, url=f'https://github.com/{REPO}/issues/{n}',
                    assignees=['RD 範例'], worker='PromeoPCRunner3', ending=ending,
                    created_at='2026-11-09T03:00:00Z', modified_at='2026-11-10T00:00:00Z',
                    processing_seconds=seconds, cost_usd=cost, cost_basis='measured',
                    delivered_at='2026-11-09T12:00:00Z' if status == 'Handled' else None, **more)
    return dict(schema_version=1, captured_at='2026-11-10T08:00:00+08:00', source='DEMO · 合成版型資料，非實際案件', warnings=[],
                rows=[row(90001, '[WER-DEMO-01] 關閉視窗時，程式在結束的瞬間當機', 'Handled', 'fixed', 5520, 12.4),
                      row(90002, '[WER-DEMO-02] 匯出影片後，背景工作仍使用已釋放物件', 'Handled', 'fixed_open_risks', 7380, 18.2),
                      row(90003, '[WER-DEMO-03] 匯入照片時當機，本機尚未能重現', 'Handled', 'cannot_reproduce', 3240, None),
                      row(90004, '[WER-DEMO-04] 長標題示意：連續編輯多張子母畫面照片並切換專案後，在預覽區選取照片時程式意外關閉', 'Handling', seconds=2880, cost=5.2, progress='重現階段，等待額度恢復'),
                      row(90005, '[WER-DEMO-05] 更換素材時背景縮圖程序當機', 'Init')])


def duration(value):
    value = number(value)
    if value is None:
        return '尚無資料'
    seconds = round(value)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return (f'{hours} 小時 {minutes:02d} 分' if hours else
            f'{minutes} 分 {seconds:02d} 秒' if minutes else f'{seconds} 秒')


def esc(value):
    return html.escape(str(value if value is not None else ''), quote=True)


def anchor(url, label):
    parsed = urlsplit(url or '')
    if parsed.scheme != 'https' or not parsed.netloc:
        return esc(label)
    return f'<a href="{esc(url)}">{esc(label)}</a>'


STATUS_LABELS = {'Init': '未解', 'Handling': '處理中', 'Handled': '已解完・待 RD', 'Done': '已結案'}
NEW_ORDER = ['Handled', 'Handling', 'Init', 'Done']


def done_on_day(row, day):
    """Done by issue close time; falls back to last modification when the issue was never closed."""
    if row.get('status') != 'Done':
        return False
    if instant(row.get('closed_at')):
        return on_day(row['closed_at'], day)
    return on_day(row.get('modified_at'), day)


def waiting_days(row, captured_at):
    delivered, captured = instant(row.get('delivered_at')), instant(captured_at)
    if not delivered or not captured:
        return None
    return max(0, (captured - delivered).days)


def daily_overview(rows, day):
    """Everything the top of the report needs: yesterday's intake, plus the current work queues."""
    new = [r for r in rows if on_day(r.get('created_at'), day)]
    new.sort(key=lambda r: (NEW_ORDER.index(r['status']) if r.get('status') in NEW_ORDER else len(NEW_ORDER),
                            r.get('number', 0)))
    groups = group_rows(rows, 'daily', day)
    return dict(new=new, counts={s: sum(r.get('status') == s for r in new) for s in NEW_ORDER},
                done_on_day=sum(done_on_day(r, day) for r in rows),
                init_total=len(groups['Init']), handling=groups['Handling'], handled=groups['Handled'],
                missing_created=sum(instant(r.get('created_at')) is None for r in rows))


def short_text(value, limit=160):
    value = re.sub(r'\s+', ' ', value or '').strip()
    return value if len(value) <= limit else value[:limit - 1] + '…'


def clock(value):
    stamp = instant(value)
    return stamp.astimezone(TAIPEI).strftime('%Y-%m-%d %H:%M') if stamp else '尚無資料'


def case_links(row):
    links = [anchor(row.get('url'), '#' + str(row.get('number', '')))]
    links.append(anchor(row['boomerang_url'], 'Boomerang') if row.get('boomerang_url')
                 else '<span class="missing-link">Boomerang（缺 PropID）</span>')
    for key, label in [('report_url', '完整報告'), ('report_json_url', '報告 JSON')]:
        if row.get(key):
            links.append(anchor(row[key], label))
    return ' · '.join(links)


def version_cell(row):
    version = row.get('latest_crash_version')
    owned = row.get('owned_versions')
    if not version and not owned:
        return '<span class="missing-link">尚無版本資料</span>'
    lines = [f'<b>{esc(version or owned)}</b>']
    if version:
        when = re.sub('（.*?）', '', row.get('latest_crash_at') or '').strip() or '時間未知'
        lines.append(f'<small>最新當機 {esc(when)}</small>')
    else:
        lines.append('<small>承接版本，無最新當機資料</small>')
    if version and owned and not owned.startswith(version):
        lines.append(f'<small>本票承接 {esc(owned)}</small>')
    return ''.join(lines)


def pr_cell(row):
    urls = row.get('pr_urls') or []
    if not urls:
        return '尚無 PR'
    state = {'draft': 'Draft', 'ready': 'Ready'}.get(row.get('pr_state'))
    items = [anchor(u, 'PR #' + u.rstrip('/').rsplit('/', 1)[-1]) for u in urls]
    return '<br>'.join(items) + (f'<small>{esc(state)}</small>' if state else '')


def render_table(name, title, description, labels, widths, body_rows, empty_text):
    head = (f'<tr class="print-group-title"><th colspan="{len(labels)}"><h2>{esc(title)}'
            f'<span class="count">{len(body_rows)}</span></h2><p>{esc(description)}</p></th></tr>')
    out = [f'<section class="group" id="{name}"><div class="group-heading"><div><h2>{esc(title)}'
           f'<span class="count">{len(body_rows)}</span></h2><p>{esc(description)}</p></div></div>',
           '<table class="rows"><colgroup>' + ''.join(f'<col style="width:{w}%">' for w in widths)
           + '</colgroup><thead>' + head + '<tr class="row column-head">'
           + ''.join(f'<th>{x}</th>' for x in labels) + '</tr></thead><tbody>']
    if not body_rows:
        out.append(f'<tr><td colspan="{len(labels)}" class="empty">{esc(empty_text)}</td></tr>')
    for index, cells in enumerate(body_rows, 1):
        number_badge = f'<span class="case-number" aria-label="案例 {index}">{index}</span>'
        cells = [number_badge + cells[0]] + cells[1:]
        out.append('<tr class="row case-row">' + ''.join(
            f'<td class="cell" data-label="{labels[i]}">{c}</td>' for i, c in enumerate(cells)) + '</tr>')
    out.append('</tbody></table></section>')
    return ''.join(out)


def render(data, view, day):
    ov = daily_overview(data['rows'], day)
    handled, handling = ov['handled'], ov['handling']
    demo = data['source'].startswith('DEMO')
    captured_at = data.get('captured_at')
    scope = f'統計日 {day}（台灣時間）' if view == 'daily' else f'統計日 {day}（台灣時間）· 目前看板快照'
    counts = ov['counts']
    waits = [w for w in (waiting_days(r, captured_at) for r in handled) if w is not None]

    def metric(label, value, detail):
        return f'<article class="metric"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>'

    yesterday = [metric('昨日新增 crash', len(ov['new']), '依 issue 建立時間計算'),
                 metric('其中：未解', counts['Init'], '目前仍是 Init，尚未開始處理'),
                 metric('其中：處理中', counts['Handling'], '目前 Handling'),
                 metric('其中：已解完', counts['Handled'] + counts['Done'],
                        f'待 RD 處理 {counts["Handled"]} · 已結案 {counts["Done"]}'),
                 metric('昨日新進 Done', ov['done_on_day'], '昨日結案，只列數字')]
    s = summarize(handled)
    money = (f'時間不含排隊；成本含估算 {s["cost_estimated"]} 件；' if s['cost_estimated'] else '時間不含排隊；') \
        + f'缺成本 {s["cost_missing"]} 件'
    current = [metric('目前 Init 待解', ov['init_total'], '只列數字，不列明細'),
               metric('目前 Handling', len(handling), '處理中，明細見下方'),
               metric('目前 Handled 等 RD', len(handled), '已交付，明細見下方'),
               metric('最久已等待', f'{max(waits)} 天' if waits else '尚無資料', '從 AutoFix 交付到資料擷取')]
    output = ['<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
              '<title>WER AutoFix 每日報告</title><style>' + CSS + '</style></head><body><main>',
              '<header><div class="eyebrow">WER AUTOFIX / DAILY REPORT</div><h1>WER AutoFix 每日報告</h1>',
              f'<p class="subtitle">{esc(scope)}</p><div class="metadata"><span>資料擷取 {esc(captured_at)}</span>{"<span>版型示意・非實際案件</span>" if demo else ""}</div></header>',
              '<nav><a href="#New">昨日新增</a><a href="#Handling">處理中</a><a href="#Handled">等 RD 處理</a></nav>',
              f'<section class="overview intake"><div class="section-label">昨日概況 <small>{esc(day)} · 台灣時間</small></div><div class="metrics">'
              + ''.join(yesterday) + '</div><p class="scope-note">新增數依 issue 建立時間，狀態為資料擷取當下的狀態，不是昨日當天的狀態。'
              '以 issue 數代表 crash 案件數，非 crash 發生次數。「新進 Done」以 issue 關閉時間判斷，無關閉時間才用最後修改時間。</p>']
    if ov['missing_created']:
        output.append(f'<p class="scope-note">{ov["missing_created"]} 件缺建立時間，未計入新增；請用 --live 更新資料。</p>')
    output.append('</section><section class="overview backlog"><div class="section-label">目前概況 <small>資料擷取當下的全部案件</small></div>'
                  '<div class="metrics">' + ''.join(current) + '</div><div class="outcomes">')
    outcome_counts = {}
    for row in handled:
        key = row.get('ending') or 'unknown'
        outcome_counts[key] = outcome_counts.get(key, 0) + 1
    for ending, count in sorted(outcome_counts.items(), key=lambda p: -p[1]):
        output.append(f'<span class="outcome"><i class="dot {esc(ending)}"></i>{esc(ENDINGS.get(ending, "結局缺資料"))}<b>{count}</b></span>')
    time_part = f'處理時間合計 {duration(s["seconds"])}' if s['count'] - s['time_missing'] else '處理時間合計 尚無資料'
    cost_part = f'模型成本合計 USD {s["cost_usd"]:,.2f}' if s['count'] - s['cost_missing'] else '模型成本合計 尚無資料'
    output.append(f'</div><p class="scope-note">上列為目前 Handled 的結局分布。{esc(time_part)}、{esc(cost_part)}'
                  f'（最新 run 累計，非昨日新增消耗；{esc(money)}）。</p></section>')

    # Yesterday's new crashes: compact one-line rows; Init and Done show only a title.
    mini_labels = ['Crash / Issue', '目前狀態', 'crash 版本', '結局／備註', '接手 RD']
    output.append(f'<section class="listing" id="New"><div class="group-heading"><div><h2>昨日新增 crash<span class="count">{len(ov["new"])}</span></h2>'
                  f'<p>{esc(day)} 新建立的 issue 與其目前狀態；Init 與 Done 只列標題，不展開細節。</p></div></div>'
                  '<table class="mini"><colgroup>' + ''.join(f'<col style="width:{w}%">' for w in [36, 14, 18, 18, 14])
                  + '</colgroup><thead><tr>' + ''.join(f'<th>{x}</th>' for x in mini_labels) + '</tr></thead><tbody>')
    if not ov['new']:
        output.append('<tr><td colspan="5" class="empty">昨日沒有新增 crash</td></tr>')
    for index, row in enumerate(ov['new'], 1):
        status = row.get('status')
        if status == 'Handled':
            note = ENDINGS.get(row.get('ending'), '尚無 AutoFix 結果')
        elif status == 'Handling':
            note = row.get('progress') or '處理中；即時進度尚無資料'
        elif status == 'Init':
            note = '尚未執行'
        else:
            note = '—'
        label = STATUS_LABELS.get(status, f'未知狀態（{status}）')
        cells = [f'<span class="case-number">{index}</span><strong class="crash-title">{anchor(row.get("url"), row.get("title", "未命名 crash"))}</strong>'
                 f'<div class="links">{case_links(row) if status in ("Handled", "Handling") else ""}</div>',
                 f'<span class="status {esc(status)}">{esc(label)}</span>', version_cell(row), esc(note),
                 esc('、'.join(row.get('assignees') or []) or '—')]
        output.append('<tr>' + ''.join(f'<td data-label="{mini_labels[i]}">{c}</td>' for i, c in enumerate(cells)) + '</tr>')
    output.append('</tbody></table></section>')

    handling_rows = []
    for row in handling:
        handling_rows.append([
            f'<strong class="crash-title">{anchor(row.get("url"), row.get("title", "未命名 crash"))}</strong><div class="links">{case_links(row)}</div>',
            f'<span class="ending">{esc(row.get("progress") or "處理中；即時進度尚無資料")}</span>', version_cell(row),
            esc('、'.join(row.get('assignees') or []) or '尚未交接'), esc(row.get('worker') or '尚未分派'),
            f'<time>{esc(clock(row.get("created_at")))}</time>', f'<time>{esc(clock(row.get("modified_at")))}</time>'])
    output.append(render_table('Handling', '目前處理中（Handling）', '全部目前處理中的案件，不限日期；包含等待額度或環境恢復。',
                               ['Crash / Issue', '目前進度', 'crash 版本', '接手 RD', '執行機器', '新增時間', '最後更新'],
                               [28, 17, 13, 10, 12, 10, 10], handling_rows, '目前沒有處理中的案件'))

    handled_rows = []
    for row in handled:
        line = row.get('ending') or 'unknown'
        days = waiting_days(row, captured_at)
        if days is None:
            wait = '尚無交付時間'
        else:
            wait = f'<span class="{"aging" if days >= 3 else ""}">已等 {days} 天</span><small>交付 {esc(clock(row.get("delivered_at")))}</small>'
        cause = row.get('root_cause')
        cause_cell = (f'<span title="{esc(cause)}">{esc(short_text(cause))}</span>'
                      + (f'<small>{esc(row["root_cause_loc"])}</small>' if row.get('root_cause_loc') else '')) if cause else '報告未提供根因'
        handled_rows.append([
            f'<strong class="crash-title">{anchor(row.get("url"), row.get("title", "未命名 crash"))}</strong><div class="links">{case_links(row)}</div>',
            f'<span class="ending {esc(line)}">{esc(ENDINGS.get(line, "尚無 AutoFix 結果" if line == "unknown" else f"結局待確認（{line}）"))}</span>',
            cause_cell, version_cell(row), f'<b>{esc(duration(row.get("processing_seconds")))}</b>',
            esc('、'.join(row.get('assignees') or []) or '尚未交接'), wait, pr_cell(row)])
    output.append(render_table('Handled', '目前等 RD 處理（Handled）',
                               '全部已交付、尚待 RD 處理的案件，不限日期；等最久的排最前面。',
                               ['Crash / Issue', '處理結局', '為什麼當', 'crash 版本', '處理時間', '接手 RD', '等待 RD 處理', '修復 PR'],
                               [21, 10, 20, 11, 8, 9, 11, 10], handled_rows, '目前沒有等 RD 處理的案件'))

    notes = ['昨日新增依 issue createdAt 計算，狀態為擷取當下狀態。Handling／Handled 顯示全部案件；Init 與 Done 只顯示數字。所有日期均為台灣時間。',
             '「昨日新進 Done」以 issue closedAt 判斷，無 closedAt 才用 Project／issue 最後修改時間；修改時間包含內容與欄位編輯，是近似值，不是精確的狀態轉換時間。',
             'crash 版本取自 issue 內的版本表：「最新一次當機」為 Boomerang 上該 PropID 最新一筆的時間與版本，「本票承接」為 AutoFix 承接的 package 版本；舊格式 issue 只有承接版本。', 'Handled 代表 AutoFix 已交付，修復是否成立依結局判讀；「為什麼當」取自 AutoFix 報告的白話根因（agent_report.plain.root_cause），報告沒有時退回 bad_state_origin 說明；細節請看「完整報告」。',
             data.get('note', '')]
    output.append('<footer><h3>資料口徑</h3><ul>' + ''.join(f'<li>{esc(t)}</li>' for t in notes if t) + '</ul>')
    if data.get('warnings'):
        output.append('<details open><summary>資料缺漏 ' + str(len(data['warnings'])) + ' 項</summary><ul>' + ''.join('<li>' + esc(w) + '</li>' for w in data['warnings']) + '</ul></details>')
    source_label = data['source'] if demo else 'GitHub WER Issues（Project #40）與 AutoFix 報告'
    output.append(f'<p>來源：{esc(source_label)}</p></footer></main></body></html>')
    return ''.join(output)


CSS = """
:root{color-scheme:light;--paper:#fffef9;--bg:#f3f3ed;--ink:#243b32;--muted:#6a776e;--line:#d8e0d4;--green:#2f6950}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 'Segoe UI','Microsoft JhengHei',sans-serif}main{max-width:1450px;margin:auto;padding:42px 44px 60px}header{border-bottom:1px solid var(--line);padding-bottom:24px}.eyebrow{font-size:11px;font-weight:700;letter-spacing:2.5px;color:var(--green)}h1{font-size:36px;line-height:1.3;letter-spacing:-1px;margin:14px 0 8px}.subtitle{font-size:20px;margin:0 0 14px}.metadata{display:flex;gap:12px 24px;flex-wrap:wrap;font-size:12px;color:var(--muted)}nav{display:flex;gap:24px;padding:16px 0 23px}a{color:var(--green);text-underline-offset:3px}nav a{text-decoration:none;font-weight:600;font-size:13px}a:focus-visible{outline:3px solid #b68338;outline-offset:3px}.overview{background:#e9eee2;border:1px solid #c9d5bd;border-radius:14px;padding:24px 25px}.section-label{font-size:16px;font-weight:700}.section-label small{font-size:12px;font-weight:400;margin-left:12px;color:var(--muted)}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:22px;margin-top:22px}.metric{display:flex;flex-direction:column;border-right:1px solid #ccd6c5;padding-right:15px;min-width:0}.metric:last-child{border:0}.metric>span{font-size:13px;color:#52664f}.metric strong{font-size:28px;font-weight:650;line-height:1.4;margin:5px 0 7px}.metric small{font-size:11px;color:var(--muted)}.outcomes{display:flex;gap:10px 25px;flex-wrap:wrap;border-top:1px solid #ccd6c5;margin-top:22px;padding-top:16px}.outcome{display:flex;align-items:center;gap:8px;font-size:13px}.outcome b{font-size:17px}.dot{width:8px;height:8px;border-radius:50%;background:#969e8e}.dot.fixed{background:#38755a}.dot.fixed_open_risks{background:#ba8d42}.dot.fixed_induced{background:#7b9363}.dot.engine_owned{background:#6b85a0}.scope-note{font-size:11px;color:#62705e;margin:13px 0 0}.group{margin-top:34px;scroll-margin-top:15px}.group-heading h2{font-size:24px;line-height:1.4;margin:0}.count{display:inline-block;font-size:13px;vertical-align:middle;margin-left:12px;background:#e0e8d7;padding:2px 9px;border-radius:20px}.group-heading p{color:var(--muted);font-size:12px;margin:5px 0 14px}.rows{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--paper)}.row{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,1.6fr) minmax(0,1fr) minmax(0,1.1fr) minmax(0,1.1fr) minmax(0,1fr);gap:18px;padding:18px 20px;align-items:start}.column-head{font-size:11px;letter-spacing:.2px;color:var(--muted);background:#eeeee6;padding-block:11px}.case-row+.case-row{border-top:1px solid var(--line)}.cell{min-width:0;overflow-wrap:anywhere;font-size:13px}.cell small{display:block;color:var(--muted);font-size:10px;margin-top:5px}.crash-title{font-size:14px;line-height:1.65}.crash-title a{color:var(--ink);text-decoration:none}.crash-title a:hover{text-decoration:underline}.links{font-size:11px;margin-top:7px}.ending{font-size:13px;font-weight:600}.ending.fixed{color:#2f7151}.ending.fixed_open_risks,.ending.fixed_induced{color:#876524}.ending.not_fixed{color:#9c4f46}.empty{font-size:13px;color:var(--muted);padding:25px 20px}footer{border-top:1px solid var(--line);margin-top:35px;padding-top:16px;font-size:11px;color:var(--muted)}footer h3{font-size:12px}footer ul{padding-left:18px}footer li{margin:6px 0}footer p{margin-top:20px}details{margin:15px 0}summary{cursor:pointer}
@media(max-width:800px){main{padding:25px 18px 35px}h1{font-size:28px}.subtitle{font-size:17px}.overview{padding:18px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:20px 14px}.metric:nth-child(2){border:0}.metric strong{font-size:22px}.section-label small{display:block;margin:4px 0 0}.column-head{display:none}.case-row{grid-template-columns:repeat(2,minmax(0,1fr));gap:15px 18px;padding:18px}.cell:first-child{grid-column:1/-1}.cell:before{content:attr(data-label);display:block;font-size:10px;color:var(--muted);margin-bottom:4px}.cell:first-child:before{display:none}.crash-title{font-size:15px}.cell small{font-size:11px}.group{margin-top:27px}}
@page{size:A4 landscape;margin:12mm}
@media print{html{scroll-behavior:auto}body{background:white;font-size:12px}main{max-width:none;margin:0;padding:0}nav{display:none}header{padding-bottom:14px;margin-bottom:15px}h1{font-size:27px}.subtitle{font-size:17px}.overview{padding:16px 18px}.metrics{gap:15px;margin-top:14px}.metric strong{font-size:22px}.group{margin-top:22px}.group-heading{break-after:avoid}.group-heading h2{font-size:20px}.rows{overflow:visible}.row{gap:13px;padding:12px 14px}.cell{font-size:11px}.crash-title{font-size:12px}.column-head{font-size:10px}.case-row{break-inside:avoid}.overview{break-inside:avoid}a{color:inherit;text-decoration:none}footer{margin-top:20px}.outcomes{margin-top:13px;padding-top:12px}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.rows{display:block;width:100%;border-spacing:0;text-align:left}.rows thead,.rows tbody{display:block}.column-head th{font-weight:400}.cell{display:block}footer{break-inside:avoid}
@media print{.rows{display:table;table-layout:fixed;border-collapse:separate}.rows thead{display:table-header-group}.rows tbody{display:table-row-group}.rows tr{display:table-row}.rows th,.rows td{display:table-cell;padding:10px 12px;vertical-align:top;border-bottom:1px solid var(--line)}.rows .empty{padding:16px 12px}.rows .case-row:last-child td{border-bottom:0}.group{break-inside:auto}.column-head th{background:#eeeee6}.rows .cell:before{display:none}}
@media print{body{line-height:1.45}header{padding-bottom:9px;margin-bottom:12px}h1{font-size:24px;margin:8px 0 5px}.subtitle{font-size:15px;margin-bottom:8px}.metadata{font-size:10px}.overview{padding:12px 16px}.metrics{margin-top:10px}.metric strong{font-size:21px;margin:3px 0 4px}.scope-note{font-size:9px;margin-top:9px}.group-heading{break-inside:avoid;break-after:avoid}.group-heading>*{break-after:avoid}.group-heading p{margin-bottom:9px}.rows th,.rows td{padding:8px 10px}.group{margin-top:18px}footer{font-size:9px;line-height:1.35;margin-top:14px;padding-top:8px}footer h3{font-size:10px;margin:0 0 5px}footer ul{margin:5px 0}footer li{margin:3px 0}footer p{margin:8px 0 0}}
"""

# Readability overrides shared by screen and print.
CSS += """
.backlog{margin-top:22px}.case-dates{display:flex;flex-wrap:wrap;gap:5px 12px;margin-top:12px;padding:8px 10px;background:#eaf0e5;border-left:3px solid #739568;font-size:14px;line-height:1.7}.case-dates span{display:block}.case-dates b{color:#365640}.case-dates time{font-variant-numeric:tabular-nums}.subtitle{font-weight:600}
.intake .metrics{grid-template-columns:repeat(5,minmax(0,1fr))}.intake .metric:first-child strong{color:#276447}.backlog{background:#fffefb;border-color:#d9dfd5;box-shadow:0 2px 8px #243b3205}.average{display:block;color:#365e4b;font-size:15px;font-weight:600;margin-top:3px}.average small{display:block;font-weight:400;margin-top:4px}.backlog .metric:last-child strong{font-size:24px;font-variant-numeric:tabular-nums}.metric strong{font-variant-numeric:tabular-nums}.backlog .metric>small{line-height:1.6}.backlog .metric a{font-weight:600}.intake .metric>span{line-height:1.5}
@media(max-width:800px){.intake .metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.intake .metric:first-child{grid-column:1/-1;border:0;border-bottom:1px solid #ccd6c5;padding-bottom:12px}.intake .metric:nth-child(2){border-right:1px solid #ccd6c5}.intake .metric:nth-child(3){border:0}.backlog .metric:last-child strong{font-size:21px}}
@media print{.backlog{margin-top:12px}.case-dates{font-size:11px;margin-top:6px;padding:4px 6px;gap:2px 8px}}
body{font-size:17px}.metadata,nav a,.section-label small,.metric small,.scope-note,.group-heading p,.links{font-size:14px}
.metric>span,.outcome{font-size:15px}.column-head{font-size:14px}.cell,.ending{font-size:15px}.crash-title{font-size:17px}.cell small{font-size:13px}footer{font-size:13px}footer h3{font-size:14px}.missing-link{color:var(--muted)}
@media(max-width:800px){nav{gap:18px;flex-wrap:wrap}.crash-title{font-size:17px}.cell:before,.cell small{font-size:13px}.metric strong{font-size:23px}}
@media print{.cell,.ending{font-size:12px}.crash-title{font-size:14px}.links,.cell small,.column-head,.metadata,.group-heading p,.scope-note,.metric small{font-size:11px}footer,footer h3{font-size:11px}.section-label small,.metric>span,.outcome{font-size:12px}.rows th,.rows td{padding:10px}.overview{break-inside:avoid}}
@media print{.average{font-size:12px}.average small{font-size:10px;margin-top:2px}.backlog .metric:last-child strong{font-size:18px}.backlog{box-shadow:none}.group-heading,.group-heading>*{break-after:auto}.group-heading h2{break-after:avoid}.rows,.rows tbody{break-inside:auto}.group{margin-top:14px}.group-heading p{margin-bottom:6px}.rows th,.rows td{padding:8px 10px}.metrics{gap:12px}.outcomes{gap:8px 18px}footer{break-inside:auto}.scope-note{font-size:10px}}
@media print{.rows{border-collapse:collapse;border-radius:0;border:0;overflow:visible;break-inside:auto}.rows th,.rows td{border-bottom:1px solid var(--line)}.group-heading{break-after:avoid}.group-heading>*{break-after:avoid}.group-heading p{break-after:avoid}footer{font-size:9px;line-height:1.3;margin-top:10px;padding-top:6px;break-inside:avoid}footer h3{font-size:10px}footer li{margin:2px 0}footer p{margin-top:5px}.cell,.ending{font-size:11px}.crash-title{font-size:13px}.case-dates{font-size:10px;padding:3px 5px;margin-top:5px}.rows td{padding:7px 9px}.backlog .metric strong{font-size:20px}.backlog .metric:last-child strong{font-size:17px}}
"""


CSS += """
/* Calm neutral palette; accents identify hierarchy, rather than tinting all text. */
:root{--paper:#fffefa;--bg:#f5f4f0;--ink:#25343b;--muted:#596a72;--line:#d7e0e2;--green:#326a73}
.overview{background:#edf4f4;border-color:#cddfe0}.backlog{background:#fffefa;border-color:var(--line);box-shadow:none}
.metric{border-color:#cedcde}.metric>span{color:#52656e}.intake .metric:first-child strong{color:#275e67}
.outcomes{border-color:var(--line)}.scope-note{color:var(--muted)}.average{color:#32636c}
.count{background:#e6eef0;color:#385963}.column-head,.column-head th{background:#edf1f2;color:#52646d}
.case-dates{background:#f0f4f6;border-left-color:#8caab1;color:#4c626d}.case-dates b{color:#375763}
.ending.fixed{color:#32694f}.ending.fixed_open_risks,.ending.fixed_induced{color:#7f6229}.ending.not_fixed{color:#934b46}
.row{padding:26px 24px;gap:22px}.case-row{line-height:1.8}.crash-title{line-height:1.85}.links{margin-top:12px;line-height:1.85}.cell small{margin-top:9px;line-height:1.7}.case-dates{margin-top:14px;padding:9px 12px;gap:5px 14px}
@media(max-width:800px){.case-row{padding:23px 20px;gap:20px}.case-dates{display:block}.case-dates span+span{margin-top:4px}.intake .metric:first-child,.intake .metric:nth-child(2){border-color:#cedcde}}
/* Keep the overview compact, but let case pages breathe at readable sizes. */
@media print{
 .group{margin-top:24px}.group-heading{break-after:avoid}.group-heading>*{break-after:avoid}
 .group-heading h2{font-size:22px;line-height:1.5}.group-heading p{font-size:12px;line-height:1.7;margin:7px 0 16px}
 .rows th{padding:12px 14px;font-size:11px;line-height:1.6;background:#edf1f2}
 .rows td{padding:17px 14px;line-height:1.75;border-bottom:1px solid var(--line)}
 .cell,.ending{font-size:13px;line-height:1.75}.crash-title{font-size:15px;line-height:1.8}
 .rows td:nth-child(4){font-size:12px}
 .links{font-size:12px;line-height:1.8;margin-top:11px}.cell small{font-size:11px;line-height:1.7;margin-top:9px}
 .case-dates{font-size:11px;line-height:1.8;margin-top:12px;padding:7px 9px;gap:4px 12px}
 .case-row{break-inside:avoid}.rows .empty{padding:23px 14px;font-size:13px}
 footer{font-size:11px;line-height:1.65;margin-top:20px;padding-top:12px;break-inside:avoid}
 footer h3{font-size:12px}footer li{margin:5px 0}footer p{margin-top:12px}
}
/* Individual case cards, including the printed table rows. */
.case-number{display:inline-flex;align-items:center;justify-content:center;min-width:28px;height:28px;padding:0 7px;margin:0 9px 5px 0;border:1px solid #bdcfd4;border-radius:7px;background:#e5eef0;color:#345d68;font-size:14px;font-weight:700;line-height:1;vertical-align:baseline;font-variant-numeric:tabular-nums}
.print-group-title{display:none}
.rows{border:0;border-radius:0;background:transparent;overflow:visible}
.column-head{border-radius:10px}
.case-row{border:1px solid #cfdcdf;border-radius:12px;background:#fafcfb;margin-top:14px}
.case-row+.case-row{border-top:1px solid #cfdcdf}
.rows .empty{border:1px solid #cfdcdf;border-radius:12px;background:#fafcfb}
@media print{
 .rows{border-collapse:separate;border-spacing:0 12px;border:0;background:transparent}
 .rows .case-row{border:0;background:transparent;margin:0}
 .rows .case-row td,.rows .case-row:last-child td{background:#fafcfb;border-top:1px solid #cfdcdf;border-bottom:1px solid #cfdcdf}
 .rows .case-row td:first-child{border-left:1px solid #cfdcdf;border-radius:10px 0 0 10px}
 .rows .case-row td:last-child{border-right:1px solid #cfdcdf;border-radius:0 10px 10px 0}
 .rows th{border:0}.rows th:first-child{border-radius:8px 0 0 8px}.rows th:last-child{border-radius:0 8px 8px 0}
 .rows .empty{border:1px solid #cfdcdf;border-radius:10px}
 .group{break-before:page;margin-top:0}.group>.group-heading{display:none}
 .rows .print-group-title{display:table-row;break-inside:avoid}
 .rows .print-group-title th{background:white;border:0;border-radius:0;padding:0 0 5px;text-align:left}
 .print-group-title h2{font-size:22px;line-height:1.5;margin:0;color:var(--ink);font-weight:700}
 .print-group-title p{font-size:12px;line-height:1.7;margin:7px 0 0;color:var(--muted);font-weight:400}
 .rows thead{break-inside:avoid}
 .case-number{min-width:24px;height:24px;padding:0 6px;font-size:12px;margin-right:8px}
}
"""


# Layout v2: yesterday's intake and the two RD work queues come first.
CSS += """
.status{display:inline-block;font-size:14px;font-weight:700;padding:2px 11px;border-radius:20px;white-space:nowrap}
.status.Init{background:#f4e6e3;color:#8a443d}.status.Handling{background:#f6ecd5;color:#7a5a1e}.status.Handled{background:#dcebe3;color:#2d6249}.status.Done{background:#e6eaec;color:#52646d}
.aging{color:#934b46;font-weight:700}
.listing{margin-top:34px;scroll-margin-top:15px}.listing .group-heading h2{font-size:24px;line-height:1.4;margin:0}.listing .group-heading p{color:var(--muted);margin:5px 0 14px}
.mini{width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0;background:#fafcfb;border:1px solid #cfdcdf;border-radius:12px;overflow:hidden;text-align:left}
.mini th{font-size:14px;font-weight:400;color:#52646d;background:#edf1f2;padding:11px 16px}
.mini td{padding:12px 16px;border-top:1px solid #dde7e9;font-size:15px;vertical-align:top;overflow-wrap:anywhere}
.mini small{display:block;color:var(--muted);font-size:13px;margin-top:4px;line-height:1.6}.mini .crash-title{font-size:15px;line-height:1.7}.mini .links{margin-top:4px}
.mini .case-number{min-width:26px;height:26px;margin-bottom:0}
.ending{display:block}
#Handling,#Handled{margin-top:34px}
@media(min-width:801px){
 #Handled .row{grid-template-columns:minmax(0,2.5fr) minmax(0,1.1fr) minmax(0,2.3fr) minmax(0,1.3fr) minmax(0,.9fr) minmax(0,1fr) minmax(0,1.2fr) minmax(0,1.1fr)}
 #Handling .row{grid-template-columns:minmax(0,2.8fr) minmax(0,1.7fr) minmax(0,1.3fr) minmax(0,1fr) minmax(0,1.2fr) minmax(0,1fr) minmax(0,1fr)}
}
@media(max-width:800px){
 .mini thead{display:none}.mini,.mini tbody{display:block}
 .mini tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 16px;padding:16px;border-top:1px solid #dde7e9}
 .mini tr:first-child{border-top:0}.mini td{display:block;padding:0;border:0}.mini td:first-child{grid-column:1/-1}
 .mini td:before{content:attr(data-label);display:block;font-size:12px;color:var(--muted);margin-bottom:2px}.mini td:first-child:before{display:none}
 .intake .metrics{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media print{
 .listing{break-before:page;margin-top:0}.listing .group-heading{break-after:avoid}.listing .group-heading h2{font-size:22px}.listing .group-heading p{font-size:12px}
 .mini{break-inside:auto;border-radius:0;border:0;background:transparent}.mini thead{display:table-header-group}
 .mini tr{break-inside:avoid}.mini th{font-size:11px;padding:8px 10px}.mini td{font-size:12px;padding:8px 10px}.mini .crash-title{font-size:12px}
 .mini .links{font-size:10px}.status{font-size:11px;padding:1px 8px}
 .group,#Handling,#Handled{break-before:page;margin-top:0}
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--live', action='store_true')
    source.add_argument('--demo', action='store_true')
    source.add_argument('--input', type=Path)
    parser.add_argument('--view', choices=['snapshot', 'daily'], default='snapshot')
    parser.add_argument('--date', default=(dt.datetime.now(TAIPEI).date() - dt.timedelta(days=1)).isoformat())
    parser.add_argument('--out', type=Path)
    parser.add_argument('--pdf', action='store_true')
    args = parser.parse_args()
    dt.date.fromisoformat(args.date)
    out = args.out or Path(__file__).resolve().parent / 'output' / ('demo' if args.demo else 'live')
    out.mkdir(parents=True, exist_ok=True)
    if args.demo:
        data = demo_data()
    elif args.live:
        data = collect(args.date, Path(__file__).resolve().parent / 'cache')
    else:
        data = json.loads(args.input.read_text(encoding='utf-8-sig'))
    output = out / 'report.html'
    (out / 'data.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    output.write_text(render(data, args.view, args.date), encoding='utf-8')
    if args.pdf:
        subprocess.run(['node', str(Path(__file__).with_name('render_report.cjs')), str(output.resolve())], check=True)
    counts = {name: len(rows) for name, rows in group_rows(data['rows'], args.view, args.date).items()}
    print(json.dumps(dict(html=str(output.resolve()), groups=counts, warnings=len(data.get('warnings', []))), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
