# Public fictional document previews

These two static PDFs are intentionally public and contain fictional data only.
They use the same Invoice and Packing List renderers as the application, with a
sample label and walkthrough URL added. They are not valid shipment documents.

Rebuild from the repository root:

    venv/bin/python scripts/build_public_samples.py

The builder passes every party field explicitly and never loads account data,
creates an account/document, or sends email. Review both rendered PDFs after any
renderer change; date and PDF metadata reflect the build date. The related invoice
is SAMPLE-INV-001: USD 1,000, 150 pieces, 7 cartons. HS codes are intentionally
blank; they are not classification advice.

Public links are included on the landing page, getting-started guide and beta
application page. Existing customer document/PDF routes still require login.
Viewing static previews does not consume the Free plan's document allowance.
