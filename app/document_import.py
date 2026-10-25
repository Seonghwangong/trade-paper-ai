"""Read-only file extraction. Saving uses the existing authenticated invoice flow."""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.account_company import load_account_company
from app.routers.company import ACCOUNT_COMPANIES_FILE

router = APIRouter()
MAX_FILE = 8 * 1024 * 1024
MAX_TEXT = 200_000
MAX_ITEMS = 100
ALIASES = {
    'name': ('description', 'description of goods', 'item description', 'product', 'product name', 'item', 'item name', '품명', '품목', '제품명'),
    'quantity': ('quantity', 'qty', '수량'),
    'unit_price': ('unit price', 'unitprice', 'price', 'price per unit', '단가'),
    'unit': ('unit', 'uom', '단위'),
    'amount': ('amount', 'total', 'line total', '금액'),
    'hs_code': ('hs code', 'hscode', 'hs', '세번'),
    'origin': ('origin', 'country of origin', '원산지'),
}
META = {
    'seller': ('seller', 'exporter', 'shipper', '수출자', '판매자', '공급자'),
    'buyer': ('buyer', 'importer', 'consignee', 'sold to', 'bill to', '수입자', '거래처', '구매자'),
    'seller_address': ('seller address', 'exporter address', '수출자 주소'),
    'buyer_address': ('buyer address', 'consignee address', '수입자 주소', '거래처 주소'),
    'buyer_email': ('buyer email', '수입자 이메일'),
    'currency': ('currency', '통화'),
    'invoice_date': ('invoice date', 'date', '발행일', '작성일'),
    'trade_term': ('incoterms', 'incoterm', 'trade term', '무역조건', '인도조건'),
    'payment_term': ('payment terms', 'payment term', '결제조건'),
    'source_reference': ('invoice no', 'invoice number', 'invoice #', '송장번호'),
}


def normalized(value):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', str(value)).casefold())


HEADER_MAP = {normalized(alias): key for key, aliases in ALIASES.items() for alias in aliases}
META_MAP = {normalized(alias): key for key, aliases in META.items() for alias in aliases}


def number(value):
    text = str(value).strip()
    # Only unambiguous dot decimals and groups of three comma-separated digits.
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,6})?', text):
        return ''
    try:
        result = Decimal(text.replace(',', ''))
        if result > Decimal('1000000000000'):
            return ''
        return format(result, 'f')
    except InvalidOperation:
        return ''


def _date(value):
    value = str(value).strip()
    if re.fullmatch(r'\d{4}[./-]\d{1,2}[./-]\d{1,2}', value):
        y, m, d = map(int, re.split('[./-]', value))
        try:
            return date(y, m, d).isoformat()
        except ValueError:
            pass
    return ''


def parse_rows(rows):
    fields, items, warnings = {}, [], []
    headers = None
    skipped = 0
    raw_lines = []
    pending_meta = {}
    def remember(key, value):
        if key in fields and fields[key] != value:
            warnings.append('conflicting_' + key)
        else:
            fields[key] = value
    for row in rows:
        cells = [str(c).strip()[:1000] for c in row]
        if not any(cells):
            continue
        raw_lines.append('\t'.join(cells))
        mapped = {i: (HEADER_MAP | META_MAP)[normalized(c)] for i, c in enumerate(cells) if normalized(c) in (HEADER_MAP | META_MAP)}
        if 'name' in mapped.values() and 'quantity' in mapped.values():
            if len(set(mapped.values())) != len(mapped):
                warnings.append('duplicate_headers')
                headers = None
            else:
                headers = mapped
            pending_meta = {}
            continue
        if pending_meta:
            for index, key in pending_meta.items():
                value = cells[index] if index < len(cells) else ''
                if value and normalized(value) not in META_MAP and ':' not in value:
                    remember(key, value)
            pending_meta = {}
            continue
        meta_found = False
        for i, cell in enumerate(cells):
            parts = re.split(r'\s*[:：]\s*', cell, maxsplit=1)
            label = parts[0]
            key = META_MAP.get(normalized(label))
            if key:
                value = parts[1] if len(parts) == 2 else (cells[i + 1] if i + 1 < len(cells) else '')
                if value and normalized(value) not in META_MAP:
                    remember(key, value)
                else:
                    pending_meta[i] = key
                meta_found = True
        if meta_found or not headers:
            continue
        if re.match(r'^(?:grand\s*total|sub\s*total|total|합계|총계)\b|^(?:합계|총계)', cells[0], re.I):
            headers = None
            continue
        item = {key: cells[i] if i < len(cells) else '' for i, key in headers.items()}
        for key in META:
            if key in item:
                value = item.pop(key)
                if value:
                    remember(key, value)
        if not item.get('name'):
            skipped += 1
            continue
        if not item.get('quantity') and not item.get('unit_price'):
            skipped += 1
            continue
        if len(items) >= MAX_ITEMS:
            raise ValueError('too_many_items')
        for key in ('quantity', 'unit_price', 'amount'):
            if key in item or key != 'amount':
                raw = item.get(key, '')
                money = re.fullmatch(r'([A-Z]{3})\s+(.+)', raw) if key != 'quantity' else None
                if money:
                    remember('currency', money[1])
                    raw = money[2]
                item[key] = number(raw)
        if item.get('amount') and item.get('quantity') and item.get('unit_price'):
            if abs(Decimal(item['quantity']) * Decimal(item['unit_price']) - Decimal(item['amount'])) > Decimal('.01'):
                item['check_amount'] = True
        items.append(item)
    if skipped:
        warnings.append('unrecognized_rows')
    if not items:
        warnings.append('no_items')
    for key in ('seller', 'buyer', 'invoice_date', 'currency'):
        fields.setdefault(key, '')
    fields['invoice_date'] = _date(fields['invoice_date'])
    currency = fields['currency'].upper()
    fields['currency'] = currency if re.fullmatch(r'[A-Z]{3}', currency) else ''
    return {'fields': fields, 'items': items, 'warnings': sorted(set(warnings)), 'source_text': '\n'.join(raw_lines)[:MAX_TEXT]}


def text_rows(text):
    """Columns are tabs (PDF/OCR geometry) or two spaces; no guessed numbers."""
    rows = []
    for line in text.splitlines():
        cells = re.split(r'\t| {2,}', line.strip())
        rows.append(cells)
    return rows


def spreadsheet_rows(data, filename, sheet_index=0):
    if filename.lower().endswith('.csv'):
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = data.decode('cp949')
            except UnicodeDecodeError as exc:
                raise ValueError('encoding') from exc
        if len(text) > MAX_TEXT:
            raise ValueError('too_large')
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(StringIO(text), dialect))
        if len(rows) > 2000 or any(len(r) > 100 for r in rows):
            raise ValueError('too_large')
        return rows, ['CSV'], []
    if not filename.lower().endswith('.xlsx'):
        raise ValueError('unsupported')
    try:
        with ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > 2000 or sum(i.file_size for i in infos) > 20 * 1024 * 1024:
                raise ValueError('too_large')
            if any(i.flag_bits & 1 for i in infos):
                raise ValueError('encrypted')
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            def xml(name):
                content = archive.read(name)
                if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
                    raise ValueError('invalid_file')
                return ET.fromstring(content)
            workbook = xml('xl/workbook.xml')
            sheets = workbook.findall('s:sheets/s:sheet', ns)
            if sheet_index < 0 or sheet_index >= len(sheets):
                raise ValueError('invalid_sheet')
            rels = {r.attrib['Id']: r.attrib['Target'] for r in xml('xl/_rels/workbook.xml.rels') if r.attrib.get('TargetMode') != 'External'}
            rid = sheets[sheet_index].attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            target = rels.get(rid, '')
            target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            if '..' in target.split('/') or not target.startswith('xl/worksheets/'):
                raise ValueError('invalid_file')
            strings = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                strings = [''.join(t.text or '' for t in si.findall('.//s:t', ns)) for si in xml('xl/sharedStrings.xml')]
            rows, warnings = [], []
            for row in xml(target).findall('s:sheetData/s:row', ns):
                values = {}
                for cell in row.findall('s:c', ns):
                    ref = cell.attrib.get('r', '')
                    letters = re.match(r'[A-Z]+', ref)
                    if not letters:
                        continue
                    col = 0
                    for char in letters[0]:
                        col = col * 26 + ord(char) - 64
                    if col > 100:
                        raise ValueError('too_large')
                    if cell.find('s:f', ns) is not None:
                        warnings.append('formulas_skipped')
                        value = ''  # Never execute formulas or trust a stale cached result.
                    elif cell.attrib.get('t') == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('.//s:t', ns))
                    else:
                        value = cell.findtext('s:v', '', ns)
                        if cell.attrib.get('t') == 's':
                            value = strings[int(value)]
                    values[col - 1] = value
                rows.append([values.get(i, '') for i in range(max(values, default=-1) + 1)])
                if len(rows) > 2000 or sum(len(str(c)) for c in rows[-1]) > MAX_TEXT:
                    raise ValueError('too_large')
            if sum(len(str(c)) for row in rows for c in row) > MAX_TEXT:
                raise ValueError('too_large')
            return rows, [s.attrib.get('name', 'Sheet') for s in sheets], sorted(set(warnings))
    except (BadZipFile, ET.ParseError, KeyError, IndexError, RuntimeError, OverflowError) as exc:
        raise ValueError('invalid_file') from exc


def _account(request):
    user = request.scope.get('trade_paper_user') or {}
    account = str(user.get('account_id', '')).strip()
    if not account:
        raise HTTPException(401, 'Login required')
    if user.get('role') == 'Viewer':
        raise HTTPException(403, 'Viewer role is read-only')
    return account


@router.get('/import-document', response_class=HTMLResponse)
def import_page(request: Request):
    account = _account(request)
    lang = 'ko' if request.query_params.get('lang', request.cookies.get('tp_language', 'en')) == 'ko' else 'en'
    company = load_account_company(account, ACCOUNT_COMPANIES_FILE)
    bootstrap = json.dumps({'lang': lang, 'company': {k: company.get(k, '') for k in ('name', 'address')}}, ensure_ascii=False).replace('<', '\\u003c')
    template = Path(__file__).with_name('templates').joinpath('document_import.html').read_text()
    return HTMLResponse(template.replace('__LANG__', lang).replace('__BOOTSTRAP__', bootstrap), headers={'Cache-Control': 'no-store'})


@router.post('/import-document/extract')
async def extract(request: Request):
    _account(request)
    # Stream before multipart parsing to bound both memory and temporary storage.
    content = bytearray()
    async for part in request.stream():
        content.extend(part)
        if len(content) > MAX_FILE + 65536:
            raise HTTPException(413, 'too_large')
    request._body = bytes(content)
    try:
        if request.headers.get('content-type', '').startswith('application/json'):
            body = json.loads(content)
            text = body.get('text')
            if not isinstance(text, str) or len(text) > MAX_TEXT:
                raise ValueError('too_large')
            result = parse_rows(text_rows(text))
        else:
            async with request.form(max_files=1, max_fields=1) as form:
                upload = form.get('file')
                if not hasattr(upload, 'read'):
                    raise ValueError('invalid_file')
                data = await upload.read(MAX_FILE + 1)
                if len(data) > MAX_FILE:
                    raise ValueError('too_large')
                rows, sheets, warnings = spreadsheet_rows(data, upload.filename or '', int(form.get('sheet', 0)))
                result = parse_rows(rows)
                result['sheets'] = sheets
                result['warnings'] = sorted(set(result['warnings'] + warnings))
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})
    except (ValueError, TypeError, csv.Error, AttributeError) as exc:
        code = str(exc) if str(exc) in {'too_large', 'too_many_items', 'unsupported', 'encoding', 'encrypted', 'invalid_sheet'} else 'invalid_file'
        raise HTTPException(422, code) from exc
