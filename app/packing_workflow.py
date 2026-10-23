"""Account-scoped packing calculations and reviewed Invoice refreshes."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
import hashlib
import html
import json
from urllib.parse import quote

from fastapi import Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.storage import load_json_strict, locked_json_mutation, path_lock
from app.ui import page_shell

PARTY = ('seller', 'seller_address', 'seller_email', 'seller_phone', 'buyer', 'buyer_address', 'buyer_email')
IDENTITY = ('name', 'hs_code', 'origin', 'unit')
SHARED = (*IDENTITY, 'quantity')
WEIGHTS = ('carton', 'net_weight', 'gross_weight')


def number(value, label, *, zero=False):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or (not zero and n == 0) or n > 100000000:
            raise ValueError()
        if n != n.quantize(Decimal(".000001")):
            raise ValueError()
        return n
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(422, f'{label}: enter a finite {"non-negative" if zero else "positive"} number (maximum 100000000).')


def calculate(payload):
    quantity = number(payload.get('quantity'), 'Quantity')
    capacity = number(payload.get('units_per_carton'), 'Units per carton')
    net_each = number(payload.get('net_per_unit'), 'Net weight per unit')
    tare = number(payload.get('tare_per_carton'), 'Packaging weight per carton', zero=True)
    cartons = (quantity / capacity).to_integral_value(rounding=ROUND_CEILING)
    net = quantity * net_each
    gross = net + cartons * tare
    if net < Decimal(".001") or max(cartons, net, gross) > 100000000:
        raise HTTPException(422, 'Calculated totals must be at least 0.001 kg and at most 100000000.')
    return {'carton': int(cartons), 'net_weight': str(net.quantize(Decimal('.001'), rounding=ROUND_HALF_UP)),
            'gross_weight': str(gross.quantize(Decimal('.001'), rounding=ROUND_HALF_UP))}


def identity(item):
    return tuple(str(item.get(k, '') or '').strip() for k in IDENTITY)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def proposal(current, source):
    """Only unambiguous unchanged item/quantity matches keep physical totals."""
    result = deepcopy(current)
    for key in PARTY:
        result[key] = source.get(key, '')
    old_items, new_items = current.get('items', []), source.get('items', [])
    old_counts, new_counts = Counter(map(identity, old_items)), Counter(map(identity, new_items))
    result['items'] = []
    for item in new_items:
        key = identity(item)
        prior = next((old for old in old_items if identity(old) == key), {}) if old_counts[key] == new_counts[key] == 1 else {}
        row = {k: item.get(k, '') for k in SHARED}
        # Physical totals cannot be inferred from a changed quantity or product.
        same_quantity = False
        if prior:
            try:
                same_quantity = Decimal(str(prior.get('quantity'))) == Decimal(str(item.get('quantity')))
            except InvalidOperation:
                pass
        row.update({k: prior.get(k, '') if same_quantity else '' for k in WEIGHTS})
        if prior.get('item_id'):
            row['item_id'] = prior['item_id']
        result['items'].append(row)
    return result


def comparable(record):
    def quantity(value):
        try:
            n = Decimal(str(value))
            return str(n.normalize()) if n.is_finite() else str(value)
        except InvalidOperation:
            return str(value or '')
    return {**{k: str(record.get(k, '') or '').strip() for k in PARTY},
            'items': [{k: quantity(row.get(k)) if k == 'quantity' else str(row.get(k, '') or '').strip()
                       for k in SHARED} for row in record.get('items', [])]}

def attach_routes(router):
    from app import packing as pack

    @router.post('/packing-calculate')
    def packing_calculate(request: Request, payload: dict = Body(...)):
        pack._account_id(request)
        return calculate(payload)

    @router.get('/packing-defaults')
    def packing_defaults(request: Request):
        account = pack._account_id(request)
        return [r['settings'] for r in load_json_strict(pack.PACKING_FILE.with_name('packing_defaults.json'), [], list)
                if r.get('account_id') == account]

    @router.post('/packing-defaults')
    def save_defaults(request: Request, payload: dict = Body(...)):
        account = pack._account_id(request)
        key = identity(payload)
        if not key[0] or any(len(part) > 200 for part in key):
            raise HTTPException(422, 'Enter an item name (maximum 200 characters per field).')
        calculate(dict(payload, quantity=1))
        settings = {k: payload.get(k, '') for k in (*IDENTITY, 'units_per_carton', 'net_per_unit', 'tare_per_carton')}
        def save(rows):
            old = next((r for r in rows if r.get('account_id') == account and identity(r['settings']) == key), None)
            if old is not None:
                old['settings'] = settings
            else:
                if sum(r.get('account_id') == account for r in rows) >= 500:
                    raise HTTPException(422, 'Maximum 500 saved packing settings per account.')
                rows.append({'account_id': account, 'settings': settings})
        locked_json_mutation(pack.PACKING_FILE.with_name('packing_defaults.json'), [], save, list)
        return {'saved': True}

    @router.get('/packing-sync/{packing_no}')
    def sync_preview(packing_no: str, request: Request):
        account = pack._account_id(request)
        current = pack._owned_packing(packing_no, account)
        source = pack._owned_invoice(current['invoice_no'], account)
        proposed = proposal(current, source)
        esc = lambda x: html.escape(str(x or ''), quote=True)
        target = quote(packing_no, safe='')
        content = '<p>Compare the changes below. Applying replaces parties and items from the Invoice. Changed or ambiguous items need cartons and weights entered again. Existing downloaded PDFs and other linked documents are not updated.</p>'
        content += '<h2>Before / After</h2><table><thead><tr><th>Field</th><th>Saved Packing List</th><th>Current Invoice</th></tr></thead><tbody>'
        for key in PARTY:
            content += f'<tr><th>{esc(key.replace("_", " ").title())}</th><td translate="no">{esc(current.get(key))}</td><td translate="no">{esc(source.get(key))}</td></tr>'
        content += '</tbody></table><h2>Saved items</h2><ul translate="no">'
        for row in current.get('items', []):
            content += f'<li>{esc(row.get("name"))}: {esc(row.get("quantity"))} {esc(row.get("unit"))}</li>'
        content += f'</ul><form method="post" action="/packing-sync/{target}" data-native-submit="true">'
        content += f'<input type="hidden" name="version" value="{fingerprint([current, source])}"><h2>Items to apply</h2>'
        for row in proposed['items']:
            content += f'<section class="packing-sync-item"><p translate="no">{esc(row.get("name"))} · {esc(row.get("quantity"))} {esc(row.get("unit"))}</p>'
            for key in SHARED:
                content += f'<input type="hidden" name="{ "item_name" if key == "name" else key}" value="{esc(row.get(key))}">'
            for key in WEIGHTS:
                content += f'<label>{key.replace("_", " ").title()}<input name="{key}" type="number" min="0" step="{"1" if key == "carton" else "0.001"}" required value="{esc(row.get(key))}"></label>'
            content += '</section>'
        content += '<p>All weights are shipment-line totals in kg. Check the actual packing before applying.</p><button type="submit">Apply reviewed Invoice changes</button></form>'
        content += f'<p><a href="/edit-packing/{target}">Cancel</a></p><script src="/static/packing-assistant.js" defer></script>'
        return HTMLResponse(page_shell('Refresh Packing List from Invoice', content), headers={'Cache-Control': 'no-store'})

    @router.post('/packing-sync/{packing_no}')
    def sync_apply(packing_no: str, request: Request, version: str = Form(...),
                   carton: list[str] = Form([]), net_weight: list[str] = Form([]), gross_weight: list[str] = Form([])):
        account = pack._account_id(request)
        # Lock the source and destination together; refuse stale previews instead of
        # overwriting a concurrent edit. Only the Packing file is written.
        with path_lock(pack.invoice_module.INVOICE_FILE), path_lock(pack.PACKING_FILE):
            current = pack._owned_packing(packing_no, account)
            source = pack._owned_invoice(current['invoice_no'], account)
            if fingerprint([current, source]) != version:
                raise HTTPException(409, 'A document changed. Reopen the Invoice comparison and review again.')
            updated = proposal(current, source)
            if not updated['items'] or any(len(v) != len(updated['items']) for v in (carton, net_weight, gross_weight)):
                raise HTTPException(422, 'Enter cartons and weights for every item.')
            for index, row in enumerate(updated['items']):
                c = number(carton[index], 'Cartons')
                n = number(net_weight[index], 'Net weight')
                g = number(gross_weight[index], 'Gross weight')
                if c != c.to_integral_value() or g < n:
                    raise HTTPException(422, 'Cartons must be whole numbers; gross weight must be at least net weight.')
                row.update(carton=int(c), net_weight=str(n), gross_weight=str(g))
            pack.assign_item_ids(updated['items'], [r.get('item_id', '') for r in updated['items']])
            def replace(rows):
                for index, row in enumerate(rows):
                    if row.get('account_id') == account and row.get('packing_no') == packing_no:
                        rows[index] = updated
                        return
                raise HTTPException(404, 'Packing List not found')
            locked_json_mutation(pack.PACKING_FILE, [], replace, list)
        from app.audit_log import record_request_audit
        record_request_audit(request, 'Refresh from Invoice', 'Packing List', packing_no, path=pack.PACKING_FILE.with_name('audit_log.json'))
        return RedirectResponse('/edit-packing/' + quote(packing_no, safe=''), status_code=303)
