from app.workspace_language import inject, language


def test_preference_only_accepts_supported_languages():
    assert language({}, {b'cookie':b'tp_language=ko; other=en'})=='ko'
    assert language({'lang':['en']}, {b'cookie':b'tp_language=ko'})=='en'
    assert language({'lang':['<script>']}, {})=='en'


def test_script_injection_does_not_break_existing_print_popup_script():
    source='<html lang="en"><body><script>popup.write("<html><body>PDF</body></html>")</script></body></html>'
    changed=inject(source,'ko')
    assert 'popup.write("<html><body>PDF</body></html>")</script>' in changed
    assert changed.startswith('<html lang="ko">')
    assert changed.count('src="/static/workspace-language.js"')==1
    assert changed.endswith('data-language="ko" defer></script></body></html>')
