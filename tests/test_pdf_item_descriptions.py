from copy import deepcopy
from io import BytesIO
import re

import pytest
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics

from app import invoice, packing
from app.pdf_fonts import TP_UNICODE


def document(renderer, names):
    payload = {
        'invoice_no': 'INV-DESCRIPTION', 'packing_no': 'PK-DESCRIPTION',
        'invoice_date': '2024-01-01', 'packing_date': '2024-01-02',
        'seller': 'Fictional Seller', 'buyer': 'Fictional Buyer', 'currency': 'USD',
        'items': [dict(name=name, quantity=731 + i, unit_price=6, hs_code='123456',
                       carton=1, net_weight=2, gross_weight=3) for i, name in enumerate(names)],
    }
    original = deepcopy(payload)
    response = renderer(payload, {})
    assert payload == original
    return PdfReader(BytesIO(response.body))


@pytest.mark.parametrize('renderer', [invoice.create_invoice_pdf, packing.create_packing_list_pdf])
@pytest.mark.parametrize('name', [
    'Fictional stainless steel flange, specification ABC-123, diameter 150 mm, '
    'surface finish polished, material grade SAMPLE-316L, final required specification END-SPEC-999',
    '가상제품 규격 日本語仕様 中文规格 ' * 8 + '끝규격-마지막',
    'MODEL-' + 'A' * 180 + '-END',
])
def test_full_item_description_is_present_in_pdf(renderer, name):
    reader = document(renderer, [name])
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    assert ''.join(name.split()) in ''.join(text.split())
    assert 'Date: 2024-01-0' in text


@pytest.mark.parametrize('renderer,width,x', [
    (invoice.create_invoice_pdf, 170, 60),
    (packing.create_packing_list_pdf, 135, 80),
])
def test_description_can_continue_on_new_pages_without_repeating_quantities(renderer, width, x):
    markers = [f'SEG{i:03d}' for i in range(240)]
    reader = document(renderer, [' '.join(markers), 'Second complete item'])
    assert len(reader.pages) >= 2
    texts = []
    lines = []

    def check_line(text, cm, tm, font, size):
        line = text.strip()
        if line.startswith('SEG'):
            assert tm[4] == pytest.approx(x)
            assert tm[5] >= 145
            assert pdfmetrics.stringWidth(line, TP_UNICODE, size) <= width + .01
            lines.append(line)

    for page in reader.pages:
        texts.append(page.extract_text(visitor_text=check_line) or '')
    combined = '\n'.join(texts)
    assert lines
    for marker in markers:
        assert marker in combined
    assert 'Second complete item' in combined
    for quantity in ('731', '732'):
        assert len(re.findall(r'^' + quantity + r'(?:\.0+)?$', combined, re.M)) == 1
    assert combined.count('Authorized Signature:') == 1
    if renderer is packing.create_packing_list_pdf:
        assert 'Total Cartons: 2' in combined
        assert 'Total Net Weight: 4' in combined
        assert 'Total Gross Weight: 6' in combined
