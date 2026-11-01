"""Conservative order-note parsing. No network, persistence or guessed money values."""
import re

from app.document_import import META_MAP, normalized, number, _date

MAX_NOTE = 8000
NUM = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,6})?'
UNITS = {'개': 'PCS', 'pcs': 'PCS', 'pieces': 'PCS', 'piece': 'PCS',
         '세트': 'SET', 'sets': 'SET', 'set': 'SET', 'kg': 'KG',
         '킬로그램': 'KG', '박스': 'CTN', 'cartons': 'CTN', 'ctn': 'CTN'}
UNIT = '|'.join(sorted(UNITS, key=len, reverse=True))
ITEM = re.compile(
    rf'(?P<name>.+?)\s+(?P<quantity>{NUM})\s*(?P<unit>{UNIT})'
    rf'\s*[,，]?\s*(?:개당|단가|각|at|@|each)\s*:?\s*'
    rf'(?:(?P<before>[A-Za-z]{{3}})\s+)?(?P<price>{NUM})'
    rf'\s*(?P<after>[A-Za-z]{{3}}|달러|원|유로)?(?:\s+each)?', re.I)


def parse_order_note(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('empty_note')
    if len(text) > MAX_NOTE:
        raise ValueError('note_too_large')
    fields, items, unmatched, currencies = {}, [], [], set()
    conflicts = set()
    def remember(key, value):
        if key in fields and fields[key] != value:
            conflicts.add(key)
        fields.setdefault(key, value)
    # Semicolons/newlines separate items. Commas stay intact in numbers and names.
    for raw in re.split(r'[\n;；]+', text):
        line = raw.strip()
        if not line:
            continue
        parts = re.split(r'[:：]', line, maxsplit=1)
        key = META_MAP.get(normalized(parts[0])) if len(parts) == 2 else None
        if key:
            value = parts[1].strip()
            if not value or len(value) > (1000 if 'address' in key else 200):
                unmatched.append(line)
                continue
            remember(key, value)
            if key == 'currency':
                currencies.add(value.upper())
            continue
        # A buyer label may be spoken without punctuation.
        party = re.fullmatch(r'(?:거래처|수입자|buyer)\s+(.+)', line, re.I)
        if party and len(party[1]) <= 200:
            remember('buyer', party[1].strip())
            continue
        match = ITEM.fullmatch(line)
        if not match or len(match['name']) > 200:
            unmatched.append(line)
            continue
        quantity, price = number(match['quantity']), number(match['price'])
        if not quantity or not price or float(quantity) <= 0:
            unmatched.append(line)
            continue
        currency = (match['before'] or match['after'] or '').upper()
        # "dollar" has many currencies; the customer must specify which one.
        currency = {'원': 'KRW', '유로': 'EUR', '달러': 'DOLLAR_UNSPECIFIED'}.get(currency, currency)
        if match['before'] and match['after'] and match['before'].upper() != match['after'].upper():
            currencies.add('CONFLICT')
        if currency:
            currencies.add(currency)
        items.append({'name': match['name'].strip(), 'quantity': quantity,
                      'unit_price': price, 'unit': UNITS[match['unit'].lower()],
                      'hs_code': '', 'origin': ''})
        if len(items) > 100:
            raise ValueError('too_many_items')
    for key in conflicts:
        fields[key] = ''
    currency = next(iter(currencies)) if len(currencies) == 1 else ''
    fields['currency'] = currency if re.fullmatch('[A-Z]{3}', currency) else ''
    if 'currency' in conflicts:
        fields['currency'] = ''
    fields['invoice_date'] = _date(fields.get('invoice_date', ''))
    warnings = ['note_review']
    if unmatched:
        warnings.append('note_unmatched')
    if len(currencies) > 1 or (currencies and not fields['currency']):
        warnings.append('note_currency')
        # Mixed-currency unit prices cannot be used as a single-currency invoice.
        for item in items:
            item['unit_price'] = ''
    if conflicts:
        warnings.append('conflict')
        warnings.extend('conflicting_' + key for key in sorted(conflicts))
    if not items:
        warnings.append('no_items')
    return {'fields': fields, 'items': items, 'warnings': warnings,
            'source_text': text, 'unmatched_lines': unmatched}
