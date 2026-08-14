"""Build the deterministic 15-second landing demo from the real dashboard capture."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "static" / "dashboard.png"
OUTPUT = ROOT / "app" / "static" / "trade-paper-demo-15s.gif"


def font(size, bold=False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def main():
    source = Image.open(SOURCE).convert("RGB")
    width, height = 960, 540
    stages = (
        ("1. Choose Buyer + Product", "Start with reusable account-owned master data", 0.00),
        ("2. Export Wizard", "Create Invoice, Packing, S/I and Shipment", 0.18),
        ("3. Shipment Tracking", "See status, dates and transport details", 0.38),
        ("4. Document Package", "Open every linked document from one shipment", 0.58),
        ("5. Ready for review", "Edit every generated document before sharing", 0.78),
    )
    frames = []
    for title, subtitle, progress in stages:
        crop_height = int(source.width * height / width)
        max_top = max(0, source.height - crop_height)
        top = int(max_top * progress)
        frame = source.crop((0, top, source.width, min(source.height, top + crop_height))).resize((width, height), Image.Resampling.LANCZOS)
        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rounded_rectangle((34, 354, 700, 504), radius=22, fill=(15, 23, 42, 232))
        draw.text((64, 382), title, font=font(31, True), fill="white")
        draw.text((64, 432), subtitle, font=font(20), fill=(203, 213, 225))
        frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("P", palette=Image.Palette.ADAPTIVE, colors=128)
        frames.append(frame)
    frames[0].save(OUTPUT, save_all=True, append_images=frames[1:], duration=3000, loop=0, optimize=True, disposal=2)


if __name__ == "__main__":
    main()
