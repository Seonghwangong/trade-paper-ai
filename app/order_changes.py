"""Reviewed order amendments. Blank source values never erase saved data."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape
import json
import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import invoice, packing
from app.document_import import _account, MAX_ITEMS, number
from app.packing_workflow import fingerprint, comparable
from app.storage import locked_json_mutation, path_lock
from app.ui import page_shell

router = APIRouter()
FIELDS = ('seller', 'seller_address', 'buyer', 'buyer_address', 'buyer_email',
          'currency', 'trade_term', 'payment_term', 'source_reference')
ITEM_FIELDS = ('name', 'quantity', 'unit', 'unit_price', 'hs_code', 'origin')
HISTORY = 'order_change_history'


def owned(number_, account):
    record = invoice._owned_invoice(number_, account)
    if record.get('archived_at'):
        raise HTTPException(404, 'Invoice not found')
    return record


def snapshot(record):
    return deepcopy({k: v for k, v in record.items() if k != HISTORY})


def retain_previous(updated, current, reason):
    history = deepcopy(current.get(HISTORY, []))
    if not isinstance(history, list) or len(history) >= 100:
        raise HTTPException(422, 'revision_history_full')
    history.append({'at': datetime.now(timezone.utc).isoformat(), 'reason': reason,
                    'document': snapshot(current)})
    updated[HISTORY] = history


def text(value, limit=1000):
    if value is None:
        return ''
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise HTTPException(422, 'invalid_changes')
    result = str(value).strip()
    if len(result) > limit:
        raise HTTPException(422, 'invalid_changes')
    return result


def clean_candidate(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('fields'), dict) or not isinstance(payload.get('items'), list):
        raise HTTPException(422, 'invalid_changes')
    if len(payload['items']) > MAX_ITEMS:
        raise HTTPException(422, 'invalid_changes')
    fields = {key: text(payload['fields'].get(key)) for key in FIELDS}
    if fields['currency'] and not re.fullmatch('[A-Z]{3}', fields['currency']):
        raise HTTPException(422, 'invalid_currency')
    items = []
    for source in payload['items']:
        if not isinstance(source, dict):
            raise HTTPException(422, 'invalid_changes')
        row = {key: text(source.get(key), 2003 if key == 'name' else 1000) for key in ITEM_FIELDS}
        if not row['name']:
            raise HTTPException(422, 'item_name_required')
        for key in ('quantity', 'unit_price'):
            if row[key]:
                row[key] = number(row[key])
                if not row[key]:
                    raise HTTPException(422, 'invalid_number')
        items.append(row)
    return {'fields': fields, 'items': items}


def identity(row):
    return ' '.join(text(row.get('name'), 2003).split()).casefold()


def equal(a, b, key):
    if key in ('quantity', 'unit_price'):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except InvalidOperation:
            return False
    return str(a if a is not None else '').strip() == str(b).strip()


def compare(current, candidate, mapping=None):
    old, new = current.get('items', []), candidate['items']
    if not isinstance(old, list) or not 0 < len(old) <= MAX_ITEMS or any(not isinstance(i, dict) for i in old):
        raise HTTPException(422, 'invalid_saved_items')
    mapping = {} if mapping is None else mapping
    if not isinstance(mapping, dict) or any(k not in {str(i) for i in range(len(new))} for k in mapping):
        raise HTTPException(422, 'invalid_mapping')
    oc, nc = Counter(map(identity, old)), Counter(map(identity, new))
    used, changes, unresolved = set(), [], []
    def change(id_, key, before, after, **extra):
        changes.append(dict(id=id_, field=key, before=before, after=after, **extra))
    for key, value in candidate['fields'].items():
        if value and not equal(current.get(key, ''), value, key):
            change('field:' + key, key, current.get(key, ''), value, kind='field')
    for index, row in enumerate(new):
        choice = mapping.get(str(index))
        if choice is None and oc[identity(row)] == nc[identity(row)] == 1:
            choice = next(i for i, item in enumerate(old) if identity(item) == identity(row))
        if choice == 'ignore':
            continue
        if choice == 'new':
            if not row['quantity'] or not row['unit_price']:
                raise HTTPException(422, 'new_item_numbers_required')
            change(f'add:{index}', 'item', '', row, kind='add', source=index)
            continue
        if choice is None:
            unresolved.append({'source': index, 'item': row})
            continue
        if type(choice) is not int or not 0 <= choice < len(old) or choice in used:
            raise HTTPException(422, 'invalid_mapping')
        used.add(choice)
        for key, value in row.items():
            if value and not equal(old[choice].get(key, ''), value, key):
                change(f'item:{choice}:{key}', key, old[choice].get(key, ''), value,
                       kind='item', target=choice, source=index, name=old[choice].get('name', ''))
    # Missing source rows are retained unless separately, explicitly selected.
    for index, row in enumerate(old):
        if index not in used:
            change(f'remove:{index}', 'item', row, '', kind='remove', target=index)
    return {'changes': changes, 'unresolved': unresolved, 'existing_items': old}


def selected_record(current, candidate, result, selected):
    if not isinstance(selected, list) or not selected or any(not isinstance(s, str) for s in selected):
        raise HTTPException(422, 'select_changes')
    known = {c['id']: c for c in result['changes']}
    if len(set(selected)) != len(selected) or any(s not in known for s in selected):
        raise HTTPException(422, 'invalid_changes')
    updated, removed = deepcopy(current), set()
    for key in selected:
        c = known[key]
        if c['kind'] == 'field':
            updated[c['field']] = c['after']
        elif c['kind'] == 'item':
            updated['items'][c['target']][c['field']] = c['after']
        elif c['kind'] == 'add':
            updated['items'].append(deepcopy(c['after']))
        else:
            removed.add(c['target'])
    updated['items'] = [item for i, item in enumerate(updated['items']) if i not in removed]
    if not 0 < len(updated['items']) <= MAX_ITEMS:
        raise HTTPException(422, 'items_required')
    for item in updated['items']:
        for field in ('quantity', 'unit_price'):
            raw = text(item.get(field))
            parsed = number(raw)
            if not parsed:
                raise HTTPException(422, 'invalid_number')
            item[field] = invoice._invoice_number(field, parsed)
    # A previous signature does not constitute approval of amended content.
    updated.pop('signature_image', None)
    retain_previous(updated, current, 'Reviewed revised order')
    return updated


async def body(request):
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 500_000:
            raise HTTPException(413, 'too_large')
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HTTPException(422, 'invalid_changes')


def version(current, candidate, mapping):
    return fingerprint([current, candidate, mapping])


@router.post('/order-changes/{invoice_no}/preview')
async def preview(invoice_no: str, request: Request):
    account = _account(request)
    current = owned(invoice_no, account)
    data = await body(request)
    candidate = clean_candidate(data.get('candidate'))
    mapping = data.get('mapping', {})
    result = compare(current, candidate, mapping)
    return JSONResponse({**result, 'version': version(current, candidate, mapping)}, headers={'Cache-Control': 'no-store'})


@router.post('/order-changes/{invoice_no}/apply')
async def apply(invoice_no: str, request: Request):
    account = _account(request)
    data = await body(request)
    if data.get('confirmed') is not True:
        raise HTTPException(422, 'confirmation_required')
    candidate = clean_candidate(data.get('candidate'))
    mapping = data.get('mapping', {})
    with path_lock(invoice.INVOICE_FILE):
        current = owned(invoice_no, account)
        if data.get('version') != version(current, candidate, mapping):
            raise HTTPException(409, 'changed_since_review')
        result = compare(current, candidate, mapping)
        if result['unresolved']:
            raise HTTPException(422, 'resolve_items')
        updated = selected_record(current, candidate, result, data.get('selected'))
        def replace(rows):
            for index, row in enumerate(rows):
                if row.get('account_id') == account and row.get('invoice_no') == invoice_no:
                    rows[index] = updated
                    return
            raise HTTPException(404, 'Invoice not found')
        locked_json_mutation(invoice.INVOICE_FILE, [], replace, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, 'Apply revised order', 'Commercial Invoice', invoice_no,
                         path=invoice.INVOICE_FILE.with_name('audit_log.json'))
    # No physical quantities are written implicitly. The existing reviewed
    # packing flow shows updated shared fields and requires actual packing data.
    return JSONResponse({'invoice_no': invoice_no,
                         'next_url': '/order-changes/' + quote(invoice_no, safe='') + '/status'},
                        headers={'Cache-Control': 'no-store'})


@router.get('/order-changes/{invoice_no}/status')
def status(invoice_no: str, request: Request):
    current = owned(invoice_no, invoice._account_id(request))
    ko = request.query_params.get('lang', request.cookies.get('tp_language')) == 'ko'
    t = lambda en, kr: kr if ko else en
    number_ = quote(invoice_no, safe='')
    lang = 'ko' if ko else 'en'
    content = '<p>' + t('Previous versions are preserved. Existing downloaded PDFs do not change. Review your signature before sharing amended documents.',
        '이전 내용은 보존했습니다. 이미 내려받은 PDF는 바뀌지 않습니다. 수정본을 보내기 전에 서명을 다시 확인해 주세요.') + '</p>'
    links = [p for p in packing.load_packing_lists(invoice._account_id(request)) if p.get('invoice_no') == invoice_no and not p.get('archived_at')]
    for p in links:
        needs_review = comparable(p) != comparable(current)
        url = '/packing-sync/' if needs_review else '/email-pair/prepare/'
        label = t('Review packing changes', '포장명세서 변경 확인') if needs_review else t('Prepare both PDFs', '두 PDF 준비')
        content += f'<p><a class="tp-btn" href="{url}{quote(p["packing_no"], safe="")}?lang={lang}">{label} · {escape(p["packing_no"])}</a></p>'
        for i, entry in reversed(list(enumerate(p.get(HISTORY, [])))):
            content += f'<p><a href="/order-changes/packing/{quote(p["packing_no"], safe="")}/history/{i}/pdf">{t("Previous Packing List", "변경 전 포장명세서")} · {escape(p["packing_no"])} · {i + 1}</a> · {escape(entry["at"])}</p>'
    if not links:
        content += f'<p><a href="/packing-page?invoice_no={number_}&lang={lang}">{t("Create Packing List", "포장명세서 만들기")}</a></p>'
    content += '<p>' + t('Check actual cartons and weights. Other linked documents are not changed automatically.',
        '실제 박스 수와 중량을 확인하세요. 연결된 다른 서류는 자동으로 바뀌지 않습니다.') + '</p>'
    content += f'<p><a href="/edit-invoice/{number_}?lang={lang}">{t("Review Invoice and signature", "송장·서명 확인")}</a> · <a href="/invoice-pdf/{number_}">{t("Current Invoice PDF", "현재 송장 PDF")}</a></p>'
    content += '<h2>' + t('Previous versions', '이전 내용') + '</h2><ul>'
    for i, entry in reversed(list(enumerate(current.get(HISTORY, [])))):
        content += f'<li><a href="/order-changes/{number_}/history/{i}/pdf">{t("Previous Invoice", "변경 전 송장")} {i + 1}</a> · {escape(entry["at"])}</li>'
    content += '</ul>'
    return HTMLResponse(page_shell(t('Order changes', '주문 변경 결과'), content, lang=lang), headers={'Cache-Control': 'no-store'})


@router.get('/order-changes/{invoice_no}/history/{index}/pdf')
def history_pdf(invoice_no: str, index: int, request: Request):
    current = owned(invoice_no, invoice._account_id(request))
    history = current.get(HISTORY, [])
    if not 0 <= index < len(history):
        raise HTTPException(404, 'Version not found')
    response = invoice.create_invoice_pdf(history[index]['document'], {})
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Disposition'] = f'attachment; filename="previous-invoice-{index + 1}.pdf"'
    return response


@router.get('/order-changes/packing/{packing_no}/history/{index}/pdf')
def packing_history_pdf(packing_no: str, index: int, request: Request):
    current = packing._owned_packing(packing_no, packing._account_id(request))
    history = current.get(HISTORY, [])
    if not 0 <= index < len(history):
        raise HTTPException(404, 'Version not found')
    response = packing.create_packing_list_pdf(history[index]['document'], {}, {})
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Disposition'] = f'attachment; filename="previous-packing-{index + 1}.pdf"'
    return response
