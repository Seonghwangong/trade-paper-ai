import html
import json
import os

from fastapi.responses import HTMLResponse

from app import email_delivery
from app.release import APP_VERSION, RELEASE_STAGE
from app.subscription import PLANS, plan_price_label


FAQ_ENTRIES = (
    ("What documents are supported?", "Trade Paper AI supports Quotations, Proforma and Commercial Invoices, Packing Lists, Shipments, Shipping Instructions, Booking Confirmations, Bills of Lading, Container records, Customs Declarations, and origin, inspection, insurance, and weight certificates."),
    ("Are previous PDFs affected?", "Snapshot-enabled documents use their saved values first, so later changes to related master or upstream records do not replace the values preserved in an existing document."),
    ("Does Trade Paper AI support Unicode?", "Yes. The current PDF workflow supports mixed English, Korean, Japanese, and Chinese text across the implemented document PDFs."),
    ("Is my company data isolated?", "Yes. Authenticated business records are loaded and validated within the current account ownership scope."),
    ("What happens in Demo Mode?", "Demo Mode guides you through the real workflow with temporary form prefills. Nothing is saved until you choose Save."),
    ("Can I archive a document?", "Yes. Supported documents can be archived, restored, and excluded from active search and document packages. Permanent deletion is restricted to administrators."),
    ("Is payment active?", "No. Starter is offered at ₩29,000 per month, but online payment processing is not active yet. Apply for Founding Beta to discuss onboarding. Professional remains contact-based."),
)


def _public_base_url():
    try:
        return email_delivery.public_base_url(os.environ)
    except email_delivery.EmailConfigurationError:
        return ""


def _json_ld(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def landing_page():
    base_url = _public_base_url()
    canonical_url = f"{base_url}/" if base_url else "/"
    hero_url = f"{base_url}/static/product-hunt/hero.png" if base_url else "/static/product-hunt/hero.png"
    starter = PLANS["Starter"]
    structured_data = (
        {"@context": "https://schema.org", "@type": "Organization", "name": "Trade Paper AI", "url": canonical_url, "logo": f"{base_url}/static/product-hunt/logo.png" if base_url else "/static/product-hunt/logo.png"},
        {"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "Trade Paper AI", "applicationCategory": "BusinessApplication", "operatingSystem": "Web", "url": canonical_url, "description": "Create and manage export documents in one connected workflow.", "softwareVersion": APP_VERSION, "offers": [
            {"@type": "Offer", "name": "Free", "price": str(PLANS["Free"]["price"]), "priceCurrency": PLANS["Free"]["currency"], "description": "Free plan with up to five documents per month."},
            {"@type": "Offer", "name": "Starter", "price": str(starter["price"]), "priceCurrency": starter["currency"], "description": f"Starter plan billed {str(starter['billing_cycle']).casefold()}."},
        ]},
        {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": question, "acceptedAnswer": {"@type": "Answer", "text": answer}} for question, answer in FAQ_ENTRIES]},
    )
    json_ld = "".join(f'<script type="application/ld+json">{_json_ld(item)}</script>' for item in structured_data)
    faq_html = "".join(f"<details><summary>{html.escape(question)}</summary><p>{html.escape(answer)}</p></details>" for question, answer in FAQ_ENTRIES)
    return HTMLResponse("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Create and manage export documents in one connected workflow.">
<link rel="canonical" href="__CANONICAL_URL__">
<link rel="manifest" href="/static/site.webmanifest">
<meta name="theme-color" content="#2563eb">
<meta property="og:type" content="website">
<meta property="og:title" content="Trade Paper AI">
<meta property="og:description" content="Create and manage export documents in one connected workflow.">
<meta property="og:url" content="__CANONICAL_URL__">
<meta property="og:image" content="__HERO_URL__">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Trade Paper AI">
<meta name="twitter:description" content="Create and manage export documents in one connected workflow.">
<meta name="twitter:image" content="__HERO_URL__">
__JSON_LD__
<title>Trade Paper AI</title>
<style>
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:#fff;color:#111827;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
a{color:inherit}
.wrap{width:min(1120px,calc(100% - 40px));margin:0 auto}
.nav{display:flex;min-height:76px;align-items:center;justify-content:space-between}
.brand{font-size:19px;font-weight:750;letter-spacing:-.02em}
.nav-link{color:#475569;font-size:14px;font-weight:650;text-decoration:none}
.hero{display:grid;min-height:620px;place-items:center;padding:96px 0 88px;text-align:center}
.hero-inner{max-width:880px}
.eyebrow{margin:0 0 22px;color:#2563eb;font-size:14px;font-weight:750;letter-spacing:.08em;text-transform:uppercase}
h1{margin:0;font-size:clamp(58px,9vw,104px);font-weight:760;letter-spacing:-.065em;line-height:.94}
.subtitle{max-width:760px;margin:28px auto 34px;color:#64748b;font-size:clamp(19px,2.6vw,27px);letter-spacing:-.02em;line-height:1.45}
.primary{display:inline-flex;min-height:52px;align-items:center;justify-content:center;padding:0 27px;border-radius:999px;background:#2563eb;color:#fff;font-size:16px;font-weight:720;text-decoration:none;box-shadow:0 10px 25px rgba(37,99,235,.2);transition:transform .18s ease,background .18s ease,box-shadow .18s ease}
.primary:hover{transform:translateY(-2px);background:#1d4ed8;box-shadow:0 14px 30px rgba(37,99,235,.26)}
.secondary{display:inline-flex;min-height:52px;align-items:center;justify-content:center;padding:0 27px;border:1px solid #cbd5e1;border-radius:999px;background:#fff;color:#1e293b;font-size:16px;font-weight:720;text-decoration:none;transition:transform .18s ease,border-color .18s ease,background .18s ease}
.secondary:hover{transform:translateY(-2px);border-color:#94a3b8;background:#f8fafc}
.primary:focus-visible,.secondary:focus-visible,.nav-link:focus-visible,.footer-links a:focus-visible{outline:3px solid #93c5fd;outline-offset:4px}
.hero-actions,.section-actions{display:flex;align-items:center;justify-content:center;gap:12px;flex-wrap:wrap}
.hero-actions .primary{min-width:150px}.hero-actions .secondary{min-width:138px}
.hero-comfort{margin:24px auto 0;color:#334155;font-size:16px;font-weight:650;line-height:1.6}
.trust-row{display:flex;align-items:center;justify-content:center;gap:10px 24px;flex-wrap:wrap;margin:34px auto 0;padding-top:24px;border-top:1px solid #e2e8f0;color:#475569;font-size:14px;font-weight:700}
.trust-row span{white-space:nowrap}
.product-preview{padding:24px 0 120px}
.browser-frame{overflow:hidden;border:1px solid #dbe3ee;border-radius:26px;background:#fff;box-shadow:0 32px 80px rgba(15,23,42,.16),0 8px 24px rgba(15,23,42,.08)}
.browser-bar{display:flex;height:54px;align-items:center;gap:14px;padding:0 20px;border-bottom:1px solid #e2e8f0;background:#f8fafc}
.browser-dots{display:flex;gap:7px}
.browser-dot{width:10px;height:10px;border-radius:50%;background:#cbd5e1}
.browser-address{flex:1;max-width:520px;margin:0 auto;padding:8px 18px;border:1px solid #e2e8f0;border-radius:999px;background:#fff;color:#94a3b8;font-size:12px;text-align:center}
.dashboard-screenshot{display:block;width:100%;height:auto}
.demo-caption{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:18px;color:#64748b}.demo-caption strong{color:#0f172a}.demo-caption a{font-weight:750;color:#2563eb;text-decoration:none}
.value-highlights{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:22px}
.value-highlight{padding:22px;border:1px solid #e2e8f0;border-radius:20px;background:#f8fafc}
.value-highlight strong{display:block;margin-bottom:8px;font-size:17px}.value-highlight span{color:#64748b;line-height:1.55}
.section{padding:112px 0;border-top:1px solid #f1f5f9}
.section-heading{max-width:680px;margin:0 auto 56px;text-align:center}
.section h2{margin:0;font-size:clamp(38px,5vw,58px);letter-spacing:-.045em}
.section-heading p{margin:18px 0 0;color:#64748b;font-size:18px;line-height:1.6}
.features{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}
.feature{min-height:170px;padding:30px;border:1px solid #e2e8f0;border-radius:24px;background:#fff;box-shadow:0 14px 38px rgba(15,23,42,.055)}
.feature-icon{display:grid;width:42px;height:42px;place-items:center;border-radius:12px;background:#eff6ff;color:#2563eb;font-size:18px;font-weight:800}
.feature h3{margin:24px 0 8px;font-size:19px;letter-spacing:-.02em}.feature p{margin:0;color:#64748b;line-height:1.55}
.showcase{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.showcase-card{padding:34px;border:1px solid #e2e8f0;border-radius:26px;background:linear-gradient(145deg,#fff,#f8fafc)}.showcase-card .number{display:grid;width:42px;height:42px;place-items:center;border-radius:12px;background:#0f172a;color:#fff;font-weight:800}.showcase-card h3{margin:24px 0 10px;font-size:25px}.showcase-card p{margin:0;color:#64748b;line-height:1.65}.showcase-card a{display:inline-block;margin-top:20px;color:#2563eb;font-weight:750;text-decoration:none}
.pricing{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.price-card{display:flex;min-height:290px;flex-direction:column;padding:30px;border:1px solid #e2e8f0;border-radius:24px;background:#fff}.price-card.featured{border:2px solid #2563eb;box-shadow:0 18px 44px rgba(37,99,235,.12)}.price-card h3{margin:0;font-size:25px}.price{margin:18px 0 6px;font-size:32px;font-weight:800}.price-note{min-height:42px;color:#64748b}.price-card ul{padding-left:20px;color:#475569;line-height:1.9}.price-card a{margin-top:auto;text-align:center}
.contact-card{max-width:860px;margin:0 auto;padding:38px;border-radius:28px;background:#0f172a;color:#fff;text-align:center}.contact-card h2{font-size:clamp(32px,4vw,48px)}.contact-card p{color:#cbd5e1;font-size:18px;line-height:1.6}
.workflow{display:flex;align-items:center;justify-content:center;gap:24px}
.step{display:grid;width:170px;min-height:116px;place-items:center;padding:22px;border:1px solid #e2e8f0;border-radius:22px;background:#f8fafc;text-align:center;font-size:18px;font-weight:730}
.arrow{color:#94a3b8;font-size:28px}
.export-flow{display:grid;max-width:540px;margin:0 auto;padding:0;list-style:none}.export-flow li{position:relative;display:grid;place-items:center;min-height:60px;padding:12px 22px;border:1px solid #dbe3ee;border-radius:16px;background:#f8fafc;font-size:17px;font-weight:750}.export-flow li:not(:last-child){margin-bottom:34px}.export-flow li:not(:last-child)::after{position:absolute;top:calc(100% + 7px);content:"↓";color:#2563eb;font-size:20px}
.coming-soon{max-width:760px;margin:0 auto;padding:46px;border:1px dashed #94a3b8;border-radius:26px;background:#f8fafc;text-align:center}.coming-soon strong{display:block;font-size:28px}.coming-soon p{margin:12px 0 0;color:#64748b;line-height:1.6}
.security-list{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.security-item{padding:26px;border:1px solid #dbe3ee;border-radius:20px;background:#fff;font-size:17px;font-weight:750;text-align:center}.security-item span{display:block;margin-bottom:10px;color:#16a34a;font-size:22px}
.section-actions{margin-top:42px}
.beta-card{max-width:780px;margin:0 auto;padding:34px;border:1px solid #bfdbfe;border-radius:26px;background:#eff6ff;text-align:center}.beta-card h2{font-size:clamp(32px,4vw,46px)}.beta-card p{margin:16px 0 0;color:#475569;font-size:18px;line-height:1.6}
.faq{display:grid;max-width:860px;margin:0 auto;gap:12px}.faq details{border:1px solid #e2e8f0;border-radius:16px;background:#fff;padding:0 22px}.faq summary{cursor:pointer;padding:20px 0;font-weight:750;list-style-position:inside}.faq details p{margin:0;padding:0 0 20px;color:#64748b;line-height:1.65}
footer{padding:54px 0;border-top:1px solid #e2e8f0}
    .footer-inner{display:flex;align-items:flex-start;justify-content:space-between;gap:32px}
    .footer-brand{display:grid;min-width:0;max-width:620px;gap:5px}.footer-inner strong{font-size:17px}.footer-inner span{color:#64748b}.business-info{display:grid;gap:7px;margin:17px 0 0;color:#475569;font-size:13px;line-height:1.55}.business-info div{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:8px}.business-info dt{font-weight:750}.business-info dd{min-width:0;margin:0;overflow-wrap:anywhere;word-break:keep-all}.footer-links{display:flex;justify-content:flex-end;gap:16px;flex-wrap:wrap}.footer-links a{color:#475569;font-size:14px;font-weight:650;text-decoration:none}.footer-links a:hover{text-decoration:underline}
@media(max-width:780px){
  .hero{min-height:540px;padding:72px 0 64px}
  .product-preview{padding-top:20px}
  .product-preview{padding-bottom:90px}
  .features{grid-template-columns:1fr 1fr}.value-highlights{grid-template-columns:1fr}.showcase,.pricing{grid-template-columns:1fr}.security-list{grid-template-columns:1fr 1fr}
      .workflow{flex-direction:column}
      .arrow{transform:rotate(90deg)}
      .footer-inner{flex-direction:column}.footer-links{justify-content:flex-start}
}
@media(max-width:520px){
  .wrap{width:min(100% - 28px,1120px)}
  .nav{min-height:66px}
  .hero{min-height:500px;padding:52px 0 52px}
  .hero-actions a{width:100%}.trust-row{gap:10px 16px;margin-top:28px;padding-top:20px}
  .product-preview{padding:18px 0 72px}
  .browser-frame{border-radius:18px}
  .browser-bar{height:46px;padding:0 14px}
  .browser-address{padding:6px 10px;font-size:10px}
  .subtitle{margin-top:24px}
  .section{padding:78px 0}
  .features{grid-template-columns:1fr}.security-list{grid-template-columns:1fr}
  .feature{min-height:145px}
  .step{width:100%}
      .business-info div{grid-template-columns:1fr;gap:1px}
}
</style>
</head>
<body>
<nav class="wrap nav" aria-label="Primary navigation">
  <div class="brand">Trade Paper AI</div>
  <a class="nav-link" href="/login">Sign in</a>
</nav>
<main>
  <section class="hero">
    <div class="wrap hero-inner">
      <p class="eyebrow">Founding Beta · Export operations workspace</p>
      <h1>Create Export Documents<br>in Minutes, Not Hours.</h1>
      <p class="subtitle">Create, manage and send Commercial Invoice, Packing List,<br>Shipping Instruction, Bill of Lading and more<br>in one connected workflow.</p>
      <div class="hero-actions"><a class="primary" href="/register">Start Free</a><a class="secondary" href="#demo">Watch 15-Second Demo</a></div>
      <p class="hero-comfort">Enter your information once. Keep every export document connected.</p>
      <div class="trust-row" aria-label="Product trust highlights"><span>✓ Unicode PDF</span><span>✓ Stable Snapshots</span><span>✓ Account Isolation</span><span>✓ Guided Workflow</span><span>✓ Founding Beta</span></div>
    </div>
  </section>
  <section class="product-preview" id="demo" aria-labelledby="demo-title">
    <div class="wrap">
      <header class="section-heading"><h2 id="demo-title">Export Wizard in 15 Seconds</h2><p>See Buyer and Product data become a connected, editable export workflow.</p></header>
      <div class="browser-frame">
        <div class="browser-bar" aria-hidden="true">
          <div class="browser-dots"><span class="browser-dot"></span><span class="browser-dot"></span><span class="browser-dot"></span></div>
          <div class="browser-address">app.tradepaper.ai/dashboard</div>
        </div>
        <img class="dashboard-screenshot" src="/static/trade-paper-demo-15s.gif" alt="15-second Trade Paper AI demo showing Buyer and Product selection, Export Wizard, Shipment Tracking, and Document Package">
      </div>
      <div class="demo-caption"><span><strong>15-second product tour.</strong> See the connected export workflow at a glance.</span><a href="/demo">Open interactive demo →</a></div>
    </div>
  </section>
  <section class="section" aria-labelledby="features-title">
    <div class="wrap">
      <header class="section-heading">
        <h2 id="features-title">Why Trade Paper AI</h2>
        <p>Spend less time copying the same export information between documents.</p>
      </header>
      <div class="features">
        <article class="feature"><span class="feature-icon">✓</span><h3>No duplicate work</h3><p>Reuse Company, Buyer, and Product details instead of typing them into every document.</p></article>
        <article class="feature"><span class="feature-icon">✓</span><h3>Connected documents</h3><p>Carry document references and snapshots through one shipment workflow.</p></article>
        <article class="feature"><span class="feature-icon">✓</span><h3>Built for exporters</h3><p>Create, track, package, and send the documents used in day-to-day export operations.</p></article>
      </div>
    </div>
  </section>
  <section class="section" aria-labelledby="workflow-title">
    <div class="wrap">
      <header class="section-heading">
        <h2 id="workflow-title">One Connected Export Workflow</h2>
        <p>Move from reusable master data to completed documents and delivery.</p>
      </header>
      <ol class="export-flow" aria-label="Company, Buyer, Product, Invoice, Packing, SI, Shipment, Booking, B/L, CO, Email, Done"><li>Company</li><li>Buyer</li><li>Product</li><li>Invoice</li><li>Packing</li><li>SI</li><li>Shipment</li><li>Booking</li><li>B/L</li><li>CO</li><li>Email</li><li>Done</li></ol>
      <div class="section-actions"><a class="primary" href="/register">Start Free</a></div>
    </div>
  </section>
  <section class="section" aria-labelledby="customers-title"><div class="wrap"><header class="section-heading"><h2 id="customers-title">Customer Logos</h2></header><div class="coming-soon"><strong>Coming Soon</strong><p>We are currently welcoming our first Founding Beta companies. No customer logos are shown until we have permission to share them.</p></div></div></section>
  <section class="section" aria-labelledby="stories-title"><div class="wrap"><header class="section-heading"><h2 id="stories-title">Founding Beta Stories</h2><p>There are no customer testimonials yet. We are recruiting our first Founding Beta companies and will publish feedback only from real customers.</p></header><div class="section-actions"><a class="primary" href="/founding-beta">Join Founding Beta</a></div></div></section>
  <section class="section" aria-labelledby="security-title"><div class="wrap"><header class="section-heading"><h2 id="security-title">Security Built Into the Workspace</h2><p>Practical controls protect account-owned data and important operations.</p></header><div class="security-list"><div class="security-item"><span>✓</span>Account Isolation</div><div class="security-item"><span>✓</span>Audit Log</div><div class="security-item"><span>✓</span>Backup</div><div class="security-item"><span>✓</span>Email Security</div></div></div></section>
  <section class="section" id="pricing" aria-labelledby="pricing-title">
    <div class="wrap"><header class="section-heading"><h2 id="pricing-title">Subscription plans</h2><p>Starter is available at a published monthly price. Online payment processing is not active yet.</p></header><div class="pricing"><article class="price-card"><h3>Free</h3><div class="price">$0</div><p class="price-note">Up to 5 documents per month.</p><ul><li>Core document workflow</li><li>Account-isolated workspace</li></ul><a class="secondary" href="/register">Start Free</a></article><article class="price-card featured"><h3>Starter</h3><div class="price">__STARTER_PRICE__</div><p class="price-note">Unlimited documents on a monthly plan.</p><ul><li>Unlimited document usage</li><li>Direct onboarding</li></ul><a class="primary" href="/starter">View Starter</a></article><article class="price-card"><h3>Professional</h3><div class="price">Contact</div><p class="price-note">Professional workflow plan; contact us for availability.</p><ul><li>Professional workflow structure</li><li>Priority support during beta</li></ul><a class="secondary" href="/contact">Contact</a></article></div></div>
  </section>
  <section class="section" aria-labelledby="beta-title">
    <div class="wrap"><div class="beta-card"><h2 id="beta-title">Founding Beta</h2><p>Join the first 10 companies for six months of founding pricing, direct onboarding, and priority support.</p><div class="section-actions"><a class="primary" href="/founding-beta">Apply for Founding Beta</a></div></div></div>
  </section>
  <section class="section" id="faq" aria-labelledby="faq-title">
    <div class="wrap">
      <header class="section-heading"><h2 id="faq-title">Frequently Asked Questions</h2><p>Clear answers about the workflow, documents, and your saved data.</p></header>
      <div class="faq">__FAQ_HTML__</div>
    </div>
  </section>
  <section class="section" aria-labelledby="contact-title"><div class="wrap"><div class="contact-card"><h2 id="contact-title">Questions before you start?</h2><p>Use the deployment's configured Contact channel for product questions, Founding Beta onboarding, privacy, or account requests.</p><div class="section-actions"><a class="primary" href="/contact">Contact Trade Paper AI</a><a class="secondary" href="/founding-beta">Apply for Founding Beta</a></div></div></div></section>
  <section class="section" aria-labelledby="final-cta-title"><div class="wrap"><div class="contact-card"><h2 id="final-cta-title">Ready to simplify export documentation?</h2><p>Start your connected export workflow today.</p><div class="section-actions"><a class="primary" href="/register">Start Free</a><a class="secondary" href="/founding-beta">Join Founding Beta</a></div></div></div></section>
</main>
<footer>
      <div class="wrap footer-inner"><div class="footer-brand"><strong>Trade Paper AI</strong><span>Version __APP_VERSION__ · __RELEASE_STAGE__ · Built for Exporters.</span><dl class="business-info" aria-label="사업자 정보"><div><dt>상호</dt><dd>지엘피(GLP)</dd></div><div><dt>대표자</dt><dd>공성환</dd></div><div><dt>사업자등록번호</dt><dd>357-45-01167</dd></div><div><dt>사업장 주소</dt><dd>경상남도 창원시 의창구 지귀로120번길 19, 2층 203호(봉곡동)</dd></div><div><dt>전화번호</dt><dd>010-7166-7770</dd></div></dl></div><nav class="footer-links" aria-label="Footer navigation"><a href="/founding-beta">Apply for Founding Beta</a><a href="/feedback">Send Feedback</a><a href="/demo">Demo</a><a href="/about">About</a><a href="/release-notes">Release Notes</a><a href="/version-history">Version History</a><a href="/contact">Contact</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/login">Sign In</a></nav></div>
</footer>
<script>(function(){
  function source(){const value=((new URLSearchParams(location.search).get('utm_source')||'')+' '+(document.referrer||'')).toLowerCase();if(value.includes('producthunt')||value.includes('product hunt'))return 'Product Hunt';if(value.includes('reddit'))return 'Reddit';if(value.includes('google'))return 'Google';if(!value.trim())return 'Direct';return 'Other'}
  const seen=new Set();const observer=new IntersectionObserver(function(entries){entries.forEach(function(entry){if(!entry.isIntersecting)return;const page=entry.target.id==='pricing'?'Pricing':'FAQ';if(seen.has(page))return;seen.add(page);fetch('/analytics/visit?page='+encodeURIComponent(page)+'&source='+encodeURIComponent(source()),{method:'POST',credentials:'omit',keepalive:true}).catch(function(){})})},{threshold:.25});['pricing','faq'].forEach(function(id){const node=document.getElementById(id);if(node)observer.observe(node)})
})();</script>
</body>
</html>""".replace("__APP_VERSION__", APP_VERSION).replace("__RELEASE_STAGE__", RELEASE_STAGE).replace("__CANONICAL_URL__", html.escape(canonical_url, quote=True)).replace("__HERO_URL__", html.escape(hero_url, quote=True)).replace("__JSON_LD__", json_ld).replace("__FAQ_HTML__", faq_html).replace("__STARTER_PRICE__", html.escape(plan_price_label("Starter"))))
