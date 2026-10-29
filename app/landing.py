"""Short bilingual public home; existing billing gates remain authoritative."""
import html
import json
import os
from pathlib import Path
from fastapi.responses import HTMLResponse
from app import email_delivery, public_billing
from app.seo import public_head
from app.release import APP_VERSION, BUSINESS_PHONE
from app.subscription import PLANS, plan_price_label

COPY = {
'en': {
'eyebrow':'EXPORT DOCUMENTS, TOGETHER',
'headline':'Enter once.<br>Prepare both documents.',
'intro':'Reuse company, buyer and item details to prepare your Commercial Invoice and Packing List. Review, save and download separate PDFs.',
'sample':'Try without signup','start':'Start Free','signin':'Sign in','pricing':'Plans','guide':'Getting Started','comfort':'Fictional sample · No account or card needed',
'demo':'A look inside the workspace','demo_note':'15-second product tour. See the full workflow in the guide.',
'feature1':'Reuse your details','detail1':'Save company, buyer and item information for your next document.',
'feature2':'Review changes together','detail2':'Compare an Invoice with its saved Packing List and apply changes after review.',
'feature3':'Get both PDFs','detail3':'Check actual cartons and weights, then download the latest version of each document.',
'plans_title':'Start small. Keep your work moving.',
'free_note':'5 documents per month','free_detail':'Save your own company, buyer and item details. Each Invoice and Packing List counts as one document.',
'starter_note':'Unlimited documents · First-use guidance','starter_detail':'For recurring export paperwork. Review the price, renewal and cancellation terms before subscribing.',
'starter_cta':'View Starter','renewal':'Monthly renewal until cancelled. Cancellation does not automatically refund previous payments.',
'professional':'Need team access?','contact':'Contact us','faq_title':'Before you start',
'q1':'Can I try it without signing up?','a1':'Yes. The no-signup sample uses fictional company, buyer and item details. Change the numbers and download both PDFs. Create a free account to save your own documents.',
'q2':'What happens when an Invoice changes?','a2':'A saved Packing List does not update automatically. Compare it with the Invoice and review changes before applying them. Verify cartons and weights and download fresh PDFs; previous downloads stay unchanged.',
'q3':'Does this replace customs or compliance checks?','a3':'No. This is document preparation, not customs filing or certification. Check documents against your actual shipment and the requirements for your product and destination.',
'q4':'Is payment active?','inactive':'Online payment processing is not active yet. You can still use the Free plan and contact us for help.','active':public_billing.NOTICE,
'feedback':'Send Feedback','about':'About','privacy':'Privacy','terms':'Terms','refund':'Refund Policy','help':'First-use help',
'description':'Reuse company, buyer and item details to create Commercial Invoice and Packing List PDFs. Free and monthly Starter plans.'},
'ko': {
'eyebrow':'수출 서류, 한 흐름으로','headline':'한 번 입력하고,<br>두 서류로 이어 쓰세요.',
'intro':'회사·거래처·품목 정보를 재사용해 Invoice와 Packing List를 작성하세요. 내용을 확인하고 저장한 뒤 각각 PDF로 내려받습니다.',
'sample':'가입 없이 체험','start':'무료로 시작','signin':'로그인','pricing':'요금','guide':'사용 안내','comfort':'가상 출하 체험 · 가입·카드 등록 없이',
'demo':'실제 작업 화면을 살펴보세요','demo_note':'15초 제품 소개. 자세한 작성 순서는 사용 안내에서 확인하세요.',
'feature1':'반복 입력 줄이기','detail1':'회사·거래처·품목 정보를 저장해 다음 문서에 재사용합니다.',
'feature2':'변경 내용 함께 확인','detail2':'Invoice와 저장된 Packing List를 비교하고 확인한 변경을 반영합니다.',
'feature3':'PDF 두 장으로 마무리','detail3':'실제 박스·중량을 확인하고 각 문서의 최신 PDF를 내려받습니다.',
'plans_title':'가볍게 시작하고, 필요한 만큼 작성하세요.',
'free_note':'월 5문서','free_detail':'내 회사·거래처·품목 정보를 저장하세요. Invoice와 Packing List는 각각 한 문서로 계산됩니다.',
'starter_note':'문서 무제한 · 첫 사용 안내','starter_detail':'수출 서류를 반복 작성하는 분을 위한 플랜입니다. 구독 전 요금·갱신·해지 조건을 확인하세요.',
'starter_cta':'Starter 요금·구독 보기','renewal':'해지 전까지 매월 자동갱신됩니다. 해지는 이전 결제 금액의 자동 환불을 뜻하지 않습니다.',
'professional':'팀 사용이 필요하신가요?','contact':'문의하기','faq_title':'시작 전에 확인하세요',
'q1':'가입 없이 체험할 수 있나요?','a1':'네. 가상 회사·거래처·품목으로 숫자를 바꾸고 두 PDF를 만들어 볼 수 있습니다. 내 정보를 저장하고 문서를 작성하려면 무료 계정이 필요합니다.',
'q2':'Invoice를 수정하면 어떻게 되나요?','a2':'저장된 Packing List는 자동으로 바뀌지 않습니다. Invoice와 비교하고 변경 내용을 확인한 뒤 반영하세요. 박스·중량을 검토하고 PDF를 다시 받아야 하며, 이전에 내려받은 파일은 그대로입니다.',
'q3':'통관 신고나 규정 검토도 대신해 주나요?','a3':'아니요. 서류 작성 도구이며 통관 신고·인증을 대신하지 않습니다. 실제 출하 내용과 품목·도착지별 요구사항을 확인하세요.',
'q4':'유료 구독을 할 수 있나요?','inactive':'현재 온라인 유료 결제는 열려 있지 않습니다. 무료 플랜을 이용하거나 문의해 주세요.','active':'Starter 월 구독은 Paddle에서 결제할 수 있습니다. 결제 전 최종 금액과 매월 갱신 조건을 확인하세요. Professional은 별도 문의가 필요합니다.',
'feedback':'의견 보내기','about':'서비스 소개','privacy':'개인정보 처리방침','terms':'이용약관','refund':'해지·환불','help':'첫 사용 도움',
'description':'회사·거래처·품목 정보를 재사용하는 수출 서류 작성 도구. Invoice와 Packing List PDF를 만들고 무료 또는 Starter 월 구독으로 이용하세요.'}}

def _public_base_url():
    try:
        return email_delivery.public_base_url(os.environ)
    except email_delivery.EmailConfigurationError:
        return ''

def _json_ld(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')

def landing_page(lang='en'):
    lang = 'ko' if lang == 'ko' else 'en'
    copy = COPY[lang]
    active = public_billing.enabled()
    entries = [(copy[f'q{i}'], copy[f'a{i}']) for i in range(1, 4)]
    entries.append((copy['q4'], copy['active' if active else 'inactive']))
    base_url = _public_base_url()
    canonical_url = (f'{base_url}/' if base_url else '/') + ('?lang=ko' if lang == 'ko' else '')
    hero_url = f'{base_url}/static/product-hunt/hero.png' if base_url else '/static/product-hunt/hero.png'
    structured_data = (
        {'@context':'https://schema.org','@type':'Organization','name':'Trade Paper AI','url':canonical_url,'logo':f'{base_url}/static/product-hunt/logo.png'},
        {'@context':'https://schema.org','@type':'SoftwareApplication','name':'Trade Paper AI','applicationCategory':'BusinessApplication','operatingSystem':'Web','url':canonical_url,'description':copy['description'],'softwareVersion':APP_VERSION,'offers':[
            {'@type':'Offer','name':name,'price':str(PLANS[name]['price']),'priceCurrency':PLANS[name]['currency'],'description':'Free plan with up to five documents per month.' if name == 'Free' else 'Starter plan billed monthly.'} for name in ('Free','Starter')]},
        {'@context':'https://schema.org','@type':'FAQPage','mainEntity':[{'@type':'Question','name':q,'acceptedAnswer':{'@type':'Answer','text':a}} for q,a in entries]},
    )
    values = {key: html.escape(value, quote=True) for key,value in copy.items()}
    values.update({
        'headline':copy['headline'], 'lang':lang, 'query':'?lang=ko' if lang == 'ko' else '',
        'canonical':html.escape(canonical_url,quote=True),'hero_url':html.escape(hero_url,quote=True),
        'phone':html.escape(BUSINESS_PHONE),
        'starter_price':f'월 {PLANS["Starter"]["price"]:,}원' if lang == 'ko' else html.escape(plan_price_label('Starter')),
        'free_price':'무료' if lang == 'ko' else 'Free',
        'payment_notice':html.escape(copy['active' if active else 'inactive']),
        'renewal_notice':html.escape(copy['renewal']) if active else '',
        'json_ld':''.join(f'<script type="application/ld+json">{_json_ld(item)}</script>' for item in structured_data),
        'faq_html':''.join(f'<details><summary>{html.escape(q)}</summary><p>{html.escape(a)}</p></details>' for q,a in entries),
    })
    page = Path(__file__).with_name('templates').joinpath('public_home.html').read_text(encoding='utf-8')
    for key,value in values.items():
        page = page.replace('__' + key.upper() + '__', value)
    page = page.replace('<link rel="canonical" href="' + values['canonical'] + '">', public_head('/', lang) or '<link rel="canonical" href="' + values['canonical'] + '">')
    return HTMLResponse(page)
