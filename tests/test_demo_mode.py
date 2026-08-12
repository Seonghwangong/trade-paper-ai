from pathlib import Path

from app import buyer, product, release_pages


def _body(response):
    return response.body.decode("utf-8")


def test_demo_page_presents_the_real_workflow_in_order():
    body = _body(release_pages.demo_page())
    steps = [
        "Step 1 · Company",
        "Step 2 · Buyer",
        "Step 3 · Product",
        "Step 4 · Invoice",
        "Step 5 · Packing",
        "Step 6 · Shipment Hub",
    ]
    assert [body.index(step) for step in steps] == sorted(body.index(step) for step in steps)
    for path in (
        "/company?demo=1",
        "/buyer-form?demo=1",
        "/product-form?demo=1",
        "/invoice?demo=1",
        "/invoice-list",
        "/shipment-form",
    ):
        assert f'href="{path}"' in body
    assert "Temporary values — nothing is saved until you press Save." in body
    assert "No fake Packing data is created." in body


def test_buyer_and_product_demo_prefills_are_query_scoped():
    buyer_demo = _body(buyer.buyer_form(demo=1))
    product_demo = _body(product.product_form(demo=1))
    assert 'value="Sakura Retail Co."' in buyer_demo
    assert 'value="Tokyo, Japan"' in buyer_demo
    assert 'value="buyer@example.jp"' in buyer_demo
    assert 'value="Japan"' in buyer_demo
    assert 'value="Notebook Computer"' in product_demo
    assert 'value="847130"' in product_demo
    assert 'value="850"' in product_demo
    assert 'value="Korea"' in product_demo
    assert "Demo Preview" in buyer_demo and "Demo Preview" in product_demo

    buyer_regular = _body(buyer.buyer_form())
    product_regular = _body(product.product_form())
    assert "Sakura Retail Co." not in buyer_regular
    assert "Notebook Computer" not in product_regular
    assert "Demo Preview" not in buyer_regular
    assert "Demo Preview" not in product_regular


def test_static_demo_prefills_require_demo_query_and_never_submit_automatically():
    static_dir = Path(__file__).parents[1] / "app" / "static"
    company = (static_dir / "company.html").read_text(encoding="utf-8")
    invoice = (static_dir / "invoice.html").read_text(encoding="utf-8")

    assert 'params.get("demo") !== "1"' in company
    assert 'document.getElementById("name").value = "Busan Comfort Trading"' in company
    assert 'params.get("demo") !== "1"' in invoice
    assert 'document.getElementById("qty1").value = "1"' in invoice
    assert 'product.name === "Notebook Computer"' in invoice
    assert "applyDemoPrefill();" in company and "applyDemoPrefill();" in invoice
