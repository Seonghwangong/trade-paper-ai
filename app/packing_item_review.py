"""Read-only line comparison; never changes item correspondence or weights."""
from collections import Counter
from decimal import Decimal, InvalidOperation
from html import escape


def _text(value):
    return str(value if value is not None else '').strip()


def _name(item):
    # Keep dimensions, punctuation and model numbers: no fuzzy product matching.
    return ' '.join(_text(item.get('name')).split()).casefold()


def _number(value):
    try:
        result = Decimal(_text(value))
        return result if result.is_finite() and 0 < result <= 100000000 else None
    except InvalidOperation:
        return None


def review_items(invoice, packing):
    """Pair only unique full names, independent of row order; flag all else."""
    left, right = invoice.get('items', []), packing.get('items', [])
    lc, rc = Counter(map(_name, left)), Counter(map(_name, right))
    by_name = {_name(item): index for index, item in enumerate(right)}
    used, rows = set(), []
    for index, item in enumerate(left):
        key = _name(item)
        other_index = by_name.get(key) if key and lc[key] == rc[key] == 1 else None
        other = right[other_index] if other_index is not None else None
        issues, delta = [], None
        if other is None:
            issues.append('ambiguous' if key and (lc[key] > 1 or rc[key] > 1) else 'unpaired')
        else:
            used.add(other_index)
            for field in ('unit', 'hs_code', 'origin'):
                a, b = _text(item.get(field)), _text(other.get(field))
                if not a or not b:
                    issues.append(field + '_missing')
                elif a != b:
                    issues.append(field + '_different')
            a, b = _number(item.get('quantity')), _number(other.get('quantity'))
            if a is None or b is None:
                issues.append('quantity_missing')
            elif 'unit_missing' not in issues and 'unit_different' not in issues:
                delta = b - a
                if delta:
                    issues.append('quantity_different')
            if _number(other.get('net_weight')) is None:
                issues.append('net_missing')
        rows.append(dict(invoice_index=index, packing_index=other_index,
                         invoice=item, packing=other, issues=issues, delta=delta))
    for index, item in enumerate(right):
        if index not in used:
            issues = ['unpaired']
            if _number(item.get('net_weight')) is None:
                issues.append('net_missing')
            rows.append(dict(invoice_index=None, packing_index=index, invoice=None,
                             packing=item, issues=issues, delta=None))
    return rows


def render_item_review(invoice, packing, lang='en'):
    """Escape document data and keep the existing reviewed-write flow separate."""
    def t(en, ko):
        return ko if lang == 'ko' else en
    labels = {
        'ambiguous': t('Duplicate name — pair manually', '이름 중복 — 직접 대응 확인'),
        'unpaired': t('No unique counterpart — check name/specification', '대응 미확정 — 물품명·규격 확인'),
        'unit_missing': t('Unit missing', '단위 누락'),
        'unit_different': t('Different units — quantity difference not calculated', '단위 다름 — 수량 차이 계산 안 함'),
        'hs_code_missing': t('HS code missing — check if required', 'HS 코드 누락 — 필요 여부 확인'),
        'hs_code_different': t('HS codes differ', 'HS 코드 다름'),
        'origin_missing': t('Origin missing', '원산지 누락'),
        'origin_different': t('Origins differ', '원산지 다름'),
        'quantity_missing': t('Quantity missing or invalid', '수량 누락 또는 유효하지 않음'),
        'quantity_different': t('Quantity differs', '수량 다름'),
        'net_missing': t('Item net weight missing or invalid', '품목별 순중량 누락 또는 유효하지 않음'),
    }
    def cell(item, index, packed=False):
        if item is None:
            return '—'
        e = lambda value: escape(_text(value), quote=True)
        text = f'<span translate="no">#{index + 1} · {e(item.get("name"))}<br>{e(item.get("quantity"))} {e(item.get("unit"))}<br>HS: {e(item.get("hs_code")) or "—"} · {e(item.get("origin")) or "—"}</span>'
        if packed:
            text += f'<br>{t("Line net weight", "이 품목 총 순중량")}: <span translate="no">{e(item.get("net_weight")) or "—"} kg</span>'
        return text
    result = '<section id="item-comparison"><h2>' + t('Compare items side by side', '품목별 나란히 비교') + '</h2><p>'
    result += t('Unique full item names (including any specification in the name) are aligned regardless of order; only spacing and letter case are ignored. Similar names and duplicates are not automatically paired. This table does not change documents.',
                '물품명에 포함된 규격까지 같은 유일한 품목끼리 순서와 관계없이 나란히 표시합니다. 공백·대소문자만 무시하며, 비슷한 이름이나 중복 이름은 자동 대응하지 않습니다. 이 표는 문서를 변경하지 않습니다.')
    result += '</p><style>#item-comparison .tp-table{min-width:680px;line-height:1.6}#item-comparison td{vertical-align:top;overflow-wrap:anywhere}#item-comparison td:first-child,#item-comparison td:nth-child(2){width:30%}</style><div class="tp-table-wrap"><table class="tp-table"><thead><tr><th scope="col">Invoice</th><th scope="col">Packing List</th><th scope="col">' + t('Quantity difference (PL − Invoice)', '수량 차이 (PL − 송장)') + '</th><th scope="col">' + t('Review', '확인 사항') + '</th></tr></thead><tbody>'
    rows = review_items(invoice, packing)
    for row in rows:
        delta = '—' if row['delta'] is None else format(row['delta'], '+f')
        notes = '<br>'.join(labels[issue] for issue in row['issues']) or t('Listed fields agree', '표시 항목 일치')
        result += '<tr><td>' + cell(row['invoice'], row['invoice_index']) + '</td><td>' + cell(row['packing'], row['packing_index'], True) + '</td><td translate="no">' + delta + '</td><td>' + notes + '</td></tr>'
    if not rows:
        result += '<tr><td colspan="4">' + t('No items to compare', '비교할 품목 없음') + '</td></tr>'
    result += '</tbody></table></div><p>' + t('Net weight is the total for each packing line, not per unit. Check each line against actual packing, especially when entered HS codes differ. No weight allocation or HS classification is performed.',
                '순중량은 개당 중량이 아닌 해당 포장 품목의 총 순중량입니다. 입력한 HS 코드가 나뉘는 경우에도 품목별 실제 포장 중량을 확인하세요. 중량 배분이나 HS 분류는 하지 않습니다.') + '</p></section>'
    return result
