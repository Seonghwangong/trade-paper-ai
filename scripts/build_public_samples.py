"""Build fixed fictional previews using the real renderers, without loading user data.

Run from the repository root: venv/bin/python scripts/build_public_samples.py
Only the two named static PDFs are written. No account or document is created.
"""
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.colors import HexColor
from app.invoice import create_invoice_pdf
from app.packing import create_packing_list_pdf


class SampleCanvas(Canvas):
    def showPage(self):
        self.saveState()
        self.setFillColor(HexColor('#1D4ED8'))
        self.setFont('Helvetica-Bold', 9)
        self.drawString(45, self._pagesize[1] - 107,
                        'FICTIONAL SAMPLE - Not for commercial use')
        self.setFont('Helvetica', 8)
        self.drawString(45, 25, 'Try your own sample: www.tradepaper.ai/getting-started')
        self.restoreState()
        super().showPage()


def build():
    company = {'name': 'Sample Export Co.', 'address': '100 Example Road, Sample City',
               'email': 'seller@example.com', 'phone': ''}
    parties = {'seller': company['name'], 'seller_address': company['address'],
               'seller_email': company['email'], 'seller_phone': '',
               'buyer': 'Sample Import Co.', 'buyer_address': '200 Example Street, Demo City',
               'buyer_email': 'buyer@example.com', 'invoice_no': 'SAMPLE-INV-001'}
    invoice = dict(parties, currency='USD', items=[
        {'name': 'Sample Cotton T-shirt', 'hs_code': '', 'quantity': 100,
         'unit_price': 8, 'origin': 'Korea', 'unit': 'PCS'},
        {'name': 'Sample Canvas Tote Bag', 'hs_code': '', 'quantity': 50,
         'unit_price': 4, 'origin': 'Korea', 'unit': 'PCS'}])
    packing = dict(parties, packing_no='SAMPLE-PK-001', items=[
        {'name': 'Sample Cotton T-shirt', 'hs_code': '', 'quantity': 100,
         'carton': 5, 'net_weight': 20, 'gross_weight': 25, 'unit': 'PCS'},
        {'name': 'Sample Canvas Tote Bag', 'hs_code': '', 'quantity': 50,
         'carton': 2, 'net_weight': 10, 'gross_weight': 12, 'unit': 'PCS'}])
    directory = ROOT/'app/static/samples'
    directory.mkdir(parents=True, exist_ok=True)
    with patch('reportlab.pdfgen.canvas.Canvas', SampleCanvas):
        docs = {'commercial-invoice.pdf': create_invoice_pdf(invoice, company).body,
                'packing-list.pdf': create_packing_list_pdf(packing, company, {}).body}
    for name, content in docs.items():
        assert content.startswith(b'%PDF')
        (directory/name).write_bytes(content)
        print(f'{name}: {len(content)} bytes')


if __name__ == '__main__':
    build()
