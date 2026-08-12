from fastapi.responses import HTMLResponse

from app.release import APP_VERSION, RELEASE_STAGE


def landing_page():
    return HTMLResponse("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
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
.workflow{display:flex;align-items:center;justify-content:center;gap:24px}
.step{display:grid;width:170px;min-height:116px;place-items:center;padding:22px;border:1px solid #e2e8f0;border-radius:22px;background:#f8fafc;text-align:center;font-size:18px;font-weight:730}
.arrow{color:#94a3b8;font-size:28px}
.section-actions{margin-top:42px}
.beta-card{max-width:780px;margin:0 auto;padding:34px;border:1px solid #bfdbfe;border-radius:26px;background:#eff6ff;text-align:center}.beta-card h2{font-size:clamp(32px,4vw,46px)}.beta-card p{margin:16px 0 0;color:#475569;font-size:18px;line-height:1.6}
.faq{display:grid;max-width:860px;margin:0 auto;gap:12px}.faq details{border:1px solid #e2e8f0;border-radius:16px;background:#fff;padding:0 22px}.faq summary{cursor:pointer;padding:20px 0;font-weight:750;list-style-position:inside}.faq details p{margin:0;padding:0 0 20px;color:#64748b;line-height:1.65}
footer{padding:54px 0;border-top:1px solid #e2e8f0}
.footer-inner{display:flex;align-items:center;justify-content:space-between;gap:20px}
.footer-brand{display:grid;gap:5px}.footer-inner strong{font-size:17px}.footer-inner span{color:#64748b}.footer-links{display:flex;justify-content:flex-end;gap:16px;flex-wrap:wrap}.footer-links a{color:#475569;font-size:14px;font-weight:650;text-decoration:none}.footer-links a:hover{text-decoration:underline}
@media(max-width:780px){
  .hero{min-height:540px;padding:72px 0 64px}
  .product-preview{padding-top:20px}
  .product-preview{padding-bottom:90px}
  .features{grid-template-columns:1fr 1fr}.value-highlights{grid-template-columns:1fr}
  .workflow{flex-direction:column}
  .arrow{transform:rotate(90deg)}
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
  .features{grid-template-columns:1fr}
  .feature{min-height:145px}
  .step{width:100%}
  .footer-inner{align-items:flex-start;flex-direction:column}.footer-links{justify-content:flex-start}
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
      <p class="eyebrow">Comfort First trade documentation</p>
      <h1>Enter once.<br>Reuse across documents.</h1>
      <p class="subtitle">Reuse Company, Buyer, and Product data. Preserve stable document snapshots. Create Unicode PDFs. Follow a shipment-guided workflow.</p>
      <div class="hero-actions"><a class="primary" href="/founding-beta">Apply for Founding Beta</a><a class="secondary" href="/register">Start Free</a><a class="secondary" href="/demo">View Demo</a></div>
      <p class="hero-comfort">Enter your information once. Reuse it across your export documents.</p>
      <div class="trust-row" aria-label="Product trust highlights"><span>✓ Unicode PDF</span><span>✓ Stable Snapshots</span><span>✓ Account Isolation</span><span>✓ Guided Workflow</span><span>✓ Founding Beta</span></div>
    </div>
  </section>
  <section class="product-preview" aria-label="Product preview">
    <div class="wrap">
      <div class="browser-frame">
        <div class="browser-bar" aria-hidden="true">
          <div class="browser-dots"><span class="browser-dot"></span><span class="browser-dot"></span><span class="browser-dot"></span></div>
          <div class="browser-address">app.tradepaper.ai/dashboard</div>
        </div>
        <img class="dashboard-screenshot" src="/static/dashboard.png" alt="Trade Paper AI dashboard showing document workflow, statistics, and quick actions">
      </div>
      <div class="value-highlights" aria-label="Product value highlights">
        <article class="value-highlight"><strong>Reusable Master Data</strong><span>Save company, buyer, and product details once, then reuse them in future trade documents.</span></article>
        <article class="value-highlight"><strong>Guided Next Steps</strong><span>Dashboard guidance and Shipment Hub recommendations show the next useful action.</span></article>
        <article class="value-highlight"><strong>Stable Snapshots</strong><span>Saved document values stay consistent even when related master data changes later.</span></article>
      </div>
    </div>
  </section>
  <section class="section" aria-labelledby="features-title">
    <div class="wrap">
      <header class="section-heading">
        <h2 id="features-title">Everything you need.</h2>
        <p>Build and manage essential export documents from one comfortable workspace.</p>
      </header>
      <div class="features">
        <article class="feature"><span class="feature-icon">C</span><h3>Company Management</h3><p>Keep exporter details ready for use across your documents.</p></article>
        <article class="feature"><span class="feature-icon">B</span><h3>Buyer Management</h3><p>Save once and reuse buyer details across future trade documents.</p></article>
        <article class="feature"><span class="feature-icon">P</span><h3>Product Management</h3><p>Reuse product names, HS Codes, origins, and unit prices.</p></article>
        <article class="feature"><span class="feature-icon">I</span><h3>Commercial Invoice</h3><p>Generate invoices using your saved company, buyer, and product data.</p></article>
        <article class="feature"><span class="feature-icon">L</span><h3>Packing List</h3><p>Carry Invoice information forward without typing everything again.</p></article>
        <article class="feature"><span class="feature-icon">S</span><h3>Shipment Hub</h3><p>See linked documents, workflow progress, health, and the recommended next step.</p></article>
        <article class="feature"><span class="feature-icon">PDF</span><h3>Unicode PDF</h3><p>Create ready-to-share PDFs with English, Korean, Japanese, and Chinese text.</p></article>
        <article class="feature"><span class="feature-icon">⌕</span><h3>Search</h3><p>Find account-owned master data and trade documents from one search.</p></article>
        <article class="feature"><span class="feature-icon">D</span><h3>Trade Documents</h3><p>Create shipping, customs, and certificate documents from the same workflow.</p></article>
      </div>
    </div>
  </section>
  <section class="section" aria-labelledby="workflow-title">
    <div class="wrap">
      <header class="section-heading">
        <h2 id="workflow-title">How It Works</h2>
        <p>Complete the required setup first, then create and export your trade documents.</p>
      </header>
      <div class="workflow" aria-label="Company, Buyer, Product, Invoice, Packing List, PDF">
        <div class="step">Company</div><span class="arrow" aria-hidden="true">→</span>
        <div class="step">Buyer</div><span class="arrow" aria-hidden="true">→</span>
        <div class="step">Product</div><span class="arrow" aria-hidden="true">→</span>
        <div class="step">Invoice</div><span class="arrow" aria-hidden="true">→</span>
        <div class="step">Packing List</div><span class="arrow" aria-hidden="true">→</span>
        <div class="step">PDF</div>
      </div>
      <div class="section-actions"><a class="primary" href="/register">Create Your First Invoice</a><a class="secondary" href="/demo">View Demo</a></div>
    </div>
  </section>
  <section class="section" aria-labelledby="beta-title">
    <div class="wrap"><div class="beta-card"><h2 id="beta-title">Founding Beta</h2><p>Trade Paper AI is currently available as a Founding Beta. Features may continue to improve during the beta period.</p></div></div>
  </section>
  <section class="section" aria-labelledby="faq-title">
    <div class="wrap">
      <header class="section-heading"><h2 id="faq-title">Frequently Asked Questions</h2><p>Clear answers about the workflow, documents, and your saved data.</p></header>
      <div class="faq">
        <details><summary>What documents are supported?</summary><p>Trade Paper AI supports Quotations, Proforma and Commercial Invoices, Packing Lists, Shipments, Shipping Instructions, Booking Confirmations, Bills of Lading, Container records, Customs Declarations, and origin, inspection, insurance, and weight certificates.</p></details>
        <details><summary>Are previous PDFs affected?</summary><p>Snapshot-enabled documents use their saved values first, so later changes to related master or upstream records do not replace the values preserved in an existing document.</p></details>
        <details><summary>Does Trade Paper AI support Unicode?</summary><p>Yes. The current PDF workflow supports mixed English, Korean, Japanese, and Chinese text across the implemented document PDFs.</p></details>
        <details><summary>Is my company data isolated?</summary><p>Yes. Authenticated business records are loaded and validated within the current account ownership scope.</p></details>
        <details><summary>What happens in Demo Mode?</summary><p>Demo Mode guides you through the real workflow with temporary form prefills. Nothing is saved until you choose Save.</p></details>
        <details><summary>Can I delete my data?</summary><p>Supported business records can be deleted when related-document integrity rules allow it. Account deletion requests are handled through the Contact page.</p></details>
      </div>
    </div>
  </section>
</main>
<footer>
  <div class="wrap footer-inner"><div class="footer-brand"><strong>Trade Paper AI</strong><span>Version __APP_VERSION__ · __RELEASE_STAGE__ · Built for Exporters.</span></div><nav class="footer-links" aria-label="Footer navigation"><a href="/founding-beta">Apply for Founding Beta</a><a href="/feedback">Send Feedback</a><a href="/demo">Demo</a><a href="/about">About</a><a href="/release-notes">Release Notes</a><a href="/version-history">Version History</a><a href="/contact">Contact</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/login">Sign In</a></nav></div>
</footer>
</body>
</html>""".replace("__APP_VERSION__", APP_VERSION).replace("__RELEASE_STAGE__", RELEASE_STAGE))
