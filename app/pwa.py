"""Online home-screen app support; no document caching or offline queue."""
import html
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


def app_head(source):
    """Make installation available from document pages as well as the home page."""
    tags = []
    if 'rel="manifest"' not in source:
        tags.append('<link rel="manifest" href="/static/site.webmanifest">')
    if 'rel="apple-touch-icon"' not in source:
        tags.append('<link rel="apple-touch-icon" href="/static/app-icons/tp-apple.png">')
    if 'rel="icon"' not in source:
        tags.append('<link rel="icon" type="image/svg+xml" href="/static/brand/tp-icon.svg">')
    if 'name="theme-color"' not in source:
        tags.append('<meta name="theme-color" content="#252725">')
    if 'name="apple-mobile-web-app-title"' not in source:
        tags.append('<meta name="apple-mobile-web-app-title" content="Trade Paper AI">')
    return source.replace('</head>', ''.join(tags) + '</head>', 1)


COPY = {
    'en': {
        'title': 'Your workspace, one tap away.',
        'intro': 'Add Trade Paper AI to your home screen. Open your workspace in its own window and continue with your existing account.',
        'back': 'Back to workspace', 'install': 'Install app',
        'manual': 'Choose your device below. If this browser offers installation, a button will appear here.',
        'online': 'Internet required', 'online_detail': 'Importing, saving and creating PDFs require a connection. Installation does not make documents available offline. Sign in with your existing account; you may need to sign in again after installation.',
        'iphone': 'iPhone / iPad', 'iphone_steps': '<li>Open this page in Safari.</li><li>Tap Share, then Add to Home Screen. You may need to scroll through the actions.</li><li>If shown, leave Open as Web App on, then tap Add.</li>',
        'android': 'Android', 'android_steps': '<li>Open this page in Chrome.</li><li>Use Install app above when available, or open the browser menu and choose Install app / Add to Home screen.</li><li>Confirm in the browser, then open the home-screen icon.</li>',
        'desktop': 'Computer', 'desktop_steps': '<li>In Chrome or Edge, use Install app above when available, or look for the install option in the address bar or browser menu.</li><li>In Safari on Mac, choose File → Add to Dock if available.</li>',
        'quick': 'Jump back into work', 'import': 'Import a file', 'import_detail': 'Review source details before creating your document.',
        'invoices': 'Your invoices', 'invoices_detail': 'Find saved invoices and download the latest PDF.',
        'shipments': 'Your shipments', 'shipments_detail': 'Continue preparing the documents for a shipment.',
        'hint': 'These actions require sign-in. Some browsers also show them when you press and hold the app icon.',
        'store': 'This is the web app. App Store and Google Play editions are not available yet. Installation does not change your plan or subscribe you to anything.',
        'noscript': 'Installation instructions and workspace links work without JavaScript. Use your browser menu to add the app.',
    },
    'ko': {
        'title': '한 번 눌러, 내 작업공간으로.',
        'intro': 'Trade Paper AI를 홈 화면에 추가하세요. 별도 창에서 열고, 기존 계정으로 작업을 이어갈 수 있어요.',
        'back': '작업공간으로', 'install': '앱 설치',
        'manual': '아래에서 기기별 방법을 확인하세요. 이 브라우저가 설치를 지원하면 여기에 설치 버튼이 나타납니다.',
        'online': '인터넷 연결이 필요해요', 'online_detail': '파일 가져오기·문서 저장·PDF 생성에는 인터넷 연결이 필요합니다. 설치해도 문서를 오프라인으로 사용할 수는 없어요. 기존 계정으로 로그인하세요. 설치 후 다시 로그인이 필요할 수 있습니다.',
        'iphone': 'iPhone / iPad', 'iphone_steps': '<li>Safari에서 이 페이지를 여세요.</li><li>공유 → 홈 화면에 추가를 누르세요. 동작 목록을 아래로 내려야 보일 수 있어요.</li><li>웹 앱으로 열기가 보이면 켜둔 채 추가를 누르세요.</li>',
        'android': 'Android', 'android_steps': '<li>Chrome에서 이 페이지를 여세요.</li><li>위에 앱 설치 버튼이 보이면 누르세요. 또는 브라우저 메뉴에서 앱 설치 / 홈 화면에 추가를 선택하세요.</li><li>브라우저에서 확인한 뒤 홈 화면의 아이콘으로 여세요.</li>',
        'desktop': '컴퓨터', 'desktop_steps': '<li>Chrome·Edge에서는 위의 앱 설치 버튼이나 주소창·브라우저 메뉴의 설치 항목을 이용하세요.</li><li>Mac Safari에서는 파일 → Dock에 추가가 제공되면 이용하세요.</li>',
        'quick': '바로 이어서 할 일', 'import': '파일 가져오기', 'import_detail': '원본에서 읽은 내용을 확인하고 문서 작성을 시작해요.',
        'invoices': '내 송장 보기', 'invoices_detail': '저장한 송장을 찾아 최신 PDF를 내려받아요.',
        'shipments': '내 선적 보기', 'shipments_detail': '선적별로 필요한 서류 작업을 이어가요.',
        'hint': '로그인 후 사용할 수 있어요. 지원하는 브라우저에서는 앱 아이콘을 길게 눌러 바로가기를 열 수도 있습니다.',
        'store': '이번 버전은 웹앱입니다. App Store·Google Play 버전은 아직 출시되지 않았어요. 설치만으로 요금제가 바뀌거나 유료 구독이 시작되지 않습니다.',
        'noscript': 'JavaScript 없이도 설치 안내와 작업공간 링크를 이용할 수 있어요. 설치는 브라우저 메뉴를 이용하세요.',
    },
}


@router.get('/install', response_class=HTMLResponse, include_in_schema=False)
def install_page(request: Request):
    lang = 'ko' if request.query_params.get('lang') == 'ko' else 'en'
    values = {key: value if key.endswith('_steps') else html.escape(value, quote=True)
              for key, value in COPY[lang].items()}
    values.update(lang=lang, query='?lang=' + lang)
    source = Path(__file__).with_name('templates').joinpath('install_app.html').read_text(encoding='utf-8')
    for key, value in values.items():
        source = source.replace('__' + key.upper() + '__', value)
    return HTMLResponse(app_head(source))
