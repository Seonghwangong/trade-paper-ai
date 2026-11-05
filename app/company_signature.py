"""Account-owned signature/stamp images and immutable invoice snapshots."""
import base64
import binascii
from io import BytesIO
import warnings
from PIL import Image, UnidentifiedImageError
from fastapi import APIRouter, Body, HTTPException, Request
from app.storage import data_path, load_json_strict, locked_json_mutation
from app.validation import DataValidationError
from app.account_company import load_account_company

router = APIRouter()
SIGNATURE_FILE = data_path('company_signatures.json')
ACCOUNT_COMPANIES_FILE = data_path('account_companies.json')
MAX_UPLOAD_BYTES = 1024 * 1024


def normalize_signature(value):
    if not isinstance(value, str) or len(value) > MAX_UPLOAD_BYTES * 4 // 3 + 100:
        raise DataValidationError('Signature', 'Use a PNG or JPEG image up to 1 MB.', 'Choose a smaller signature or stamp image.')
    try:
        header, encoded = value.split(',', 1)
        if header not in ('data:image/png;base64', 'data:image/jpeg;base64'):
            raise ValueError('Unsupported image type')
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError('Image too large')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if image.format not in ('PNG', 'JPEG') or image.width * image.height > 4_000_000 or max(image.size) > 4096:
                    raise ValueError('Unsupported dimensions')
                image.load()
                image = image.convert('RGBA')
                image.thumbnail((600, 300))
                output = BytesIO()
                image.save(output, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')
    except (ValueError, OSError, binascii.Error, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise DataValidationError('Signature', 'The signature image could not be read.', 'Choose a valid PNG or JPEG up to 1 MB and 4 million pixels.') from exc


def account_signature(account_id):
    if not account_id:
        raise HTTPException(status_code=401, detail='Sign in to manage your signature.')
    return next((record.get('image', '') for record in load_json_strict(SIGNATURE_FILE, [], list)
                 if isinstance(record, dict) and record.get('account_id') == account_id), '')


def _account(request):
    account = str((request.scope.get('trade_paper_user') or {}).get('account_id') or '').strip()
    if not account:
        raise HTTPException(status_code=401, detail='Sign in to manage your signature.')
    return account


@router.get('/company-signature')
def get_signature(request: Request):
    return {'image': account_signature(_account(request))}


@router.post('/company-signature')
def save_signature(request: Request, payload: dict = Body(...)):
    account = _account(request)
    image = normalize_signature(payload.get('image'))
    def replace(records):
        record = next((item for item in records if item.get('account_id') == account), None)
        if record is None:
            records.append({'account_id': account, 'image': image})
        else:
            record['image'] = image
    locked_json_mutation(SIGNATURE_FILE, [], replace, list)
    from app.audit_log import record_request_audit
    record_request_audit(request, 'Update', 'Company signature', 'Registered image', path=SIGNATURE_FILE.with_name('audit_log.json'))
    return {'image': image}


def signature_for_invoice(account_id, seller):
    company = load_account_company(account_id, ACCOUNT_COMPANIES_FILE)
    if str(seller).strip().casefold() != str(company.get('name', '')).strip().casefold():
        raise DataValidationError('Signature', 'This signature belongs to your saved company.', 'Use your company as the seller or leave the signature off.')
    image = account_signature(account_id)
    if not image:
        raise DataValidationError('Signature', 'Register a company signature or stamp first.', 'Open Company Information or leave the signature off.')
    return normalize_signature(image)


def signature_edit_control():
    return '''<section class="card"><h2>Signature / stamp</h2>
<label for="signature-action">Signature on this invoice</label>
<select id="signature-action" name="signature_action">
<option value="preserve">Keep the current signature</option>
<option value="use">Apply my registered company signature</option>
<option value="remove">Leave unsigned</option></select>
<p><a href="/company#company-signature" target="_blank" rel="noopener">Manage company signature / stamp</a></p>
<p>Only apply a signature you are authorized to use. Previously downloaded PDFs stay unchanged.</p></section>'''
