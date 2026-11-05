"""Keep document issue dates stable across PDF downloads."""
from datetime import date

from app.validation import DataValidationError


def new_document_date(value, label="Document date"):
    """Use the reviewed date, or the creation day for a new document."""
    text = str(value or "").strip()
    if not text:
        return date.today().isoformat()
    try:
        parsed = date.fromisoformat(text)
        if parsed.isoformat() != text:
            raise ValueError
        return text
    except ValueError as exc:
        raise DataValidationError(label, "Enter a valid date in YYYY-MM-DD format.",
                                  "Review the document date, then save again.") from exc


def saved_document_date(record, field):
    """Never invent an issue date for an older record or an unsaved preview."""
    text = str(record.get(field) or "").strip()
    try:
        parsed = date.fromisoformat(text)
        if parsed.isoformat() == text:
            return text
    except ValueError:
        pass
    return "Not recorded"
