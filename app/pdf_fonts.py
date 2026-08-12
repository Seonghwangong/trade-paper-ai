"""Shared, embedded Unicode fonts for Trade Paper PDF documents."""

from pathlib import Path
import threading

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


TP_UNICODE = "TPUnicode"
TP_UNICODE_BOLD = "TPUnicode-Bold"

_FONT_DIR = Path(__file__).resolve().parent / "fonts"
_FONT_FILES = {
    TP_UNICODE: _FONT_DIR / "TradePaperUnicode-Regular.ttf",
    TP_UNICODE_BOLD: _FONT_DIR / "TradePaperUnicode-Bold.ttf",
}
_FONT_LOCK = threading.Lock()


def ensure_pdf_fonts() -> None:
    """Register the bundled OFL fonts once per process."""
    registered = set(pdfmetrics.getRegisteredFontNames())
    if all(name in registered for name in _FONT_FILES):
        return
    with _FONT_LOCK:
        registered = set(pdfmetrics.getRegisteredFontNames())
        for name, path in _FONT_FILES.items():
            if name not in registered:
                if not path.is_file():
                    raise RuntimeError(f"Bundled PDF font is missing: {path.name}")
                pdfmetrics.registerFont(TTFont(name, str(path)))
                registered.add(name)


def fit_pdf_text(pdf, text, max_width, font_name=TP_UNICODE, font_size=8, suffix="..."):
    """Width-limit one line using the exact font used for drawing."""
    value = str(text or "")
    if pdf.stringWidth(value, font_name, font_size) <= max_width:
        return value
    while value and pdf.stringWidth(value + suffix, font_name, font_size) > max_width:
        value = value[:-1]
    return value + suffix if value else suffix
