from copy import deepcopy
from decimal import Decimal

import pytest

from app.packing_item_review import render_item_review, review_items


def item(name='Mug 450ml', quantity=10, **extra):
    return dict(name=name, quantity=quantity, unit='PCS', hs_code='691110',
                origin='Korea', net_weight=3, **extra)


def review(left, right):
    return review_items({'items': left}, {'items': right})


def test_reordered_full_names_pair_without_product_codes_and_without_mutation():
    left = [item(), item('Plate 20cm')]
    right = [item('Plate 20cm'), item()]
    before = deepcopy((left, right))
    rows = review(left, right)
    assert [r['packing_index'] for r in rows] == [1, 0]
    assert all(r['delta'] == 0 and not r['issues'] for r in rows)
    assert (left, right) == before


def test_similar_names_and_different_specifications_are_not_paired():
    rows = review([item('Mug 450ml'), item('Plate 20cm')],
                  [item('Mug 350ml'), item('Plate 20 cm')])
    assert len(rows) == 4
    assert all(r['delta'] is None and 'unpaired' in r['issues'] for r in rows)


@pytest.mark.parametrize('left_count,right_count', [(2, 1), (1, 2), (2, 2)])
def test_duplicate_names_are_never_assigned_by_quantity_or_position(left_count, right_count):
    rows = review([item(quantity=n + 1) for n in range(left_count)],
                  [item(quantity=n + 1) for n in range(right_count)])
    assert len(rows) == left_count + right_count
    assert all(r['delta'] is None for r in rows)
    assert 'ambiguous' in rows[0]['issues']


def test_quantity_delta_is_decimal_and_direction_is_packing_minus_invoice():
    row = review([item(quantity='10.2')], [item(quantity='9.1')])[0]
    assert row['delta'] == Decimal('-1.1')
    assert row['issues'] == ['quantity_different']


@pytest.mark.parametrize('field,value,issue', [
    ('unit', 'KG', 'unit_different'), ('unit', '', 'unit_missing'),
    ('quantity', 'NaN', 'quantity_missing'), ('quantity', '', 'quantity_missing'),
])
def test_quantity_difference_requires_valid_values_and_same_units(field, value, issue):
    other = item(); other[field] = value
    row = review([item()], [other])[0]
    assert row['delta'] is None and issue in row['issues']


@pytest.mark.parametrize('value', ['', None, 0, -1, 'NaN'])
def test_missing_or_invalid_net_weight_is_flagged_per_line(value):
    other = item(); other['net_weight'] = value
    row = review([item()], [other])[0]
    assert 'net_missing' in row['issues']
    assert other['net_weight'] == value


def test_entered_hs_differences_do_not_determine_pair_or_allocate_weight():
    other = item(); other.update(hs_code='691200', net_weight='')
    row = review([item()], [other])[0]
    assert row['packing_index'] == 0
    assert row['issues'] == ['hs_code_different', 'net_missing']
    assert other['net_weight'] == ''


def test_empty_names_remain_unpaired_and_missing_hs_is_not_reported_verified():
    assert len(review([item('')], [item('')])) == 2
    other = item(); other['hs_code'] = ''
    assert 'hs_code_missing' in review([item()], [other])[0]['issues']


def test_whitespace_and_case_only_are_normalized_and_html_is_escaped():
    rows = review([item(' Mug   450ml ')], [item('mug 450ML')])
    assert len(rows) == 1 and rows[0]['packing_index'] == 0
    html = render_item_review({'items': [item('<script>alert(1)</script>')]}, {'items': []}, 'ko')
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '품목별 나란히 비교' in html and '대응 미확정' in html


def test_extra_packing_line_is_visible_with_its_missing_weight():
    extra = item('Extra'); extra['net_weight'] = ''
    rows = review([item()], [item(), extra])
    assert rows[1]['invoice_index'] is None and rows[1]['packing_index'] == 1
    assert rows[1]['issues'] == ['unpaired', 'net_missing']
