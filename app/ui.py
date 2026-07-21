from __future__ import annotations

import html
import json
import re
from urllib.parse import parse_qs

from app.export import pdf_export_filename
from app.release import APP_NAME, APP_VERSION, BUILD_NAME, LAST_UPDATED, RELEASE_STAGE, RELEASE_TYPE


DESIGN_TOKENS = {
    "background": "#F3F4F6",
    "surface": "#FFFFFF",
    "primary": "#111827",
    "secondary": "#374151",
    "muted": "#6B7280",
    "border": "#E5E7EB",
    "success": "#166534",
    "success_background": "#DCFCE7",
    "warning": "#92400E",
    "warning_background": "#FEF3C7",
    "danger": "#991B1B",
    "danger_background": "#FEE2E2",
    "focus": "#2563EB",
}


def html_escape(value: object, *, attribute: bool = False) -> str:
    return html.escape(str(value or ""), quote=attribute)


def shared_css() -> str:
    return """
*{box-sizing:border-box}body{margin:0;background:#F3F4F6;color:#111827;font-family:Arial,sans-serif}
.tp-page{width:min(1180px,calc(100% - 36px));margin:40px auto}.tp-card{background:#fff;border:1px solid #E5E7EB;border-radius:16px;padding:24px}
.tp-btn{display:inline-flex;align-items:center;justify-content:center;min-height:42px;padding:10px 16px;border:0;border-radius:12px;background:#111827;color:#fff;text-decoration:none;font-weight:700;cursor:pointer}
.tp-btn-secondary{background:#E5E7EB;color:#111827}.tp-btn-danger{background:#991B1B;color:#fff}.tp-btn:focus-visible{outline:3px solid #2563EB;outline-offset:3px}
.tp-badge{display:inline-block;padding:7px 10px;border-radius:999px;font-size:13px;font-weight:700}.tp-badge-success{background:#DCFCE7;color:#166534}.tp-badge-warning{background:#FEF3C7;color:#92400E}.tp-badge-danger{background:#FEE2E2;color:#991B1B}.tp-badge-neutral{background:#E5E7EB;color:#4B5563}
.tp-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:20px}.tp-toolbar-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.tp-toolbar-count{color:#374151;font-weight:700}
.tp-search-form{display:flex;align-items:center;gap:10px;flex:1 1 360px;min-width:0}.tp-search-input{width:min(360px,100%);min-height:42px;padding:10px 13px;border:1px solid #D1D5DB;border-radius:10px;background:#fff;color:#111827;font-size:15px}.tp-search-input:focus-visible{outline:3px solid #2563EB;outline-offset:2px}
.tp-table-wrap{max-width:100%;overflow-x:auto;background:#fff;border:1px solid #E5E7EB;border-radius:16px}.tp-table{width:100%;border-collapse:collapse}.tp-table th{padding:13px 14px;background:#111827;color:#fff;text-align:left;font-size:14px}.tp-table td{padding:13px 14px;border-bottom:1px solid #E5E7EB}
.tp-empty{text-align:center;color:#6B7280;padding:32px}.tp-form-footer{display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap;margin-top:24px}
.tp-metadata{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}.tp-metadata-item{min-width:0}.tp-metadata-label{display:block;margin-bottom:7px;color:#6B7280;font-size:13px;font-weight:700}
.tp-release-footer{width:min(1180px,calc(100% - 36px));margin:34px auto 20px;padding:20px 0;border-top:1px solid #D1D5DB;color:#6B7280;text-align:center;font-size:13px;line-height:1.7}.tp-release-footer strong{display:block;color:#374151}.tp-release-version,.tp-release-build,.tp-release-date{font-size:12px}
.tp-release-footer-nav{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-top:9px}.tp-release-footer-nav a{color:#475569}
.tp-success-message{width:min(1180px,calc(100% - 36px));margin:18px auto;padding:13px 16px;border:1px solid #BBF7D0;border-radius:12px;background:#F0FDF4;color:#166534;font-weight:700}.tp-guided-empty{display:grid;gap:7px;justify-items:center;padding:10px}.tp-guided-empty strong{color:#374151}.tp-guided-empty span{color:#6B7280}.tp-guided-empty a{display:inline-block;margin-top:5px;padding:9px 13px;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:700}
@media(max-width:780px){.tp-page{width:min(100% - 28px,1180px);margin:18px auto}.tp-form-footer{align-items:stretch;flex-direction:column}.tp-form-footer .tp-btn,.tp-search-form,.tp-search-input{width:100%}.tp-search-form{flex-wrap:wrap}}
""".strip()


def form_css(*, max_width: int = 900) -> str:
    """Shared presentation rules for server-rendered document forms."""
    return shared_css() + f"""
.container{{max-width:{max_width}px;margin:40px auto;background:#fff;padding:35px;border-radius:16px}}
.container h1{{text-align:center;font-size:48px;margin-bottom:10px}}.sub{{text-align:center;color:#6B7280;margin-bottom:35px}}
.card{{border:1px solid #E5E7EB;border-radius:16px;padding:25px;margin-bottom:25px;background:#fff}}
.item-row{{border:1px solid #E5E7EB;border-radius:14px;padding:20px;margin-bottom:20px;background:#F9FAFB}}
.container input,.container select{{width:100%;padding:14px;margin-bottom:14px;border:1px solid #D1D5DB;border-radius:10px;font-size:16px;box-sizing:border-box}}
.container button{{padding:16px;background:#111827;color:#fff;border:0;border-radius:12px;font-size:18px;cursor:pointer}}
.full{{width:100%;margin-top:10px}}.small{{width:220px;margin-bottom:25px}}.add{{width:100%;background:#374151;margin-bottom:20px}}
.remove{{width:100%;background:#991B1B;margin-top:4px}}.nav-row{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:25px}}
.total{{font-size:26px;font-weight:bold;margin:25px 0}}.totals{{font-size:18px;font-weight:bold;color:#111827;margin:8px 0 20px}}
@media(max-width:780px){{.container{{margin:18px 14px;padding:22px}}.small{{width:100%}}}}
""".strip()


def button(label: object, href: str = "", kind: str = "primary", *, button_type: str = "button") -> str:
    classes = "tp-btn" + (f" tp-btn-{kind}" if kind != "primary" else "")
    safe_label = html_escape(label)
    if href:
        return f'<a class="{classes}" href="{html_escape(href, attribute=True)}">{safe_label}</a>'
    return f'<button class="{classes}" type="{html_escape(button_type, attribute=True)}">{safe_label}</button>'


def badge(label: object, kind: str = "neutral") -> str:
    return f'<span class="tp-badge tp-badge-{html_escape(kind, attribute=True)}">{html_escape(label)}</span>'


def status_badge(label: object, kind: str = "neutral") -> str:
    return badge(label, kind)


def toolbar(*items: str, count: int | None = None) -> str:
    count_html = f'<span class="tp-toolbar-count">{count}</span>' if count is not None else ""
    return f'<div class="tp-toolbar"><div class="tp-toolbar-actions">{"".join(items)}</div>{count_html}</div>'


def search_toolbar(*actions: str, action: str, value: object = "", placeholder: object = "Search", reset_url: str = "", count_label: object = "") -> str:
    search = f'<form class="tp-search-form" action="{html_escape(action, attribute=True)}" method="get"><input class="tp-search-input" type="search" name="search" value="{html_escape(value, attribute=True)}" placeholder="{html_escape(placeholder, attribute=True)}">{button("Search", button_type="submit")}{button("Reset", reset_url or action, "secondary")}</form>'
    count = badge(count_label) if count_label else ""
    return toolbar(*actions, search, count)


def table(headers: list[object] | tuple[object, ...], rows: list[list[object]] | tuple[tuple[object, ...], ...], *, empty_message: object = "No records found.") -> str:
    header_html = "".join(f'<th scope="col">{html_escape(header)}</th>' for header in headers)
    if rows:
        body_html = "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
            for row in rows
        )
    else:
        body_html = f'<tr><td class="tp-empty" colspan="{max(1, len(headers))}">{html_escape(empty_message)}</td></tr>'
    return f'<div class="tp-table-wrap"><table class="tp-table"><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table></div>'


def empty_state(message: object, action_html: str = "") -> str:
    action = f'<div>{action_html}</div>' if action_html else ""
    return f'<div class="tp-empty"><p>{html_escape(message)}</p>{action}</div>'


def page_shell(title: object, content: str, *, subtitle: object = "", navigation: str = "", styles: str = "", main_class: str = "tp-page") -> str:
    subtitle_html = f'<p>{html_escape(subtitle)}</p>' if subtitle else ""
    css = styles or shared_css()
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html_escape(title)}</title><style>{css}</style></head><body><main class="{html_escape(main_class, attribute=True)}">{navigation}<h1>{html_escape(title)}</h1>{subtitle_html}{content}</main>{release_footer()}</body></html>'


def release_footer() -> str:
    return (
        '<footer class="tp-release-footer" data-release-footer="true">'
        f'<strong>{html_escape(APP_NAME)}</strong>'
        f'<span>{html_escape(RELEASE_STAGE)}</span>'
        f'<span class="tp-release-version"> · Version {html_escape(APP_VERSION)}</span>'
        f'<span class="tp-release-build"> · Build {html_escape(BUILD_NAME)}</span>'
        f'<span class="tp-release-date"> · Release Date {html_escape(LAST_UPDATED)}</span>'
        '<span class="tp-release-copyright"> · © 2026</span>'
        '<nav class="tp-release-footer-nav" aria-label="Product information">'
        '<a href="/about">About</a><a href="/release-notes">Release Notes</a>'
        '<a href="/version-history">Version History</a><a href="/contact">Contact</a>'
        '<a href="/demo">Try Demo</a>'
        '<a href="/privacy">Privacy</a><a href="/terms">Terms</a>'
        '</nav>'
        '</footer>'
    )


def inject_release_footer(source: str) -> str:
    """Add the common footer once without changing route handlers."""
    if 'data-release-footer="true"' in source or "</body>" not in source:
        return source
    footer_styles = (
        '<style>.tp-release-footer{width:min(1180px,calc(100% - 36px));margin:34px auto 20px;'
        'padding:20px 0;border-top:1px solid #D1D5DB;color:#6B7280;text-align:center;'
        'font:13px/1.7 Arial,sans-serif}.tp-release-footer strong{display:block;color:#374151}'
        '.tp-release-version,.tp-release-build,.tp-release-date{font-size:12px}.tp-release-footer-nav{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-top:9px}.tp-release-footer-nav a{color:#475569}</style>'
    )
    return source.replace("</body>", footer_styles + release_footer() + "</body>", 1)


EMPTY_STATE_GUIDANCE = {
    "/quotation-list": ("📄", "quotations", "Quotation", "/quotation-form", "Create your first quotation."),
    "/proforma-list": ("📄", "proforma invoices", "Proforma Invoice", "/proforma-form", "Create your first proforma invoice."),
    "/invoice-list": ("📄", "invoices", "Invoice", "/invoice", "Create your first invoice."),
    "/packing-list": ("🚢", "packing lists", "Packing List", "/packing-page", "Generate one from an invoice."),
    "/shipment-list": ("🚢", "shipments", "Shipment", "/shipment-form", "Create your first shipment."),
    "/si-list": ("📄", "shipping instructions", "Shipping Instruction", "/si-form", "Create your first shipping instruction."),
    "/booking-list": ("🚢", "booking confirmations", "Booking Confirmation", "/booking-form", "Create your first booking confirmation."),
    "/container-list": ("📦", "containers", "Container", "/container-form", "Add your first container record."),
    "/bl-list": ("📄", "Bills of Lading", "Bill of Lading", "/bl-form", "Create your first Bill of Lading."),
    "/co-list": ("📄", "certificates of origin", "Certificate of Origin", "/co-form", "Create your first certificate of origin."),
    "/inspection-list": ("📄", "inspection certificates", "Inspection Certificate", "/inspection-form", "Create your first inspection certificate."),
    "/insurance-list": ("📄", "insurance certificates", "Insurance Certificate", "/insurance-form", "Create your first insurance certificate."),
    "/weight-list": ("📄", "weight certificates", "Weight Certificate", "/weight-form", "Create your first weight certificate."),
    "/customs-list": ("📄", "customs declarations", "Customs Declaration", "/customs-form", "Create your first customs declaration."),
    "/customer": ("👥", "customers", "Customer", "/customer", "Create your first customer."),
    "/buyers": ("👥", "buyers", "Buyer", "/buyer-form", "Create your first buyer to start making invoices."),
    "/products": ("📦", "products", "Product", "/product-form", "Create your first product."),
}

WORKFLOW_TIPS = (
    ("/quotation", "Next Step: Create a Proforma Invoice when the quotation is accepted."),
    ("/proforma", "Next Step: Create the Commercial Invoice from this Proforma Invoice."),
    ("/invoice", "Next Step: Create a Packing List from the saved Commercial Invoice."),
    ("/packing", "Next Step: Create the Shipping Instruction from the Packing List."),
    ("/si", "Next Step: Link the Shipping Instruction to a Shipment and prepare Booking."),
    ("/shipment", "Next Step: Follow the current recommendation shown in Shipment Hub."),
    ("/booking", "Next Step: Prepare the Bill of Lading after carrier booking is confirmed."),
    ("/container", "Next Step: Keep the Container linked to its Shipment and Packing List."),
    ("/bl", "Next Step: Complete Customs and any optional certificates needed for the shipment."),
    ("/co", "Next Step: Return to Shipment Hub to review remaining workflow records."),
    ("/inspection", "Next Step: Return to Shipment Hub to review remaining workflow records."),
    ("/insurance", "Next Step: Return to Shipment Hub to review remaining workflow records."),
    ("/weight", "Next Step: Return to Shipment Hub to review remaining workflow records."),
    ("/customs", "Next Step: Review Shipment Hub for Workflow Complete status."),
)


def _workflow_tip(path: str) -> str:
    return next((tip for prefix, tip in WORKFLOW_TIPS if path.startswith(prefix)), "")


def _help_panel(path: str) -> str:
    tip = _workflow_tip(path)
    if path == "/":
        help_text = "Use Quick Start to create a Shipment, or choose a document card to begin."
    elif path == "/search":
        help_text = "Search by document number, company, buyer, product, or shipment information."
    elif path == "/demo":
        help_text = "Choose a demo button to prefill a form. Demo values are not saved automatically."
    else:
        help_text = "Use page actions to create, view, or edit. Shortcuts: Ctrl+S Save, Alt+N New, Alt+L List."
    tip_html = f'<div class="tp-workflow-tip"><b>{html_escape(tip)}</b></div>' if tip else ""
    return (
        '<aside class="tp-context-help">'
        '<details><summary aria-label="Help">? Help</summary>'
        f'<p>{html_escape(help_text)}</p></details>{tip_html}</aside>'
    )


def _demo_script(path: str) -> str:
    demo_values = {
        "/company": {"name": "Trade Paper Demo Co.", "address": "100 Demo Trade Road, Seoul", "email": "demo@tradepaper.ai", "phone": "+82-2-0000-0000"},
        "/buyer-form": {"name": "Demo Buyer Ltd.", "address": "10 Marina Demo Avenue, Singapore", "email": "buyer@example.com", "country": "Singapore"},
        "/product-form": {"name": "Sample Export Product", "hs_code": "9999.00", "unit_price": "100", "origin": "Korea"},
        "/shipment-form": {"shipment_name": "Demo Shipment to Singapore", "customer": "Trade Paper Demo Co.", "buyer": "Demo Buyer Ltd.", "remarks": "Demo Product: Sample Export Product"},
    }.get(path, {})
    if not demo_values:
        return ""
    assignments = ",".join(
        f'{html_escape(name)}:{html_escape(value)!r}' for name, value in demo_values.items()
    )
    return f"""<script data-demo-prefill="true">
(function(){{
  const values={{{assignments}}};
  Object.keys(values).forEach(function(name){{
    const field=document.querySelector('[name="'+name+'"]')||document.getElementById(name);
    if(field&&!field.value){{field.value=values[name];field.dispatchEvent(new Event('input',{{bubbles:true}}));field.dispatchEvent(new Event('change',{{bubbles:true}}));}}
  }});
}})();
</script>"""


def _guided_empty_state(path: str) -> str:
    guidance = EMPTY_STATE_GUIDANCE.get(path)
    if not guidance:
        return ""
    icon, plural, document, create_url, description = guidance
    return (
        '<div class="tp-guided-empty">'
        f'<span class="tp-empty-icon" aria-hidden="true">{icon}</span>'
        f'<strong>No {html_escape(plural)} yet.</strong>'
        f'<span>{html_escape(description)}</span>'
        f'<a href="{html_escape(create_url, attribute=True)}">+ Add {html_escape(document)}</a>'
        '</div>'
    )


SUCCESS_EXPERIENCES = {
    "company": ("✅ Company information saved successfully.", "👉 Next: Create your first Buyer.", "/buyer-form"),
    "buyer": ("✅ Buyer created successfully.", "👉 Next: Create your first Product.", "/product-form"),
    "product": ("✅ Product created successfully.", "👉 Next: Create your first Invoice.", "/invoice"),
    "invoice": ("✅ Invoice created successfully.", "👉 Next: Generate your Packing List.", "/packing-page"),
    "packing": ("✅ Packing List created successfully.", "🎉 Congratulations! Your first export document is complete.", ""),
    "deleted": ("✅ Deleted successfully.", "", ""),
    "saved": ("✅ Saved successfully.", "", ""),
}


def _success_experience(kind: str) -> tuple[str, str, str]:
    return SUCCESS_EXPERIENCES.get(kind, SUCCESS_EXPERIENCES["saved"])


def _success_journey(kind: str) -> str:
    _, next_label, next_url = _success_experience(kind)
    if not next_label:
        return ""
    action = f'<a href="{html_escape(next_url, attribute=True)}">Continue</a>' if next_url else ""
    quick_actions = ""
    if kind == "packing":
        quick_actions = (
            '<div class="tp-success-actions"><a href="/invoice">➕ Create New Invoice</a>'
            '<a href="/packing-page">➕ Create New Packing List</a>'
            '<a href="/buyers">👥 Manage Buyers</a><a href="/products">📦 Manage Products</a></div>'
        )
    return f'<section class="tp-success-journey" role="status"><strong>{html_escape(next_label)}</strong>{action}{quick_actions}</section>'


REQUIRED_FIELDS = (
    (("/company",), ("name",)),
    (("/customer",), ("company",)),
    (("/buyer-form", "/edit-buyer"), ("name",)),
    (("/product-form", "/edit-product"), ("name",)),
    (("/quotation-form", "/edit-quotation"), ("seller", "buyer_name", "item_name")),
    (("/proforma-form", "/edit-proforma"), ("seller", "buyer", "item_name")),
    (("/invoice", "/edit-invoice"), ("seller", "buyer", "item_name")),
    (("/packing-page", "/edit-packing"), ("invoice_no", "seller", "buyer", "item_name")),
    (("/shipment-form", "/edit-shipment"), ("shipment_name",)),
    (("/si-form", "/edit-si"), ("packing_no", "shipper", "consignee")),
    (("/booking-form", "/edit-booking"), ("shipment_no", "booking_no")),
    (("/container-form", "/edit-container"), ("container_no",)),
    (("/bl-form", "/edit-bl"), ("packing_no", "shipper", "consignee")),
    (("/co-form", "/edit-co"), ("bl_no", "exporter", "consignee")),
    (("/inspection-form", "/edit-inspection"), ("bl_no", "exporter", "consignee")),
    (("/insurance-form", "/edit-insurance"), ("bl_no", "exporter", "consignee", "policy_no")),
    (("/weight-form", "/edit-weight"), ("bl_no", "exporter", "consignee")),
    (("/customs-form", "/edit-customs"), ("shipment_no", "declaration_no", "exporter", "consignee")),
)


def _required_fields(path: str) -> list[str]:
    return list(next((fields for prefixes, fields in REQUIRED_FIELDS if path.startswith(prefixes)), ()))


WORK_CONTEXTS = (
    (("/company",), "Company"),
    (("/customer",), "Customer"),
    (("/buyer-form", "/edit-buyer"), "Buyer"),
    (("/product-form", "/edit-product"), "Product"),
    (("/quotation-form", "/edit-quotation"), "Quotation"),
    (("/proforma-form", "/edit-proforma"), "Proforma Invoice"),
    (("/invoice", "/invoice-page", "/edit-invoice"), "Commercial Invoice"),
    (("/packing-page", "/edit-packing"), "Packing List"),
    (("/shipment-form", "/edit-shipment"), "Shipment"),
    (("/si-form", "/edit-si"), "Shipping Instruction"),
    (("/booking-form", "/edit-booking"), "Booking Confirmation"),
    (("/container-form", "/edit-container"), "Container Management"),
    (("/bl-form", "/edit-bl"), "Bill of Lading"),
    (("/co-form", "/edit-co"), "Certificate of Origin"),
    (("/inspection-form", "/edit-inspection"), "Inspection Certificate"),
    (("/insurance-form", "/edit-insurance"), "Insurance Certificate"),
    (("/weight-form", "/edit-weight"), "Weight Certificate"),
    (("/customs-form", "/edit-customs"), "Customs Declaration"),
)


def _work_context(path: str) -> str:
    return next(
        (
            label
            for prefixes, label in WORK_CONTEXTS
            if any(path == prefix or (prefix.startswith("/edit-") and path.startswith(prefix + "/")) for prefix in prefixes)
        ),
        "",
    )


def _ux_script(path: str) -> str:
    required_fields = json.dumps(_required_fields(path))
    work_label = json.dumps(_work_context(path))
    script = """<script data-trade-paper-ux="true">
(function(){
  function restoreSavingButtons(){
    document.querySelectorAll('[data-tp-saving="true"]').forEach(function(button){
      button.disabled=false;button.textContent=button.dataset.tpOriginalText||'Save';button.removeAttribute('data-tp-saving');
    });
    const loading=document.getElementById('tp-loading-state');if(loading)loading.classList.remove('visible');
  }
  window.tpRestoreSavingButtons=restoreSavingButtons;
  function savedFeedback(message){
    return new Promise(function(resolve){
      let notice=document.getElementById('tp-save-feedback');
      if(!notice){notice=document.createElement('div');notice.id='tp-save-feedback';notice.className='tp-save-feedback';notice.setAttribute('role','status');document.body.appendChild(notice);}
      const messages={'Company':'✅ Company information saved successfully.','Buyer':'✅ Buyer created successfully.','Product':'✅ Product created successfully.','Commercial Invoice':'✅ Invoice created successfully.','Packing List':'✅ Packing List created successfully.'};
      notice.textContent=message||messages[workLabel]||'✅ Saved successfully.';notice.classList.add('visible');setTimeout(function(){notice.classList.remove('visible');resolve();},1000);
    });
  }
  window.tpSuccessFeedback=savedFeedback;
  function showLoading(message){
    let loading=document.getElementById('tp-loading-state');
    if(!loading){loading=document.createElement('div');loading.id='tp-loading-state';loading.className='tp-loading-state';loading.setAttribute('role','status');loading.setAttribute('aria-live','polite');loading.innerHTML='<span aria-hidden="true"></span><b></b>';document.body.appendChild(loading);}
    loading.querySelector('b').textContent=message||'Loading...';loading.classList.add('visible');
  }
  function confirmDelete(label){
    return new Promise(function(resolve){
      const overlay=document.createElement('div');overlay.className='tp-confirm-overlay';
      const dialog=document.createElement('section');dialog.className='tp-confirm-dialog';dialog.setAttribute('role','alertdialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-labelledby','tp-confirm-title');
      dialog.innerHTML='<h2 id="tp-confirm-title">Delete '+label+'?</h2><p>This action cannot be undone.</p><div class="tp-confirm-actions"><button type="button" class="cancel">Cancel</button><button type="button" class="confirm">Delete</button></div>';overlay.appendChild(dialog);document.body.appendChild(overlay);
      const cancel=dialog.querySelector('.cancel');const confirm=dialog.querySelector('.confirm');function finish(value){overlay.remove();resolve(value);}cancel.addEventListener('click',function(){finish(false);});confirm.addEventListener('click',function(){finish(true);});overlay.addEventListener('click',function(event){if(event.target===overlay)finish(false);});dialog.addEventListener('keydown',function(event){if(event.key==='Escape')finish(false);});cancel.focus();
    });
  }
  const workLabel=__WORK_LABEL__;
  const lastWorkKey='trade-paper-ai-last-work';
  let formDirty=false;
  function markSaved(){formDirty=false;if(workLabel==='Commercial Invoice'&&window.tpClearInvoiceDraft)window.tpClearInvoiceDraft();try{sessionStorage.removeItem(lastWorkKey);}catch(error){}}
  window.tpMarkSaved=markSaved;
  window.tpSavedThenRedirect=async function(url,message){markSaved();await savedFeedback(message);window.location.href=url;};
  if(workLabel){try{sessionStorage.setItem(lastWorkKey,JSON.stringify({url:window.location.href,label:workLabel,updated:Date.now()}));}catch(error){}}
  const continueHost=document.getElementById('tp-continue-work');
  if(continueHost){
    try{
      const lastWork=JSON.parse(sessionStorage.getItem(lastWorkKey)||'null');
      if(lastWork&&lastWork.url&&lastWork.label&&lastWork.url!==window.location.href){
        const link=document.createElement('a');link.className='tp-continue-link';link.href=lastWork.url;link.textContent='Continue Last Work: '+lastWork.label+' →';
        const note=document.createElement('small');note.textContent='Saved in this browser session.';
        continueHost.appendChild(link);continueHost.appendChild(note);continueHost.hidden=false;
      }
    }catch(error){}
  }
  document.querySelectorAll('table').forEach(function(table){
    if(table.parentElement&&getComputedStyle(table.parentElement).overflowX==='auto')return;
    const wrapper=document.createElement('div');wrapper.className='tp-responsive-table';table.parentNode.insertBefore(wrapper,table);wrapper.appendChild(table);
  });
  function openPdfPrintWindow(viewUrl){
    const popup=window.open('','_blank');
    if(!popup)return false;
    try{
      const doc=popup.document;doc.open();doc.write('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Print PDF</title><style>*{box-sizing:border-box}html,body{height:100%;margin:0;background:#E5E7EB;font-family:Arial,sans-serif}.print-status{display:flex;height:44px;align-items:center;justify-content:space-between;gap:12px;padding:8px 14px;background:#111827;color:#fff;font-size:13px}.print-status a{color:#fff;font-weight:700}.print-frame{display:block;width:100%;height:calc(100% - 44px);border:0;background:#fff}</style></head><body><div class="print-status"><span>Preparing the PDF for printing…</span><a id="print-direct-link" target="_blank" rel="noopener">Open PDF directly</a></div><iframe id="print-pdf-frame" class="print-frame" title="PDF print preview"></iframe></body></html>');doc.close();
      const frame=doc.getElementById('print-pdf-frame');const direct=doc.getElementById('print-direct-link');direct.href=viewUrl;
      let attempted=false;const requestPrint=function(){if(attempted)return;attempted=true;popup.setTimeout(function(){try{frame.contentWindow.focus();frame.contentWindow.print();}catch(error){try{popup.focus();popup.print();}catch(ignore){}}},800);};
      frame.addEventListener('load',requestPrint,{once:true});popup.setTimeout(requestPrint,3000);frame.src=viewUrl;popup.opener=null;return true;
    }catch(error){try{popup.location.href=viewUrl;}catch(ignore){}return true;}
  }
  document.querySelectorAll('a[href]').forEach(function(link){
    if(link.closest('.tp-export-actions'))return;
    let url;try{url=new URL(link.href,window.location.href);}catch(error){return;}
    if(!/(?:-pdf\/|\/pdf(?:\/|$))/.test(url.pathname))return;
    const originalUrl=url.href;url.searchParams.set('view','1');const viewUrl=url.href;
    const actions=document.createElement('span');actions.className='tp-export-actions';actions.setAttribute('aria-label','PDF export actions');
    link.target='_blank';link.rel='noopener';link.href=viewUrl;link.classList.add('tp-export-action','open');link.textContent='Open PDF ↗';
    const download=document.createElement('a');download.className='tp-export-action download';download.href=originalUrl;download.setAttribute('download','');download.textContent='⬇ Download '+(workLabel?workLabel+' ':'')+'PDF';
    const print=document.createElement('a');print.className='tp-export-action print';print.href=viewUrl;print.target='_blank';print.rel='noopener';print.textContent='🖨 Print';print.addEventListener('click',function(event){if(openPdfPrintWindow(viewUrl))event.preventDefault();});
    const copy=document.createElement('button');copy.type='button';copy.className='tp-export-action copy';copy.textContent='📋 Copy Link';copy.addEventListener('click',async function(){try{await navigator.clipboard.writeText(originalUrl);await savedFeedback('✓ Copied successfully.');}catch(error){window.prompt('Copy PDF link',originalUrl);}});
    actions.appendChild(download);actions.appendChild(print);actions.appendChild(copy);link.insertAdjacentElement('afterend',actions);
  });
  const requiredNames=new Set(__REQUIRED_FIELDS__);
  const requiredInputs=Array.from(document.querySelectorAll('input,select,textarea')).filter(function(field){return requiredNames.has(field.name||field.id);});
  document.querySelectorAll('form[method="post"]').forEach(function(form){
    if(requiredInputs.some(function(field){return form.contains(field);})&&!form.querySelector('.tp-required-note'))form.insertAdjacentHTML('afterbegin','<p class="tp-required-note"><span>*</span> Required fields</p>');
  });
  requiredInputs.forEach(function(field){
      field.setAttribute('aria-required','true');
      const label=(field.id&&document.querySelector('label[for="'+field.id+'"]'))||field.closest('label')||(field.parentElement&&field.parentElement.querySelector(':scope > label'));
      if(label&&!label.querySelector('.tp-required-mark'))label.insertAdjacentHTML('beforeend',' <span class="tp-required-mark" aria-hidden="true">*</span>');
      else if(field.placeholder&&!field.placeholder.endsWith(' *'))field.placeholder+=' *';
  });
  if(requiredInputs.length){
    const progress=document.createElement('div');progress.className='tp-form-progress';progress.setAttribute('role','status');
    progress.innerHTML='<span class="tp-form-progress-label"></span><span class="tp-form-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100"><span></span></span>';
    const progressLabel=progress.querySelector('.tp-form-progress-label');const progressTrack=progress.querySelector('.tp-form-progress-track');const progressFill=progressTrack.firstElementChild;
    const updateProgress=function(){const completed=requiredInputs.filter(function(field){return String(field.value||'').trim();}).length;const percentage=Math.round(completed*100/requiredInputs.length);progressLabel.textContent=completed===requiredInputs.length?'Ready to save · '+completed+' / '+requiredInputs.length:'Required fields: '+completed+' / '+requiredInputs.length;progressFill.style.width=percentage+'%';progressTrack.setAttribute('aria-valuenow',String(percentage));progress.classList.toggle('complete',completed===requiredInputs.length);};
    const firstForm=document.querySelector('form[method="post"]');(firstForm||document.body).insertBefore(progress,(firstForm||document.body).firstChild);requiredInputs.forEach(function(field){field.addEventListener('input',updateProgress);field.addEventListener('change',updateProgress);});updateProgress();
  }
  function enhanceItemRow(row){
    if(row.querySelector(':scope > .tp-duplicate-item'))return;
    const duplicate=document.createElement('button');duplicate.type='button';duplicate.className='tp-duplicate-item';duplicate.textContent='Duplicate Item';
    duplicate.addEventListener('click',function(){
      const clone=row.cloneNode(true);clone.querySelectorAll('.tp-duplicate-item').forEach(function(button){button.remove();});clone.querySelectorAll('[id]').forEach(function(field){field.removeAttribute('id');});row.parentNode.insertBefore(clone,row.nextSibling);enhanceItemRow(clone);clone.querySelectorAll('input,select,textarea').forEach(function(field){field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));});
    });
    row.appendChild(duplicate);
  }
  document.querySelectorAll('.item-row').forEach(enhanceItemRow);
  const recentNames=['seller','buyer','shipper','consignee','exporter','currency','port_of_loading','port_of_discharge','place_of_delivery'];
  const recentKey='trade-paper-ai-recent-values';
  const favoriteKey='trade-paper-ai-favorite-values';
  function fieldsByName(root,name){const fields=Array.from(root.querySelectorAll('[name="'+name+'"]')).filter(function(field){return field.type!=='hidden';});const byId=document.getElementById(name);if(byId&&root.contains(byId)&&!fields.includes(byId))fields.push(byId);return fields;}
  function rememberFormValues(root){
    try{const recent={};recentNames.forEach(function(name){const field=fieldsByName(root,name)[0]||document.getElementById(name);if(field&&String(field.value||'').trim())recent[name]=field.value;});if(Object.keys(recent).length)sessionStorage.setItem(recentKey,JSON.stringify(recent));}catch(error){}rememberBuyerContext();rememberItemLibrary();
  }
  try{
    const recent=JSON.parse(sessionStorage.getItem(recentKey)||'{}');const available=recentNames.some(function(name){return recent[name]&&fieldsByName(document,name).some(function(field){return !field.value;});});
    if(available){const useRecent=document.createElement('button');useRecent.type='button';useRecent.className='tp-use-recent';useRecent.textContent='Use Recent Values';useRecent.addEventListener('click',function(){recentNames.forEach(function(name){fieldsByName(document,name).forEach(function(field){if(!field.value&&recent[name]){field.value=recent[name];field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));}});});});const form=document.querySelector('form[method="post"]');if(form)form.insertBefore(useRecent,form.firstChild);}
  }catch(error){}
  const selectableNames=recentNames.filter(function(name){return fieldsByName(document,name).length;});
  if(selectableNames.length){
    let favorites={};try{favorites=JSON.parse(localStorage.getItem(favoriteKey)||'{}')||{};}catch(error){}
    const panel=document.createElement('section');panel.className='tp-favorites';panel.setAttribute('aria-label','Favorite values');
    const heading=document.createElement('div');heading.className='tp-favorites-heading';heading.innerHTML='<strong>Favorite Values</strong><span>Choose a saved value instead of typing it again.</span>';panel.appendChild(heading);
    const choices=document.createElement('div');choices.className='tp-favorite-choices';panel.appendChild(choices);
    function fieldLabel(name){return name.split('_').map(function(part){return part.charAt(0).toUpperCase()+part.slice(1);}).join(' ');}
    function applyFavorite(name,value){fieldsByName(document,name).forEach(function(field){field.value=value;field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));});}
    function renderFavorites(){
      choices.replaceChildren();let count=0;
      selectableNames.forEach(function(name){(Array.isArray(favorites[name])?favorites[name]:[]).slice(0,20).forEach(function(value){const choice=document.createElement('button');choice.type='button';choice.className='tp-favorite-choice';choice.textContent=fieldLabel(name)+': '+value;choice.title='Use '+value;choice.addEventListener('click',function(){applyFavorite(name,value);});choices.appendChild(choice);count++;});});
      choices.hidden=count===0;
    }
    const pin=document.createElement('button');pin.type='button';pin.className='tp-pin-favorite';pin.textContent='☆ Pin Current Values';pin.addEventListener('click',function(){
      let pinned=0;selectableNames.forEach(function(name){const field=fieldsByName(document,name)[0];const value=String(field&&field.value||'').trim();if(!value)return;const values=Array.isArray(favorites[name])?favorites[name]:[];favorites[name]=[value].concat(values.filter(function(item){return String(item).toLocaleLowerCase()!==value.toLocaleLowerCase();})).slice(0,20);pinned++;});
      if(!pinned){pin.textContent='Enter a reusable value first';setTimeout(function(){pin.textContent='☆ Pin Current Values';},1400);return;}
      try{localStorage.setItem(favoriteKey,JSON.stringify(favorites));}catch(error){}renderFavorites();pin.textContent='★ Values Pinned';setTimeout(function(){pin.textContent='☆ Pin Current Values';},1200);
    });panel.appendChild(pin);renderFavorites();
    const host=document.querySelector('form[method="post"]')||document.querySelector('.container,.card,main')||document.body;host.insertBefore(panel,host.firstChild);
    let recent={};try{recent=JSON.parse(sessionStorage.getItem(recentKey)||'{}')||{};}catch(error){}
    selectableNames.forEach(function(name,index){const values=[];(favorites[name]||[]).concat(recent[name]?[recent[name]]:[]).forEach(function(value){if(value&&!values.includes(value))values.push(value);});if(!values.length)return;fieldsByName(document,name).forEach(function(field,fieldIndex){if(field.tagName!=='INPUT'||field.type==='hidden'||field.hasAttribute('list'))return;const id='tp-suggestions-'+index+'-'+fieldIndex;const list=document.createElement('datalist');list.id=id;values.forEach(function(value){const option=document.createElement('option');option.value=value;list.appendChild(option);});document.body.appendChild(list);field.setAttribute('list',id);field.setAttribute('autocomplete','off');});});
  }
  const favoriteTargets=[
    {key:'currency',names:['currency'],label:'Currency'},
    {key:'incoterms',names:['incoterms','incoterm'],label:'Incoterms'},
    {key:'payment_terms',names:['payment_terms','payment_term','terms_of_payment'],label:'Payment Terms'},
    {key:'port_of_loading',names:['port_of_loading','loading_port'],label:'Loading Port'},
    {key:'port_of_discharge',names:['port_of_discharge','destination_port','port_of_destination'],label:'Destination Port'}
  ];
  let fieldFavorites={};try{fieldFavorites=JSON.parse(localStorage.getItem(favoriteKey)||'{}')||{};}catch(error){}
  function saveFieldFavorites(){try{localStorage.setItem(favoriteKey,JSON.stringify(fieldFavorites));}catch(error){}}
  function favoriteValues(key){return Array.isArray(fieldFavorites[key])?fieldFavorites[key].slice(0,20):[];}
  function closeFavoritePickers(except){document.querySelectorAll('.tp-field-favorite.open').forEach(function(wrapper){if(wrapper!==except){wrapper.classList.remove('open');const button=wrapper.querySelector('.tp-field-star');if(button)button.setAttribute('aria-expanded','false');}});}
  favoriteTargets.forEach(function(target,targetIndex){
    const fields=[];target.names.forEach(function(name){fieldsByName(document,name).forEach(function(field){if(!fields.includes(field))fields.push(field);});});
    fields.filter(function(field){return field.type!=='hidden'&&!field.readOnly&&!field.disabled;}).forEach(function(field,fieldIndex){
      if(field.closest('.tp-field-favorite'))return;
      const wrapper=document.createElement('span');wrapper.className='tp-field-favorite';field.parentNode.insertBefore(wrapper,field);wrapper.appendChild(field);
      const star=document.createElement('button');star.type='button';star.className='tp-field-star';star.textContent='★';star.title='Save or choose a '+target.label+' favorite';star.setAttribute('aria-label','Favorite '+target.label);star.setAttribute('aria-expanded','false');wrapper.appendChild(star);
      const picker=document.createElement('div');picker.className='tp-favorite-picker';picker.id='tp-favorite-picker-'+targetIndex+'-'+fieldIndex;picker.setAttribute('role','dialog');picker.setAttribute('aria-label',target.label+' favorites');star.setAttribute('aria-controls',picker.id);wrapper.appendChild(picker);
      function renderPicker(){
        picker.replaceChildren();const values=favoriteValues(target.key);
        if(!values.length){const empty=document.createElement('p');empty.className='tp-favorite-picker-empty';empty.textContent='No favorites yet. Enter a value and tap ★.';picker.appendChild(empty);return;}
        values.forEach(function(value){const row=document.createElement('div');row.className='tp-favorite-picker-row';const choose=document.createElement('button');choose.type='button';choose.className='tp-favorite-pick';choose.textContent=value;choose.addEventListener('click',function(){field.value=value;field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));wrapper.classList.remove('open');star.setAttribute('aria-expanded','false');});const remove=document.createElement('button');remove.type='button';remove.className='tp-favorite-remove';remove.textContent='✕';remove.setAttribute('aria-label','Delete '+value);remove.addEventListener('click',function(event){event.stopPropagation();fieldFavorites[target.key]=favoriteValues(target.key).filter(function(item){return item!==value;});saveFieldFavorites();renderPicker();});row.appendChild(choose);row.appendChild(remove);picker.appendChild(row);});
      }
      star.addEventListener('click',function(event){
        event.stopPropagation();const value=String(field.value||'').trim();if(value){const values=favoriteValues(target.key);fieldFavorites[target.key]=[value].concat(values.filter(function(item){return String(item).toLocaleLowerCase()!==value.toLocaleLowerCase();})).slice(0,20);saveFieldFavorites();}
        const opening=!wrapper.classList.contains('open');closeFavoritePickers(wrapper);wrapper.classList.toggle('open',opening);star.setAttribute('aria-expanded',opening?'true':'false');if(opening)renderPicker();
      });
    });
  });
  document.addEventListener('click',function(event){if(!event.target.closest('.tp-field-favorite'))closeFavoritePickers();});
  document.addEventListener('keydown',function(event){if(event.key==='Escape')closeFavoritePickers();});
  const buyerContextKey='trade-paper-ai-buyer-context';
  let buyerContexts={};try{buyerContexts=JSON.parse(localStorage.getItem(buyerContextKey)||'{}')||{};}catch(error){}
  function contextBuyerField(){return ['[name="buyer"]','[name="buyer_name"]','#buyer','#buyer_name'].map(function(selector){return document.querySelector(selector);}).find(function(field){return field&&field.matches('input,select,textarea')&&String(field.value||'').trim();})||document.querySelector('[name="buyer"],[name="buyer_name"],#buyer,#buyer_name');}
  function contextTargetFields(target){const fields=[];target.names.forEach(function(name){fieldsByName(document,name).forEach(function(field){if(!fields.includes(field)&&field.type!=='hidden'&&!field.disabled&&!field.readOnly)fields.push(field);});});return fields;}
  function normalizedBuyer(){const field=contextBuyerField();return String(field&&field.value||'').trim().toLocaleLowerCase();}
  function rememberBuyerContext(){
    const buyerField=contextBuyerField();const buyer=String(buyerField&&buyerField.value||'').trim();if(!buyer)return;
    const key=buyer.toLocaleLowerCase();const existing=buyerContexts[key]&&typeof buyerContexts[key]==='object'?buyerContexts[key]:{values:{}};const values=Object.assign({},existing.values||{});let found=false;
    favoriteTargets.forEach(function(target){const field=contextTargetFields(target).find(function(item){return String(item.value||'').trim();});if(field){values[target.key]=String(field.value).trim();found=true;}});if(!found)return;
    buyerContexts[key]={buyer:buyer,values:values,updated:Date.now()};const ordered=Object.entries(buyerContexts).sort(function(a,b){return Number(b[1].updated||0)-Number(a[1].updated||0);}).slice(0,20);buyerContexts=Object.fromEntries(ordered);try{localStorage.setItem(buyerContextKey,JSON.stringify(buyerContexts));}catch(error){}
  }
  const contextControls=[];
  favoriteTargets.forEach(function(target){contextTargetFields(target).forEach(function(field){
    const anchor=field.closest('.tp-field-favorite')||field;const control=document.createElement('span');control.className='tp-context-control';control.hidden=true;const suggestion=document.createElement('button');suggestion.type='button';suggestion.className='tp-context-suggestion';const badge=document.createElement('span');badge.className='tp-context-badge';badge.textContent='Context';badge.hidden=true;control.appendChild(suggestion);control.appendChild(badge);anchor.insertAdjacentElement('afterend',control);
    suggestion.addEventListener('click',function(){const value=suggestion.dataset.value||'';if(!value)return;field.dataset.tpContextValue=value;field.value=value;badge.hidden=false;suggestion.hidden=true;field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));field.focus({preventScroll:true});});
    field.addEventListener('input',function(){if(field.dataset.tpContextValue&&String(field.value||'')!==field.dataset.tpContextValue){delete field.dataset.tpContextValue;badge.hidden=true;}renderContextSuggestions();});
    contextControls.push({target:target,field:field,control:control,suggestion:suggestion,badge:badge});
  });});
  function renderContextSuggestions(){
    const context=buyerContexts[normalizedBuyer()];contextControls.forEach(function(item){const value=String(context&&context.values&&context.values[item.target.key]||'').trim();const applied=item.field.dataset.tpContextValue&&String(item.field.value||'')===item.field.dataset.tpContextValue;item.badge.hidden=!applied;if(applied){item.control.hidden=false;item.suggestion.hidden=true;return;}item.suggestion.hidden=false;item.control.hidden=!value||String(item.field.value||'').trim()===value;if(value){item.suggestion.dataset.value=value;item.suggestion.textContent='Use '+value+' ';const label=document.createElement('span');label.textContent='Context';item.suggestion.appendChild(label);}});
  }
  const buyerContextField=contextBuyerField();if(buyerContextField){buyerContextField.addEventListener('input',renderContextSuggestions);buyerContextField.addEventListener('change',renderContextSuggestions);}
  document.addEventListener('change',function(event){if(contextControls.some(function(item){return item.field===event.target;})){rememberBuyerContext();renderContextSuggestions();}},true);renderContextSuggestions();
  const itemLibraryKey='trade-paper-ai-item-library';
  let itemLibrary=[];try{const storedItems=JSON.parse(localStorage.getItem(itemLibraryKey)||'[]');itemLibrary=Array.isArray(storedItems)?storedItems.slice(0,100):[];}catch(error){}
  function itemField(row,selectors){return selectors.map(function(selector){return row.querySelector(selector);}).find(Boolean)||null;}
  function itemNameField(row){return itemField(row,['[name="item_name"]','input.item','input[id^="item"]']);}
  function itemRows(){const rows=[];document.querySelectorAll('.item-row,.item-card').forEach(function(row){if(itemNameField(row)&&!rows.includes(row))rows.push(row);});document.querySelectorAll('[name="item_name"],input.item,input[id^="item"]').forEach(function(field){if(field.matches('input')){const row=field.closest('.item-row,.item-card,.card');if(row&&!rows.includes(row))rows.push(row);}});return rows;}
  function itemRecord(row){const name=itemNameField(row);const hs=itemField(row,['[name="hs_code"]','input.hs_code','input[id^="hs"]']);const unit=itemField(row,['[name="unit"]','[name="unit_name"]','input.unit','input[id^="unit"]']);const price=itemField(row,['[name="unit_price"]','input.unit_price','input.price','input[id^="price"]']);return {name:String(name&&name.value||'').trim(),hs_code:String(hs&&hs.value||'').trim(),unit:String(unit&&unit.value||'').trim(),unit_price:String(price&&price.value||'').trim(),updated:Date.now()};}
  function persistItemLibrary(){itemLibrary=itemLibrary.slice(0,100);try{localStorage.setItem(itemLibraryKey,JSON.stringify(itemLibrary));}catch(error){}}
  function rememberItemLibrary(){
    const recentItems=itemRows().map(itemRecord).filter(function(item){return item.name;});if(!recentItems.length)return;const seen=new Set();const combined=[];recentItems.concat(itemLibrary).forEach(function(item){const key=String(item.name||'').trim().toLocaleLowerCase();if(!key||seen.has(key))return;seen.add(key);combined.push(item);});itemLibrary=combined.slice(0,100);persistItemLibrary();
  }
  let currentItemRow=null;
  const libraryOverlay=document.createElement('div');libraryOverlay.className='tp-item-library-overlay';libraryOverlay.hidden=true;libraryOverlay.innerHTML='<section class="tp-item-library" role="dialog" aria-modal="true" aria-labelledby="tp-item-library-title"><div class="tp-item-library-heading"><div><strong id="tp-item-library-title">Item Library</strong><span>Insert a recent product into the current row.</span></div><button type="button" class="tp-item-library-close" aria-label="Close Item Library">✕</button></div><input class="tp-item-library-search" type="search" placeholder="Search by product name" aria-label="Search Item Library"><div class="tp-item-library-list"></div><button type="button" class="tp-item-library-clear">Clear All</button></section>';document.body.appendChild(libraryOverlay);
  const librarySearch=libraryOverlay.querySelector('.tp-item-library-search');const libraryList=libraryOverlay.querySelector('.tp-item-library-list');const libraryClear=libraryOverlay.querySelector('.tp-item-library-clear');
  function closeItemLibrary(){libraryOverlay.hidden=true;currentItemRow=null;}
  function applyItemToCurrentRow(item){if(!currentItemRow)return;const mappings=[['name',['[name="item_name"]','input.item','input[id^="item"]']],['hs_code',['[name="hs_code"]','input.hs_code','input[id^="hs"]']],['unit',['[name="unit"]','[name="unit_name"]','input.unit','input[id^="unit"]']],['unit_price',['[name="unit_price"]','input.unit_price','input.price','input[id^="price"]']]];mappings.forEach(function(mapping){const field=itemField(currentItemRow,mapping[1]);if(!field)return;field.value=String(item[mapping[0]]||'');field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));});closeItemLibrary();}
  function renderItemLibrary(){
    const query=String(librarySearch.value||'').trim().toLocaleLowerCase();libraryList.replaceChildren();const matches=itemLibrary.filter(function(item){return !query||String(item.name||'').toLocaleLowerCase().includes(query);});
    if(!matches.length){const empty=document.createElement('p');empty.className='tp-item-library-empty';empty.textContent=query?'No matching Items found.':'No recent Items yet.';libraryList.appendChild(empty);}
    matches.forEach(function(item){const row=document.createElement('div');row.className='tp-item-library-row';const insert=document.createElement('button');insert.type='button';insert.className='tp-item-library-insert';insert.innerHTML='<strong></strong><span></span>';insert.querySelector('strong').textContent=item.name;insert.querySelector('span').textContent=[item.hs_code,item.unit,item.unit_price].filter(Boolean).join(' · ')||'Product Name';insert.addEventListener('click',function(){applyItemToCurrentRow(item);});const remove=document.createElement('button');remove.type='button';remove.className='tp-item-library-delete';remove.textContent='✕';remove.setAttribute('aria-label','Delete '+item.name);remove.addEventListener('click',function(){itemLibrary=itemLibrary.filter(function(entry){return String(entry.name||'').toLocaleLowerCase()!==String(item.name||'').toLocaleLowerCase();});persistItemLibrary();renderItemLibrary();});row.appendChild(insert);row.appendChild(remove);libraryList.appendChild(row);});libraryClear.hidden=!itemLibrary.length;
  }
  function openItemLibrary(row){currentItemRow=row;librarySearch.value='';renderItemLibrary();libraryOverlay.hidden=false;setTimeout(function(){librarySearch.focus({preventScroll:true});},0);}
  function enhanceItemLibraryRow(row){if(!itemNameField(row)||row.querySelector(':scope > .tp-item-library-button'))return;const button=document.createElement('button');button.type='button';button.className='tp-item-library-button';button.textContent='Item Library';button.addEventListener('click',function(){openItemLibrary(row);});row.appendChild(button);}
  itemRows().forEach(enhanceItemLibraryRow);document.querySelectorAll('#items_area,.items-container').forEach(function(container){new MutationObserver(function(){itemRows().forEach(enhanceItemLibraryRow);}).observe(container,{childList:true,subtree:true});});
  librarySearch.addEventListener('input',renderItemLibrary);libraryOverlay.querySelector('.tp-item-library-close').addEventListener('click',closeItemLibrary);libraryOverlay.addEventListener('click',function(event){if(event.target===libraryOverlay)closeItemLibrary();});libraryClear.addEventListener('click',function(){if(!itemLibrary.length||!window.confirm('Delete all saved Items from the Item Library?'))return;itemLibrary=[];persistItemLibrary();renderItemLibrary();});document.addEventListener('keydown',function(event){if(event.key==='Escape'&&!libraryOverlay.hidden)closeItemLibrary();});
  const invoiceDraftKey='trade-paper-ai-invoice-draft';let invoiceDraftTimer=null;let invoiceDraftIndicator=null;let invoiceDraftRestoreCard=null;
  function invoiceDraftRoot(){return document.querySelector('form[method="post"]')||document.querySelector('.container,main')||document.body;}
  function invoiceDraftFields(){const excluded='.tp-favorites,.tp-templates,.tp-smart-validation,.tp-item-library-overlay,.tp-context-control,.tp-draft-card,.tp-draft-indicator';return Array.from(invoiceDraftRoot().querySelectorAll('input,select,textarea')).filter(function(field){const key=String(field.name||field.id||'').trim();return key&&field.type!=='hidden'&&field.type!=='submit'&&field.type!=='button'&&!field.readOnly&&!field.disabled&&!/^invoice_?no$/i.test(key)&&!field.closest(excluded);});}
  function captureInvoiceDraft(){const values={};invoiceDraftFields().forEach(function(field){const key=field.name||field.id;if(!values[key])values[key]=[];values[key].push(field.type==='checkbox'||field.type==='radio'?Boolean(field.checked):String(field.value||''));});return {updated:Date.now(),values:values};}
  function ensureDraftIndicator(){if(invoiceDraftIndicator)return invoiceDraftIndicator;invoiceDraftIndicator=document.createElement('div');invoiceDraftIndicator.className='tp-draft-indicator';const text=document.createElement('span');text.textContent='Draft saved locally.';const clear=document.createElement('button');clear.type='button';clear.textContent='Clear Draft';clear.addEventListener('click',function(){clearInvoiceDraft(true);});invoiceDraftIndicator.appendChild(text);invoiceDraftIndicator.appendChild(clear);invoiceDraftRoot().appendChild(invoiceDraftIndicator);return invoiceDraftIndicator;}
  function saveInvoiceDraft(){if(workLabel!=='Commercial Invoice')return;const draft=captureInvoiceDraft();try{localStorage.setItem(invoiceDraftKey,JSON.stringify(draft));ensureDraftIndicator().hidden=false;}catch(error){}}
  function scheduleInvoiceDraft(){if(workLabel!=='Commercial Invoice')return;window.clearTimeout(invoiceDraftTimer);invoiceDraftTimer=window.setTimeout(saveInvoiceDraft,1000);}
  function clearInvoiceDraft(confirmFirst){if(confirmFirst&&!window.confirm('Clear the saved Invoice Draft?'))return false;window.clearTimeout(invoiceDraftTimer);try{localStorage.removeItem(invoiceDraftKey);}catch(error){}if(invoiceDraftIndicator)invoiceDraftIndicator.hidden=true;if(invoiceDraftRestoreCard)invoiceDraftRestoreCard.remove();invoiceDraftRestoreCard=null;return true;}
  window.tpClearInvoiceDraft=function(){clearInvoiceDraft(false);};
  function restoreInvoiceDraft(draft){const offsets={};invoiceDraftFields().forEach(function(field){const key=field.name||field.id;const index=offsets[key]||0;offsets[key]=index+1;if(!draft.values||!Array.isArray(draft.values[key])||index>=draft.values[key].length)return;const value=draft.values[key][index];if(field.type==='checkbox'||field.type==='radio')field.checked=Boolean(value);else field.value=String(value??'');field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));});if(invoiceDraftRestoreCard)invoiceDraftRestoreCard.remove();invoiceDraftRestoreCard=null;ensureDraftIndicator().hidden=false;}
  function showInvoiceDraftRestore(){
    if(workLabel!=='Commercial Invoice')return;let draft=null;try{draft=JSON.parse(localStorage.getItem(invoiceDraftKey)||'null');}catch(error){}if(!draft||!draft.values)return;
    const card=document.createElement('section');card.className='tp-draft-card';card.setAttribute('aria-label','Invoice Draft Recovery');card.innerHTML='<div><strong>Continue your previous draft?</strong><span>An unfinished Invoice Draft was found in this browser.</span></div><div class="tp-draft-actions"></div>';const actions=card.querySelector('.tp-draft-actions');const restore=document.createElement('button');restore.type='button';restore.className='primary';restore.textContent='Restore';restore.addEventListener('click',function(){restoreInvoiceDraft(draft);});const startNew=document.createElement('button');startNew.type='button';startNew.textContent='Start New';startNew.addEventListener('click',function(){clearInvoiceDraft(false);});const clear=document.createElement('button');clear.type='button';clear.textContent='Clear Draft';clear.addEventListener('click',function(){clearInvoiceDraft(true);});actions.appendChild(restore);actions.appendChild(startNew);actions.appendChild(clear);invoiceDraftRestoreCard=card;invoiceDraftRoot().insertBefore(card,invoiceDraftRoot().firstChild);
  }
  if(workLabel==='Commercial Invoice'){document.addEventListener('input',scheduleInvoiceDraft,true);document.addEventListener('change',scheduleInvoiceDraft,true);window.addEventListener('load',showInvoiceDraftRestore);}
  const templateKey='trade-paper-ai-smart-templates';
  const templateFields=Array.from(document.querySelectorAll('input,select,textarea')).filter(function(field){return field.type!=='hidden'&&field.type!=='submit'&&field.type!=='button'&&!field.readOnly&&!field.disabled&&(field.name||field.id)&&!field.closest('.tp-favorites,.tp-templates');});
  if(workLabel&&templateFields.length){
    let templateStore={};try{templateStore=JSON.parse(localStorage.getItem(templateKey)||'{}')||{};}catch(error){}
    let templates=Array.isArray(templateStore[workLabel])?templateStore[workLabel].slice(0,10):[];
    const templatePanel=document.createElement('section');templatePanel.className='tp-templates';templatePanel.setAttribute('aria-label','Smart templates');
    const templateHeading=document.createElement('div');templateHeading.className='tp-template-heading';templateHeading.innerHTML='<strong>Templates</strong><span>Reuse a frequent '+workLabel+' configuration.</span>';templatePanel.appendChild(templateHeading);
    const templateActions=document.createElement('div');templateActions.className='tp-template-actions';
    const templateSelect=document.createElement('select');templateSelect.className='tp-template-select';templateSelect.setAttribute('aria-label','Select a template');
    const loadTemplate=document.createElement('button');loadTemplate.type='button';loadTemplate.className='tp-template-load';loadTemplate.textContent='Use Template';
    const deleteTemplate=document.createElement('button');deleteTemplate.type='button';deleteTemplate.className='tp-template-delete';deleteTemplate.textContent='Delete';
    templateActions.appendChild(templateSelect);templateActions.appendChild(loadTemplate);templateActions.appendChild(deleteTemplate);templatePanel.appendChild(templateActions);
    const saveTemplate=document.createElement('button');saveTemplate.type='button';saveTemplate.className='tp-template-save';saveTemplate.textContent='＋ Save as Template';templatePanel.appendChild(saveTemplate);
    function persistTemplates(){templateStore[workLabel]=templates.slice(0,10);try{localStorage.setItem(templateKey,JSON.stringify(templateStore));}catch(error){}}
    function renderTemplates(){templateSelect.replaceChildren();const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=templates.length?'Select a template':'No templates saved yet';templateSelect.appendChild(placeholder);templates.forEach(function(template,index){const option=document.createElement('option');option.value=String(index);option.textContent=template.name;templateSelect.appendChild(option);});loadTemplate.disabled=!templates.length;deleteTemplate.disabled=!templates.length;}
    function templateFieldKey(field){return field.name||field.id;}
    function captureTemplateValues(){const values={};templateFields.forEach(function(field){const key=templateFieldKey(field);if(!values[key])values[key]=[];values[key].push(field.type==='checkbox'||field.type==='radio'?Boolean(field.checked):String(field.value||''));});return values;}
    function applyTemplateValues(values){
      const offsets={};templateFields.forEach(function(field){const key=templateFieldKey(field);const index=offsets[key]||0;offsets[key]=index+1;if(!values||!Array.isArray(values[key])||index>=values[key].length)return;const value=values[key][index];if(field.type==='checkbox'||field.type==='radio')field.checked=Boolean(value);else field.value=String(value??'');field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));});
    }
    saveTemplate.addEventListener('click',function(){
      const suggested=workLabel+' Template '+(templates.length+1);const name=String(window.prompt('Template name',suggested)||'').trim();if(!name)return;
      const entry={name:name,values:captureTemplateValues()};const existing=templates.findIndex(function(template){return String(template.name).toLowerCase()===name.toLowerCase();});if(existing>=0)templates.splice(existing,1);templates.unshift(entry);templates=templates.slice(0,10);persistTemplates();renderTemplates();templateSelect.value='0';saveTemplate.textContent='✓ Template Saved';setTimeout(function(){saveTemplate.textContent='＋ Save as Template';},1200);
    });
    loadTemplate.addEventListener('click',function(){if(templateSelect.value==='')return;const index=Number(templateSelect.value);if(!Number.isInteger(index)||!templates[index])return;applyTemplateValues(templates[index].values);loadTemplate.textContent='✓ Template Applied';setTimeout(function(){loadTemplate.textContent='Use Template';},1200);});
    deleteTemplate.addEventListener('click',function(){if(templateSelect.value==='')return;const index=Number(templateSelect.value);if(!Number.isInteger(index)||!templates[index])return;if(!window.confirm('Delete template “'+templates[index].name+'”?'))return;templates.splice(index,1);persistTemplates();renderTemplates();});
    renderTemplates();const favoritePanel=document.querySelector('.tp-favorites');const templateHost=document.querySelector('form[method="post"]')||document.querySelector('.container,.card,main')||document.body;if(favoritePanel&&favoritePanel.parentNode===templateHost)favoritePanel.insertAdjacentElement('afterend',templatePanel);else templateHost.insertBefore(templatePanel,templateHost.firstChild);
  }
  const validationDefinitions=[
    {key:'buyer',label:'Buyer',selectors:['[name="buyer"]','[name="buyer_name"]','#buyer','#buyer_name']},
    {key:'currency',label:'Currency',selectors:['[name="currency"]','#currency']},
    {key:'payment_terms',label:'Payment Terms',selectors:['[name="payment_terms"]','[name="payment_term"]','[name="terms_of_payment"]','#payment_terms']},
    {key:'incoterms',label:'Incoterms',selectors:['[name="incoterms"]','[name="incoterm"]','#incoterms']},
    {key:'items',label:'At least 1 Item',selectors:['[name="item_name"]','input.item','input[id^="item"]']}
  ];
  function smartValidationFields(definition){const fields=[];definition.selectors.forEach(function(selector){document.querySelectorAll(selector).forEach(function(field){if(field.matches('input,select,textarea')&&field.type!=='hidden'&&!field.disabled&&!fields.includes(field))fields.push(field);});});return fields;}
  const activeValidationDefinitions=validationDefinitions.filter(function(definition){return smartValidationFields(definition).length;});
  if(workLabel&&activeValidationDefinitions.length){
    const validationPanel=document.createElement('section');validationPanel.className='tp-smart-validation';validationPanel.setAttribute('aria-label','Smart Validation');
    const validationHeading=document.createElement('div');validationHeading.className='tp-smart-validation-heading';validationHeading.innerHTML='<div><strong>Smart Validation</strong><span>Check key information before saving.</span></div><small aria-live="polite"></small>';validationPanel.appendChild(validationHeading);
    const validationStatuses=document.createElement('div');validationStatuses.className='tp-validation-statuses';validationPanel.appendChild(validationStatuses);
    const statusButtons=new Map();
    activeValidationDefinitions.forEach(function(definition){const status=document.createElement('button');status.type='button';status.className='tp-validation-status';status.dataset.key=definition.key;status.addEventListener('click',function(){if(status.classList.contains('complete'))return;const field=smartValidationFields(definition)[0];if(!field)return;field.scrollIntoView({behavior:'smooth',block:'center'});setTimeout(function(){field.focus({preventScroll:true});},250);});validationStatuses.appendChild(status);statusButtons.set(definition.key,status);});
    function updateSmartValidation(){let completed=0;activeValidationDefinitions.forEach(function(definition){const fields=smartValidationFields(definition);const complete=fields.some(function(field){return String(field.value||'').trim();});const status=statusButtons.get(definition.key);status.classList.toggle('complete',complete);status.classList.toggle('missing',!complete);status.innerHTML='<span aria-hidden="true">'+(complete?'✓':'⚠')+'</span><b>'+definition.label+'</b><small>'+(complete?'Complete':'Missing')+'</small>';status.setAttribute('aria-label',definition.label+': '+(complete?'Complete':'Missing. Activate to jump to the field.'));if(complete)completed++;});validationHeading.querySelector('small').textContent=completed+' / '+activeValidationDefinitions.length+' complete';validationPanel.classList.toggle('complete',completed===activeValidationDefinitions.length);}
    const validationHost=document.querySelector('form[method="post"]')||document.querySelector('.container,.card,main')||document.body;validationHost.insertBefore(validationPanel,validationHost.firstChild);
    document.addEventListener('input',updateSmartValidation,true);document.addEventListener('change',updateSmartValidation,true);
    document.querySelectorAll('#items_area,.items-container').forEach(function(container){new MutationObserver(updateSmartValidation).observe(container,{childList:true,subtree:true});});
    updateSmartValidation();
  }
  const progressExtras=[
    {key:'loading_port',label:'Loading Port',selectors:['[name="port_of_loading"]','[name="loading_port"]','#port_of_loading','#loading_port']},
    {key:'destination_port',label:'Destination Port',selectors:['[name="port_of_discharge"]','[name="destination_port"]','[name="port_of_destination"]','#port_of_discharge','#destination_port']}
  ];
  const progressDefinitions=validationDefinitions.concat(progressExtras).map(function(definition){return Object.assign({},definition,{label:definition.key==='items'?'Items':definition.label});});
  const activeProgressDefinitions=progressDefinitions.filter(function(definition){return smartValidationFields(definition).length;});
  if(workLabel==='Commercial Invoice'&&activeProgressDefinitions.length){
    const progressCard=document.createElement('section');progressCard.className='tp-smart-progress gray';progressCard.setAttribute('aria-label','Document Progress');progressCard.innerHTML='<div class="tp-smart-progress-heading"><div><strong>Document Progress</strong><span class="tp-smart-progress-message" aria-live="polite"></span></div><b class="tp-smart-progress-percent">0%</b></div><div class="tp-smart-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><span></span></div><div class="tp-smart-progress-columns"><div><h3>Completed</h3><ul class="tp-progress-completed"></ul></div><div><h3>Remaining</h3><ul class="tp-progress-remaining"></ul></div></div>';
    const progressPercent=progressCard.querySelector('.tp-smart-progress-percent');const progressMessage=progressCard.querySelector('.tp-smart-progress-message');const progressTrack=progressCard.querySelector('.tp-smart-progress-track');const progressFill=progressTrack.firstElementChild;const completedList=progressCard.querySelector('.tp-progress-completed');const remainingList=progressCard.querySelector('.tp-progress-remaining');
    function progressListItem(label,complete){const item=document.createElement('li');item.textContent=(complete?'✓ ':'• ')+label;return item;}
    function updateSmartProgress(){
      const states=activeProgressDefinitions.map(function(definition){return {label:definition.label,complete:smartValidationFields(definition).some(function(field){return String(field.value||'').trim();})};});const completed=states.filter(function(state){return state.complete;});const remaining=states.filter(function(state){return !state.complete;});const percentage=Math.round(completed.length*100/states.length);const color=percentage<=30?'gray':percentage<=70?'blue':percentage<100?'green':'success';progressCard.classList.remove('gray','blue','green','success');progressCard.classList.add(color);progressPercent.textContent=percentage+'%';progressMessage.textContent=percentage===100?'Ready to Save':remaining.length+' remaining';progressFill.style.width=percentage+'%';progressTrack.setAttribute('aria-valuenow',String(percentage));completedList.replaceChildren();remainingList.replaceChildren();if(completed.length)completed.forEach(function(state){completedList.appendChild(progressListItem(state.label,true));});else completedList.appendChild(progressListItem('None yet',false));if(remaining.length)remaining.forEach(function(state){remainingList.appendChild(progressListItem(state.label,false));});else remainingList.appendChild(progressListItem('Nothing remaining',true));
    }
    const progressHost=document.querySelector('form[method="post"]')||document.querySelector('.container,.card,main')||document.body;progressHost.insertBefore(progressCard,progressHost.firstChild);document.addEventListener('input',updateSmartProgress,true);document.addEventListener('change',updateSmartProgress,true);document.querySelectorAll('#items_area,.items-container').forEach(function(container){new MutationObserver(updateSmartProgress).observe(container,{childList:true,subtree:true});});updateSmartProgress();
  }
  const firstEditable=document.querySelector('input:not([type="hidden"]):not([readonly]):not([disabled]),select:not([disabled]),textarea:not([readonly]):not([disabled])');
  if(firstEditable)setTimeout(function(){firstEditable.focus({preventScroll:true});},0);
  document.querySelectorAll('a,button').forEach(function(control){
    const label=(control.textContent||'').trim();
    if(/^(save|update)/i.test(label)){control.classList.add('tp-ux-action','tp-ux-primary');}
    if(label==='Cancel'||label==='Return to Shipment'){control.classList.add('tp-ux-action','tp-ux-secondary');}
    if(/delete|remove/i.test(label)){control.classList.add('tp-ux-action','tp-ux-danger');}
    if(control.closest('.nav-row')&&/list$/i.test(label)&&control.tagName==='A'){control.textContent='Back to List';control.classList.add('tp-ux-action','tp-ux-secondary');}
  });
  document.querySelectorAll('a[href] > button').forEach(function(button){
    const link=button.parentElement;button.tabIndex=-1;button.setAttribute('aria-hidden','true');
    if(link&&!link.getAttribute('aria-label'))link.setAttribute('aria-label',(button.textContent||'Open').trim().replace(/^←\s*/,''));
  });
  document.addEventListener('submit',async function(event){
    const form=event.target;if(!(form instanceof HTMLFormElement))return;
    const action=(form.getAttribute('action')||'').toLowerCase();
    if(form.method.toLowerCase()==='post'&&action.includes('delete')){
      event.preventDefault();const label=workLabel||document.querySelector('h1')?.textContent?.trim()||'this record';if(await confirmDelete(label))form.submit();return;
    }
    if(form.method.toLowerCase()!=='post')return;
    event.preventDefault();
    const button=event.submitter||form.querySelector('button[type="submit"],input[type="submit"]');
    const payload=new FormData(form);
    rememberFormValues(form);
    if(button){button.dataset.tpOriginalText=button.textContent||button.value||'Save';button.dataset.tpSaving='true';button.disabled=true;if('value'in button)button.value='Saving...';else button.textContent='Saving...';}
    try{
      const response=await fetch(form.action,{method:'POST',body:payload,redirect:'follow'});
      const type=(response.headers.get('content-type')||'').toLowerCase();
      if(!response.ok){const errorBody=type.includes('text/html')?await response.text():'';restoreSavingButtons();if(errorBody){document.open();document.write(errorBody);document.close();}return;}
      if(response.redirected){const nextPage=type.includes('text/html')?await response.text():'';markSaved();await savedFeedback();if(nextPage){document.open();document.write(nextPage);document.close();}else window.location.href=response.url;return;}
      if(type.includes('text/html')){const nextPage=await response.text();markSaved();await savedFeedback();document.open();document.write(nextPage);document.close();return;}
      markSaved();await savedFeedback();restoreSavingButtons();
    }catch(error){restoreSavingButtons();window.alert('Unable to save right now. Please try again.');}
  },true);
  document.addEventListener('input',function(event){
    const target=event.target;if(!workLabel||!target||!target.matches||!target.matches('input,select,textarea'))return;
    if(target.type!=='hidden'&&!target.readOnly&&!target.disabled)formDirty=true;
  },true);
  window.addEventListener('beforeunload',function(event){if(!formDirty)return;event.preventDefault();event.returnValue='';});
  document.addEventListener('click',function(event){
    const button=event.target.closest('button');if(!button||button.form)return;
    if(!/^(save|update|create)/i.test((button.textContent||'').trim()))return;
    rememberFormValues(document);
    showLoading('Saving...');
    button.dataset.tpOriginalText=button.textContent.trim();button.dataset.tpSaving='true';setTimeout(function(){button.disabled=true;button.textContent='Saving...';},0);
  },true);
  document.addEventListener('submit',function(event){
    const form=event.target;if(!(form instanceof HTMLFormElement))return;
    if(form.method.toLowerCase()==='post'&&(form.getAttribute('action')||'').toLowerCase().includes('delete'))return;
    showLoading(form.method.toLowerCase()==='post'?'Saving...':'Loading...');
  },true);
  document.addEventListener('click',function(event){
    const link=event.target.closest('a[href]');if(!link||event.defaultPrevented||link.target==='_blank'||link.hasAttribute('download'))return;
    const href=link.getAttribute('href')||'';if(!href||href.startsWith('#')||href.startsWith('javascript:')||href.startsWith('mailto:'))return;
    showLoading('Loading...');
  });
  document.addEventListener('copy',function(){savedFeedback('✓ Copied successfully.');});
  document.addEventListener('keydown',function(event){
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){
      const form=Array.from(document.querySelectorAll('form[method="post"]')).find(function(item){return !/delete/i.test(item.action||'');});
      const saveButton=Array.from(document.querySelectorAll('button')).find(function(item){return /^(save|update|create)/i.test((item.textContent||'').trim())&&!item.disabled;});
      if(form||saveButton){event.preventDefault();if(form){if(form.requestSubmit)form.requestSubmit();else form.submit();}else saveButton.click();return;}
    }
    if(event.altKey&&event.key.toLowerCase()==='n'){
      const newLink=Array.from(document.querySelectorAll('a')).find(function(link){return /new|create first|create shipment/i.test((link.textContent||'').trim());});if(newLink){event.preventDefault();newLink.click();return;}
    }
    if(event.altKey&&event.key.toLowerCase()==='l'){
      const listLink=Array.from(document.querySelectorAll('a')).find(function(link){return /back to list|list$/i.test((link.textContent||'').trim());});if(listLink){event.preventDefault();listLink.click();return;}
    }
    if(event.key!=='Enter'||event.defaultPrevented||event.target.tagName==='TEXTAREA'||event.target.form)return;
    const button=Array.from(document.querySelectorAll('button')).find(function(item){return /^(save|update|create)/i.test((item.textContent||'').trim())&&!item.disabled;});
    if(button){event.preventDefault();button.click();}
  });
  const originalAlert=window.alert.bind(window);
  window.alert=function(message){restoreSavingButtons();originalAlert(message);};
  if(typeof window.showWorkflowError==='function'){const original=window.showWorkflowError;window.showWorkflowError=function(message){restoreSavingButtons();return original(message);};}
})();
</script>"""
    return script.replace("__REQUIRED_FIELDS__", required_fields).replace("__WORK_LABEL__", work_label)


def inject_user_experience(source: str, path: str = "", success_message: str = "", demo_mode: bool = False, empty_search: bool = False, success_kind: str = "") -> str:
    """Apply presentation-only UX behavior to an HTML response."""
    if "</body>" not in source:
        source = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Trade Paper AI</title></head><body><main class="tp-fragment-page">'
            + source
            + "</main></body></html>"
        )
    if 'name="viewport"' not in source.lower() and "</head>" in source:
        source = source.replace(
            "</head>",
            '<meta name="viewport" content="width=device-width, initial-scale=1"></head>',
            1,
        )
    empty_content = (
        '<div class="tp-guided-empty"><strong>No matching documents found.</strong><span>Try a different document number, name, or keyword.</span></div>'
        if empty_search else _guided_empty_state(path)
    )
    if empty_content:
        replacement = f'<tr><td class="tp-empty" colspan="100">{empty_content}</td></tr>'
        source, count = re.subn(r"<tbody>\s*</tbody>", f"<tbody>{replacement}</tbody>", source, count=1, flags=re.I)
        if not count:
            source, count = re.subn(
                r'(<td\b[^>]*class="[^"]*tp-empty[^"]*"[^>]*>).*?(</td>)',
                lambda match: match.group(1) + empty_content + match.group(2),
                source,
                count=1,
                flags=re.I | re.S,
            )
        if not count:
            source = re.sub(
                r"(<td\b[^>]*>)[^<]*No\s+[^<]*(?:registered|yet)[^<]*(</td>)",
                lambda match: match.group(1) + empty_content + match.group(2),
                source,
                count=1,
                flags=re.I,
            )
            count = int(empty_content in source)
        if not count and re.search(r"Total\s+[^:<]+\s*:\s*0\b", source, re.I):
            source = source.replace(
                "</table>",
                f'<tr><td class="tp-empty" colspan="100">{empty_content}</td></tr></table>',
                1,
            )
    success = (
        f'<div class="tp-success-message" role="status">{html_escape(success_message)}</div>'
        if success_message else ""
    )
    styles = '<style>html,body{max-width:100%}input,select,textarea,button{max-width:100%}.tp-responsive-table{width:100%;max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}.tp-context-help{width:min(1180px,calc(100% - 36px));margin:14px auto 0;font:13px Arial,sans-serif}.tp-context-help details{margin-left:auto;width:fit-content;max-width:100%;background:#fff;border:1px solid #D1D5DB;border-radius:10px;padding:8px 11px;color:#374151}.tp-context-help summary{cursor:pointer;font-weight:700}.tp-context-help p{max-width:460px;margin:9px 0 2px;line-height:1.5}.tp-workflow-tip{margin-top:9px;padding:10px 12px;border-left:3px solid #2563EB;border-radius:8px;background:#EFF6FF;color:#1E3A5F}.tp-save-feedback{position:fixed;z-index:9999;left:50%;top:24px;transform:translate(-50%,-16px);padding:13px 18px;border:1px solid #86EFAC;border-radius:12px;background:#F0FDF4;color:#166534;font:700 15px Arial,sans-serif;box-shadow:0 12px 28px rgba(15,23,42,.18);opacity:0;pointer-events:none;transition:opacity .15s ease,transform .15s ease}.tp-save-feedback.visible{opacity:1;transform:translate(-50%,0)}.tp-success-message{width:min(1180px,calc(100% - 36px));margin:18px auto;padding:13px 16px;border:1px solid #BBF7D0;border-radius:12px;background:#F0FDF4;color:#166534;font:700 14px Arial,sans-serif}.tp-form-progress{position:sticky;top:8px;z-index:20;display:grid;gap:6px;width:220px;max-width:100%;margin:0 0 12px auto;padding:9px 12px;border:1px solid #BFDBFE;border-radius:12px;background:#EFF6FF;color:#1E3A5F;font:700 12px Arial,sans-serif}.tp-form-progress-track{display:block;height:5px;overflow:hidden;border-radius:999px;background:#DBEAFE}.tp-form-progress-track span{display:block;height:100%;width:0;border-radius:inherit;background:#2563EB;transition:width .18s ease}.tp-form-progress.complete{border-color:#BBF7D0;background:#F0FDF4;color:#166534}.tp-form-progress.complete .tp-form-progress-track{background:#DCFCE7}.tp-form-progress.complete .tp-form-progress-track span{background:#166534}.tp-continue-link{display:inline-flex;align-items:center;min-height:42px;padding:10px 14px;border-radius:10px;background:#111827;color:#fff;text-decoration:none;font-weight:800}.tp-continue-work small{display:block;margin-top:7px;color:#64748B}.tp-favorites{display:grid;gap:10px;margin:0 0 16px;padding:14px;border:1px solid #CBD5E1;border-radius:12px;background:#F8FAFC;font:13px Arial,sans-serif}.tp-favorites-heading{display:grid;gap:3px}.tp-favorites-heading strong{color:#1E293B}.tp-favorites-heading span{color:#64748B}.tp-favorite-choices{display:flex;gap:7px;flex-wrap:wrap}.tp-favorite-choice,.tp-pin-favorite{min-height:40px;padding:8px 11px;border:1px solid #CBD5E1;border-radius:9px;background:#fff;color:#334155;font-weight:700;cursor:pointer}.tp-favorite-choice:hover,.tp-pin-favorite:hover{border-color:#94A3B8;background:#F1F5F9}.tp-pin-favorite{width:max-content;max-width:100%;color:#1E3A5F}.tp-duplicate-item,.tp-use-recent{display:inline-flex;align-items:center;justify-content:center;min-height:36px;margin:8px 0;padding:8px 12px;border:1px solid #CBD5E1!important;border-radius:9px!important;background:#F8FAFC!important;color:#334155!important;font:700 13px Arial,sans-serif!important;cursor:pointer}.tp-duplicate-item{width:auto!important}.tp-use-recent{margin-bottom:14px}.tp-guided-empty{display:grid;gap:8px;justify-items:center;padding:22px}.tp-empty-icon{font-size:30px;line-height:1}.tp-guided-empty strong{color:#374151}.tp-guided-empty span:not(.tp-empty-icon){color:#6B7280}.tp-guided-empty a{display:inline-block;margin-top:5px;padding:9px 13px;border-radius:9px;background:#111827;color:#fff;text-decoration:none;font-weight:700}.tp-ux-action{display:inline-flex;align-items:center;justify-content:center;min-height:42px;padding:10px 16px;border:0;border-radius:12px;text-decoration:none;font-weight:700;cursor:pointer}.tp-ux-primary{background:#111827!important;color:#fff!important}.tp-ux-secondary{background:#E5E7EB!important;color:#111827!important}.tp-required-note{color:#6B7280;font:13px Arial,sans-serif}.tp-required-note span,.tp-required-mark{color:#991B1B}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #2563EB;outline-offset:2px}@media(max-width:780px){.tp-ux-action{width:100%}.tp-context-help details{width:100%}.tp-form-progress{position:static;margin-left:0}.tp-favorites{padding:12px}.tp-favorite-choices{display:grid;grid-template-columns:1fr}.tp-favorite-choice,.tp-pin-favorite{width:100%;min-height:46px;text-align:left}}</style>'
    styles += '<style>.tp-templates{display:grid;gap:11px;margin:0 0 16px;padding:14px;border:1px solid #CBD5E1;border-radius:12px;background:#fff;font:13px Arial,sans-serif}.tp-template-heading{display:grid;gap:3px}.tp-template-heading strong{color:#1E293B}.tp-template-heading span{color:#64748B}.tp-template-actions{display:grid;grid-template-columns:minmax(160px,1fr) auto auto;gap:8px}.tp-template-select,.tp-template-load,.tp-template-delete,.tp-template-save{min-height:42px;border:1px solid #CBD5E1;border-radius:9px;padding:9px 12px;font-weight:700}.tp-template-select{width:100%;background:#fff;color:#1E293B}.tp-template-load{background:#111827;color:#fff;cursor:pointer}.tp-template-delete{background:#F8FAFC;color:#991B1B;cursor:pointer}.tp-template-save{width:max-content;max-width:100%;background:#EFF6FF;color:#1D4ED8;cursor:pointer}.tp-template-load:disabled,.tp-template-delete:disabled{opacity:.5;cursor:not-allowed}@media(max-width:780px){.tp-templates{padding:12px}.tp-template-actions{grid-template-columns:1fr}.tp-template-select,.tp-template-load,.tp-template-delete,.tp-template-save{width:100%;min-height:46px}}</style>'
    styles += '<style>.tp-field-favorite{position:relative;display:flex;width:100%;min-width:0;align-items:stretch}.tp-field-favorite>input,.tp-field-favorite>select,.tp-field-favorite>textarea{flex:1;min-width:0}.tp-field-star{display:inline-grid;place-items:center;width:44px;min-width:44px;min-height:42px;margin-left:6px;border:1px solid #CBD5E1;border-radius:9px;background:#FFF7ED;color:#92400E;font-size:18px;cursor:pointer}.tp-field-star:hover,.tp-field-favorite.open .tp-field-star{border-color:#F59E0B;background:#FEF3C7}.tp-favorite-picker{display:none;position:absolute;z-index:1000;top:calc(100% + 7px);right:0;width:min(320px,calc(100vw - 32px));max-height:280px;overflow:auto;padding:8px;border:1px solid #CBD5E1;border-radius:12px;background:#fff;box-shadow:0 16px 34px rgba(15,23,42,.18)}.tp-field-favorite.open .tp-favorite-picker{display:grid;gap:6px}.tp-favorite-picker-row{display:grid;grid-template-columns:minmax(0,1fr) 42px;gap:6px}.tp-favorite-pick,.tp-favorite-remove{min-height:42px;border:0;border-radius:8px;cursor:pointer}.tp-favorite-pick{overflow:hidden;padding:9px 11px;background:#F8FAFC;color:#1E293B;text-align:left;text-overflow:ellipsis;white-space:nowrap;font-weight:700}.tp-favorite-pick:hover{background:#EFF6FF}.tp-favorite-remove{background:#FEF2F2;color:#991B1B;font-weight:bold}.tp-favorite-picker-empty{margin:0;padding:12px;color:#64748B;line-height:1.45;text-align:center}@media(max-width:780px){.tp-field-star{width:46px;min-width:46px;min-height:46px}.tp-favorite-picker{left:0;right:auto;width:min(340px,calc(100vw - 32px))}.tp-favorite-pick,.tp-favorite-remove{min-height:46px}}</style>'
    styles += '<style>.tp-smart-validation{display:grid;gap:12px;margin:0 0 16px;padding:14px;border:1px solid #CBD5E1;border-radius:13px;background:#F8FAFC;font:13px Arial,sans-serif}.tp-smart-validation-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.tp-smart-validation-heading>div{display:grid;gap:3px}.tp-smart-validation-heading strong{color:#1E293B}.tp-smart-validation-heading span,.tp-smart-validation-heading>small{color:#64748B}.tp-validation-statuses{display:flex;gap:8px;flex-wrap:wrap}.tp-validation-status{display:grid;grid-template-columns:auto 1fr;column-gap:7px;align-items:center;min-height:48px;padding:8px 11px;border:1px solid #FCD34D;border-radius:10px;background:#FFFBEB;color:#78350F;text-align:left;cursor:pointer}.tp-validation-status>span{grid-row:1/3;font-size:16px}.tp-validation-status b{font-size:13px}.tp-validation-status small{font-size:11px;opacity:.8}.tp-validation-status.complete{border-color:#BBF7D0;background:#F0FDF4;color:#166534;cursor:default}.tp-smart-validation.complete{border-color:#BBF7D0;background:#F8FFFA}.tp-validation-status.missing:hover{border-color:#F59E0B;background:#FEF3C7}@media(max-width:780px){.tp-smart-validation{padding:12px}.tp-smart-validation-heading{align-items:stretch;flex-direction:column}.tp-validation-statuses{display:grid;grid-template-columns:1fr}.tp-validation-status{width:100%;min-height:50px}}</style>'
    styles += '<style>.tp-context-control{display:flex;align-items:center;gap:7px;width:100%;margin:6px 0 2px}.tp-context-suggestion{display:inline-flex;align-items:center;gap:7px;min-height:38px;padding:7px 10px;border:1px solid #BFDBFE;border-radius:9px;background:#EFF6FF;color:#1E3A5F;font-weight:700;cursor:pointer}.tp-context-suggestion span,.tp-context-badge{display:inline-flex;padding:4px 7px;border-radius:999px;background:#DBEAFE;color:#1D4ED8;font-size:11px;font-weight:800}.tp-context-suggestion:hover{border-color:#60A5FA;background:#DBEAFE}.tp-context-badge{background:#E0E7FF;color:#4338CA}.tp-context-suggestion:focus-visible{outline:3px solid #2563EB;outline-offset:2px}@media(max-width:780px){.tp-context-control{align-items:stretch;flex-direction:column}.tp-context-suggestion{width:100%;min-height:46px;justify-content:center}.tp-context-badge{width:max-content;min-height:28px;align-items:center}}</style>'
    styles += '<style>.tp-item-library-button{min-height:40px;padding:8px 12px;border:1px solid #CBD5E1;border-radius:9px;background:#F8FAFC;color:#1E3A5F;font-weight:700;cursor:pointer}.tp-item-library-button:hover{border-color:#94A3B8;background:#EFF6FF}.tp-item-library-overlay{position:fixed;z-index:10000;inset:0;display:grid;place-items:center;padding:20px;background:rgba(15,23,42,.42)}.tp-item-library-overlay[hidden]{display:none}.tp-item-library{display:grid;gap:12px;width:min(560px,100%);max-height:min(720px,calc(100vh - 40px));overflow:hidden;padding:18px;border:1px solid #CBD5E1;border-radius:16px;background:#fff;box-shadow:0 22px 48px rgba(15,23,42,.25);font:13px Arial,sans-serif}.tp-item-library-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.tp-item-library-heading>div{display:grid;gap:4px}.tp-item-library-heading strong{font-size:20px;color:#111827}.tp-item-library-heading span{color:#64748B}.tp-item-library-close{width:42px;min-width:42px;min-height:42px;border:0;border-radius:9px;background:#F1F5F9;color:#475569;cursor:pointer}.tp-item-library-search{width:100%;min-height:44px;padding:10px 12px;border:1px solid #CBD5E1;border-radius:10px;font-size:14px}.tp-item-library-list{display:grid;gap:7px;min-height:72px;overflow:auto}.tp-item-library-row{display:grid;grid-template-columns:minmax(0,1fr) 44px;gap:7px}.tp-item-library-insert,.tp-item-library-delete,.tp-item-library-clear{min-height:44px;border:0;border-radius:9px;cursor:pointer}.tp-item-library-insert{display:grid;gap:4px;padding:10px 12px;background:#F8FAFC;color:#1E293B;text-align:left}.tp-item-library-insert:hover{background:#EFF6FF}.tp-item-library-insert span{overflow:hidden;color:#64748B;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.tp-item-library-delete{background:#FEF2F2;color:#991B1B;font-weight:bold}.tp-item-library-clear{padding:10px 13px;background:#F1F5F9;color:#991B1B;font-weight:700}.tp-item-library-empty{margin:0;padding:24px;color:#64748B;text-align:center}@media(max-width:780px){.tp-item-library-button{width:100%;min-height:46px}.tp-item-library-overlay{align-items:end;padding:12px}.tp-item-library{width:100%;max-height:calc(100vh - 24px);padding:14px}.tp-item-library-close,.tp-item-library-search,.tp-item-library-insert,.tp-item-library-delete,.tp-item-library-clear{min-height:46px}}</style>'
    styles += '<style>.tp-draft-card{display:flex;align-items:center;justify-content:space-between;gap:18px;margin:0 0 16px;padding:15px;border:1px solid #BFDBFE;border-radius:13px;background:#F8FAFC;color:#1E293B;font:13px Arial,sans-serif}.tp-draft-card>div:first-child{display:grid;gap:4px}.tp-draft-card strong{font-size:16px}.tp-draft-card span{color:#64748B}.tp-draft-actions{display:flex;gap:8px;flex-wrap:wrap}.tp-draft-actions button,.tp-draft-indicator button{min-height:42px;padding:9px 12px;border:1px solid #CBD5E1;border-radius:9px;background:#E5E7EB;color:#111827;font-weight:700;cursor:pointer}.tp-draft-actions button.primary{border-color:#111827;background:#111827;color:#fff}.tp-draft-indicator{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:14px 0;padding:11px 13px;border:1px solid #BBF7D0;border-radius:11px;background:#F0FDF4;color:#166534;font:700 13px Arial,sans-serif}.tp-draft-indicator[hidden]{display:none}.tp-draft-indicator button{border-color:#86EFAC;background:#fff;color:#166534}@media(max-width:780px){.tp-draft-card{align-items:stretch;flex-direction:column}.tp-draft-actions{display:grid;grid-template-columns:1fr}.tp-draft-actions button,.tp-draft-indicator button{width:100%;min-height:46px}.tp-draft-indicator{align-items:stretch;flex-direction:column}}</style>'
    styles += '<style>.tp-smart-progress{display:grid;gap:13px;margin:0 0 16px;padding:16px;border:1px solid #D1D5DB;border-radius:14px;background:#fff;color:#1E293B;font:13px Arial,sans-serif;box-shadow:0 7px 18px rgba(15,23,42,.05)}.tp-smart-progress-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.tp-smart-progress-heading>div{display:grid;gap:4px}.tp-smart-progress-heading strong{font-size:17px}.tp-smart-progress-message{color:#64748B}.tp-smart-progress-percent{font-size:24px}.tp-smart-progress-track{height:9px;overflow:hidden;border-radius:999px;background:#E5E7EB}.tp-smart-progress-track>span{display:block;width:0;height:100%;border-radius:inherit;background:#6B7280;transition:width .3s ease,background-color .3s ease}.tp-smart-progress.blue .tp-smart-progress-track>span{background:#2563EB}.tp-smart-progress.green .tp-smart-progress-track>span{background:#15803D}.tp-smart-progress.success{border-color:#86EFAC;background:#F8FFF9}.tp-smart-progress.success .tp-smart-progress-track>span{background:#166534}.tp-smart-progress.success .tp-smart-progress-message,.tp-smart-progress.success .tp-smart-progress-percent{color:#166534;font-weight:800}.tp-smart-progress-columns{display:grid;grid-template-columns:1fr 1fr;gap:16px}.tp-smart-progress-columns>div{min-width:0;padding:12px;border-radius:10px;background:#F8FAFC}.tp-smart-progress-columns h3{margin:0 0 8px;font-size:13px}.tp-smart-progress-columns ul{display:grid;gap:5px;margin:0;padding:0;list-style:none;color:#475569}.tp-progress-completed{color:#166534!important}@media(max-width:780px){.tp-smart-progress{padding:13px}.tp-smart-progress-heading{flex-wrap:wrap}.tp-smart-progress-columns{grid-template-columns:1fr}.tp-smart-progress-columns>div{overflow-wrap:anywhere}}</style>'
    styles += '<style data-final-polish="true">:root{--tp-space:16px;--tp-radius:12px;--tp-control-height:44px;--tp-primary:#111827;--tp-secondary:#E5E7EB;--tp-danger:#991B1B;--tp-border:#D1D5DB}body{line-height:1.5}main,.container,.page{max-width:100%}.card,.tp-card,section[class$="-card"]{border-radius:var(--tp-radius)}form{min-width:0}input:not([type="checkbox"]):not([type="radio"]),select,textarea{min-height:var(--tp-control-height);padding:10px 12px;border:1px solid var(--tp-border);border-radius:10px;background:#fff;color:#111827;font:inherit;transition:border-color .15s ease,box-shadow .15s ease}input::placeholder,textarea::placeholder{color:#9CA3AF}input:hover,select:hover,textarea:hover{border-color:#94A3B8}button,.tp-btn,.table-link,.document-button,.action-link{min-height:var(--tp-control-height);border-radius:10px;transition:transform .15s ease,box-shadow .15s ease,background-color .15s ease,opacity .15s ease}button:hover:not(:disabled),.tp-btn:hover,.table-link:hover,.document-button:hover,.action-link:hover{transform:translateY(-1px);box-shadow:0 7px 16px rgba(15,23,42,.12)}button:disabled,.tp-btn[aria-disabled="true"]{opacity:.5;cursor:not-allowed;box-shadow:none;transform:none}.tp-ux-primary{background:var(--tp-primary)!important}.tp-ux-secondary{background:var(--tp-secondary)!important}.tp-ux-danger,button.danger,.danger button{background:var(--tp-danger)!important;color:#fff!important}table th{height:48px;vertical-align:middle}table td{height:48px;vertical-align:middle}table tbody tr{transition:background-color .12s ease}table tbody tr:hover{background:#F8FAFC}.tp-empty,.empty{padding:32px!important;color:#64748B;text-align:center}.tp-success-message,.tp-save-feedback{border-radius:var(--tp-radius)}.workflow-error,[role="alert"],.error-message{border:1px solid #FECACA;border-radius:var(--tp-radius);background:#FEF2F2;color:#991B1B}.tp-loading-state{position:fixed;z-index:10020;right:20px;bottom:20px;display:flex;align-items:center;gap:10px;padding:12px 16px;border:1px solid #CBD5E1;border-radius:12px;background:#fff;color:#334155;box-shadow:0 12px 28px rgba(15,23,42,.18);font:700 14px Arial,sans-serif;opacity:0;pointer-events:none;transform:translateY(10px);transition:opacity .15s ease,transform .15s ease}.tp-loading-state.visible{opacity:1;transform:translateY(0)}.tp-loading-state span{width:16px;height:16px;border:2px solid #CBD5E1;border-top-color:#2563EB;border-radius:999px;animation:tp-spin .7s linear infinite}@keyframes tp-spin{to{transform:rotate(360deg)}}@media(max-width:780px){.tp-loading-state{right:12px;bottom:12px;left:12px;justify-content:center}button,.tp-btn,input:not([type="checkbox"]):not([type="radio"]),select{min-height:46px}}</style>'
    styles += '<style data-export-share="true">.tp-export-actions{display:inline-flex;align-items:center;gap:8px;flex-wrap:wrap;margin:6px}.tp-export-action{display:inline-flex;min-height:42px;align-items:center;justify-content:center;padding:9px 12px;border:1px solid #CBD5E1;border-radius:10px;background:#fff;color:#1E293B;text-decoration:none;font:700 13px Arial,sans-serif;cursor:pointer}.tp-export-action.open,.tp-export-action.download{background:#111827;color:#fff;border-color:#111827}.tp-export-action:hover{transform:translateY(-1px);box-shadow:0 7px 16px rgba(15,23,42,.12)}.tp-export-action:focus-visible{outline:3px solid #2563EB;outline-offset:2px}@media(max-width:780px){.tp-export-actions{display:grid;width:100%;grid-template-columns:1fr}.tp-export-action{width:100%;min-height:46px}}</style>'
    styles += '<style data-rc-polish="true">body{font-size:15px}button,a[class*="btn"],a[class*="button"]{font-family:Arial,sans-serif}a[href]>button{pointer-events:none}.card,.tp-card,.mini,.doc-card{max-width:100%}.nav,.nav-row,.toolbar,.actions,.document-actions{gap:12px}table{font-size:14px}.tp-responsive-table{border-radius:12px}.tp-responsive-table table{margin:0}.tp-export-actions{max-width:100%}@media(max-width:780px){.nav,.nav-row,.toolbar,.actions,.document-actions{align-items:stretch}.nav>a,.nav-row>a,.toolbar>a,.actions>a{min-height:46px}.tp-responsive-table table{min-width:720px}}</style>'
    styles += '<style data-first-user-success="true">.tp-success-journey{display:flex;align-items:center;justify-content:center;gap:14px;flex-wrap:wrap;width:min(900px,calc(100% - 36px));margin:16px auto;padding:16px;border:1px solid #BBF7D0;border-radius:14px;background:#F0FDF4;color:#166534;text-align:center}.tp-success-journey>a,.tp-success-actions a{display:inline-flex;min-height:42px;align-items:center;justify-content:center;padding:9px 13px;border-radius:10px;background:#111827;color:#fff;text-decoration:none;font-weight:700}.tp-success-actions{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;width:100%;margin-top:5px}.tp-confirm-overlay{position:fixed;z-index:11000;inset:0;display:grid;place-items:center;padding:20px;background:rgba(15,23,42,.5)}.tp-confirm-dialog{width:min(440px,100%);padding:24px;border-radius:16px;background:#fff;color:#111827;box-shadow:0 22px 48px rgba(15,23,42,.28);text-align:center}.tp-confirm-dialog h2{margin:0 0 9px}.tp-confirm-dialog p{margin:0;color:#64748B}.tp-confirm-actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:22px}.tp-confirm-actions button{min-height:44px;border:0;border-radius:10px;font-weight:700;cursor:pointer}.tp-confirm-actions .cancel{background:#E5E7EB;color:#111827}.tp-confirm-actions .confirm{background:#991B1B;color:#fff}@media(max-width:640px){.tp-success-journey{align-items:stretch;flex-direction:column}.tp-success-journey>a,.tp-success-actions,.tp-success-actions a{width:100%}.tp-success-actions{display:grid;grid-template-columns:1fr}.tp-confirm-actions{grid-template-columns:1fr}.tp-confirm-actions button{min-height:46px}}</style>'
    if success:
        source = source.replace("<body>", "<body>" + success, 1)
    journey = _success_journey(success_kind) if success_kind else ""
    if journey:
        source = source.replace("<body>", "<body>" + journey, 1)
    source = source.replace("<body>", "<body>" + _help_panel(path), 1)
    if 'data-trade-paper-ux="true"' not in source:
        demo_script = _demo_script(path) if demo_mode else ""
        source = source.replace("</body>", styles + _ux_script(path) + demo_script + "</body>", 1)
    return source


class ReleaseFooterMiddleware:
    """Presentation-only ASGI middleware for consistent HTML footers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        start_message = None
        body_parts = []
        request_method = str(scope.get("method", "GET") or "GET").upper()
        request_path = str(scope.get("path", "") or "")
        request_headers = {key.lower(): value for key, value in scope.get("headers", [])}
        success_cookie = request_headers.get(b"cookie", b"")
        success_kind = next((kind for kind in SUCCESS_EXPERIENCES if f"tp_success={kind}".encode() in success_cookie), "")
        has_success_cookie = bool(success_kind)
        mutation_kind = ""
        if request_method == "POST":
            if "delete" in request_path or "packing-delete" in request_path:
                mutation_kind = "deleted"
            elif request_path == "/save-company":
                mutation_kind = "company"
            elif request_path == "/save-buyer":
                mutation_kind = "buyer"
            elif request_path == "/save-product":
                mutation_kind = "product"
            elif request_path in {"/invoice", "/save-invoice"}:
                mutation_kind = "invoice"
            elif request_path in {"/packing", "/packing-list"}:
                mutation_kind = "packing"
        query = parse_qs(scope.get("query_string", b"").decode("utf-8", "ignore"))
        demo_mode = query.get("demo", [""])[0] == "1"
        empty_search = bool(str(query.get("search", query.get("q", [""]))[0] or "").strip())

        async def send_with_footer(message):
            nonlocal start_message
            if message["type"] == "http.response.start":
                start_message = message
                return
            if message["type"] != "http.response.body" or start_message is None:
                await send(message)
                return

            body_parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return

            headers = list(start_message.get("headers", []))
            content_type = next(
                (value.decode("latin-1").lower() for key, value in headers if key.lower() == b"content-type"),
                "",
            )
            body = b"".join(body_parts)
            if "application/pdf" in content_type:
                disposition = next(
                    (value.decode("latin-1") for key, value in headers if key.lower() == b"content-disposition"),
                    "attachment; filename=document.pdf",
                )
                fallback_match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', disposition, re.I)
                fallback = fallback_match.group(1) if fallback_match else "document.pdf"
                filename = pdf_export_filename(request_path, fallback)
                mode = "inline" if query.get("view", [""])[0] == "1" else "attachment"
                headers = [(key, value) for key, value in headers if key.lower() != b"content-disposition"]
                headers.append((b"content-disposition", f'{mode}; filename="{filename}"'.encode("latin-1")))
            if "text/html" in content_type:
                charset = "utf-8"
                text = body.decode(charset)
                effective_kind = success_kind or mutation_kind
                success_message = _success_experience(effective_kind)[0] if effective_kind and start_message.get("status", 500) < 400 else ""
                text = inject_user_experience(text, request_path, success_message, demo_mode, empty_search, effective_kind)
                body = inject_release_footer(text).encode(charset)
                headers = [(key, value) for key, value in headers if key.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode("ascii")))

            status = int(start_message.get("status", 500))
            if request_method == "POST" and 200 <= status < 400 and mutation_kind and "application/pdf" not in content_type:
                headers.append((b"set-cookie", b"tp_success=" + mutation_kind.encode() + b"; Max-Age=60; Path=/; SameSite=Lax"))
            elif has_success_cookie and "text/html" in content_type:
                headers.append((b"set-cookie", b"tp_success=; Max-Age=0; Path=/; SameSite=Lax"))

            start_message["headers"] = headers
            await send(start_message)
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, send_with_footer)


def form_page(title: object, content: str, *, subtitle: object = "", navigation: str = "", max_width: int = 900) -> str:
    return page_shell(title, content, subtitle=subtitle, navigation=navigation, styles=form_css(max_width=max_width), main_class="container")


def section_card(title: object, content: str) -> str:
    return f'<section class="card"><h2>{html_escape(title)}</h2>{content}</section>'


def metadata(items: list[tuple[object, str]] | tuple[tuple[object, str], ...]) -> str:
    fields = "".join(
        f'<div class="tp-metadata-item"><span class="tp-metadata-label">{html_escape(label)}</span>{content}</div>'
        for label, content in items
    )
    return f'<div class="tp-metadata">{fields}</div>'


def action_buttons(*items: str) -> str:
    return f'<div class="tp-form-footer">{"".join(items)}</div>'


def navigation_footer(list_url: str, list_label: object, *, state: object = "") -> str:
    items = [button(list_label, list_url, "secondary"), button("Dashboard", "/", "secondary")]
    if state:
        items.append(status_badge(state, "warning" if str(state) == "Editing" else "neutral"))
    return action_buttons(*items)


def form_footer(cancel_url: str, submit_label: object, *, shipment_url: str = "") -> str:
    actions = [button(submit_label, button_type="submit"), button("Cancel", cancel_url, "secondary")]
    if shipment_url:
        actions.append(button("Return to Shipment", shipment_url, "secondary"))
    return action_buttons(*actions)
