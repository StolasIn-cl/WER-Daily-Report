"""Prepare a WER report manifest and synchronize shared delivery receipts. Never sends mail itself."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from wer_daily_report import TAIPEI, daily_overview

SHARED = Path(__file__).resolve().parent.parent / 'Report-Delivery'
sys.path.insert(0, str(SHARED))
from delivery import Store, attachment, key, validate

ROOT = Path(__file__).resolve().parent


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def recipients(config):
    source = read_json(Path(config['recipients_source']))['recipients']
    if not isinstance(source, list) or not source:
        raise ValueError('Nightly recipient list is empty or invalid')
    extra = config.get('extra_recipients', [])
    if not isinstance(extra, list):
        raise ValueError('Extra recipients must be a list')
    result, seen = [], set()
    for address in source + extra:
        if not isinstance(address, str) or not re.fullmatch(r'[^\s@;,]+@[^\s@;,]+\.[^\s@;,]+', address.strip()):
            raise ValueError('Recipient address is invalid')
        address = address.strip()
        if address.lower() not in seen:
            result.append(address)
            seen.add(address.lower())
    return result


def now():
    return dt.datetime.now(TAIPEI).isoformat(timespec='seconds')


def write_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def delivery_manifest(state, sender):
    return validate(dict(schema_version=1, report_id='wer-daily-report', report_date=state['date'],
        generation=state['generation'], generated_at=state['prepared_at'], sender_account=sender,
        recipients=state['recipients'], subject=state['subject'], body=state['body'],
        attachments=[attachment(state['pdf_path'])]))


def apply_receipt(state, receipt):
    if receipt.get('status') != 'sent' or not receipt.get('email_sent'):
        raise ValueError('Shared delivery has not been confirmed sent')
    if receipt.get('legacy'):
        if receipt.get('report_id') != 'wer-daily-report' or receipt.get('report_date') != state['date']:
            raise ValueError('Legacy receipt mismatch')
    else:
        m = receipt['manifest']
        if m['report_id'] != 'wer-daily-report' or m['report_date'] != state['date']:
            raise ValueError('Receipt report/date mismatch')
        if m['generation'] != state['generation'] or m['attachments'][0]['sha256'] != state['pdf_sha256']:
            raise ValueError('Receipt generation/attachment mismatch')
    return dict(state, status='sent', email_sent=True, delivery_id=receipt['id'],
                delivery_evidence=receipt.get('evidence'), sent_at=receipt.get('sent_at'), updated_at=now())


def prepare(day, state_path):
    old = read_json(state_path) if state_path.exists() else {}
    sender = read_json(SHARED / 'config.local.json')['sender_account']
    existing = next((j for j in Store().list() if j['id'] == key('wer-daily-report', day, sender)), None)
    if existing:
        return dict(action='existing_delivery', **existing)
    if old.get('status') == 'sent':
        return dict(action='skip_already_sent', date=day, state_path=str(state_path))
    if old.get('status') in ('sending', 'send_unknown', 'preparing'):
        raise ValueError('Run is in progress or has an uncertain outcome; inspect state and Outlook before retrying')
    config = read_json(ROOT / 'mail-config.local.json')
    addresses = recipients(config)
    out = ROOT / 'output' / day / uuid.uuid4().hex
    state = dict(date=day, status='preparing', email_sent=False, started_at=now(),
                 generation=uuid.uuid4().hex)
    write_json(state_path, state)
    try:
        subprocess.run([sys.executable, str(ROOT / 'wer_daily_report.py'), '--live', '--view', 'daily',
                        '--date', day, '--out', str(out), '--pdf'], check=True, cwd=ROOT)
        data = read_json(out / 'data.json')
        pdf = out / f'WER-Daily-Report-{day}.pdf'
        raw_pdf = (out / 'report.pdf').read_bytes()
        if len(raw_pdf) < 1000 or not raw_pdf.startswith(b'%PDF-'):
            raise ValueError('Generated PDF is missing or invalid')
        pdf.write_bytes(raw_pdf)
        ov = daily_overview(data['rows'], day)
        counts = dict(new=len(ov['new']), new_by_status=ov['counts'], done_on_day=ov['done_on_day'],
                      init=ov['init_total'], handling=len(ov['handling']), handled=len(ov['handled']))
        c = ov['counts']
        body = (f'WER AutoFix 每日報告 — {day}（台灣時間）\n\n'
                f'昨日新增 crash：{counts["new"]} 件（未解 {c["Init"]}、處理中 {c["Handling"]}、'
                f'已解完待 RD {c["Handled"]}、已結案 {c["Done"]}）\n'
                f'昨日新進 Done：{counts["done_on_day"]} 件\n'
                f'目前 Init 待解：{counts["init"]} 件\n'
                f'目前 Handling 處理中：{counts["handling"]} 件\n'
                f'目前 Handled 等 RD 處理：{counts["handled"]} 件'
                f'\n\n資料擷取：{data["captured_at"]}\n各案件明細、根因與 PR 連結請見 PDF 附件。')
        state.update(status='ready', prepared_at=now(), captured_at=data['captured_at'],
                     recipients=addresses, subject=f'{config["subject_prefix"]} — {day}', body=body,
                     pdf_path=str(pdf), pdf_sha256=hashlib.sha256(raw_pdf).hexdigest(),
                     html_path=str(out / 'report.html'), groups=counts, new_issues=ov['counts'],
                     warnings=data.get('warnings', []))
        manifest_path = out / 'delivery-manifest.json'
        write_json(manifest_path, delivery_manifest(state, sender))
        state['manifest_path'] = str(manifest_path)
        write_json(state_path, state)
        return dict(action='ready', manifest=str(manifest_path), **state)
    except Exception as error:
        state.update(status='failed_before_send', failure=type(error).__name__, updated_at=now())
        write_json(state_path, state)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'submit', 'sync'])
    parser.add_argument('--date', default=(dt.datetime.now(TAIPEI).date() - dt.timedelta(days=1)).isoformat())
    args = parser.parse_args()
    dt.date.fromisoformat(args.date)
    folder = ROOT / 'state'
    folder.mkdir(exist_ok=True)
    state_path = folder / f'{args.date}.json'
    lock = folder / f'{args.date}.lock'
    # Serializes preparation/state mutations; sending status protects the UI interval.
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(descriptor)
    try:
        if args.action == 'prepare':
            result = prepare(args.date, state_path)
        else:
            state = read_json(state_path)
            sender = read_json(SHARED / 'config.local.json')['sender_account']
            store = Store()
            if args.action == 'submit':
                result = store.submit(read_json(state['manifest_path']))
            else:
                result = apply_receipt(state, store.get(key('wer-daily-report', args.date, sender)))
                write_json(state_path, result)
        # Keep CLI JSON portable across Windows consoles; files remain UTF-8.
        print(json.dumps(result, ensure_ascii=True))
    finally:
        lock.unlink()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
