"""Additional synthetic challenges. English TTS reads romanized code-switching;
this deliberately does not claim native Tamil/Hindi speaker coverage.
"""

import hashlib
import json
import subprocess
from pathlib import Path

from .generate import generate


def generate_expanded(root: Path):
    from PIL import Image, ImageDraw, ImageFont

    generate(root)
    manifest = root / "manifest.json"
    data = json.loads(manifest.read_text())
    cases = data["cases"]
    reviews = [
        (
            "The sambar was delicious; the box split open in the bag.",
            ["taste:POSITIVE", "packaging:NEGATIVE"],
            "mixed",
            "en",
        ),
        (
            "It was not hot when it reached me, though the delivery was quick.",
            ["temperature:NEGATIVE", "delivery:POSITIVE"],
            "negation",
            "en",
        ),
        (
            "Paisa vasool tha, lekin khana thanda aaya.",
            ["price:POSITIVE", "temperature:NEGATIVE"],
            "code_switch",
            "hinglish",
        ),
        (
            "Saapadu taste super, aana quantity kammi.",
            ["taste:POSITIVE", "portion:NEGATIVE"],
            "code_switch",
            "tanglish",
        ),
        (
            "One star is a typo. The parcel was secure and the food tasted great.",
            ["packaging:POSITIVE", "taste:POSITIVE"],
            "rating_conflict",
            "en",
        ),
        (
            "We finished the tiny serving in three bites. Too expensive for that amount.",
            ["portion:NEGATIVE", "price:NEGATIVE"],
            "indirect",
            "en",
        ),
    ]
    for i, (text, labels, category, language) in enumerate(reviews, 6):
        cases.append(
            dict(
                id=f"review-{i}",
                task="review",
                group_id=f"review-{i}",
                text=text,
                rating=1 if i == 10 else 3,
                expected=labels,
                language=language,
                category=category,
            )
        )
    orders = [
        {"items": {"Masala Dosa": 2, "Filter Coffee": 1}},
        {"items": {"Masala Dosa": 1}},
        {"items": {"Filter Coffee": 1}, "notes_contains": ["no sugar"]},
    ]
    for i, expected in enumerate(orders):
        case = next(c for c in cases if c["id"] == f"audio-{i}")
        case["expected_order"] = expected
        if i == 1:
            case["initial_order"] = [
                {"name": "Masala Dosa", "qty": 2},
                {"name": "Filter Coffee", "qty": 1},
            ]
    speech = [
        (
            "Do masala dosa aur ek filter coffee please.",
            ["masala", "coffee"],
            "hinglish",
            {"Masala Dosa": 2, "Filter Coffee": 1},
        ),
        (
            "Rendu masala dosa, oru filter coffee venum.",
            ["masala", "coffee"],
            "tanglish",
            {"Masala Dosa": 2, "Filter Coffee": 1},
        ),
        (
            "One cheese dosa and two filter coffees please.",
            ["cheese", "two"],
            "en",
            {"Cheese Dosa": 1, "Filter Coffee": 2},
        ),
    ]
    for i, (text, terms, language, items) in enumerate(speech, 3):
        path = root / f"audio-{i}.wav"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"flite=text='{text}':voice=slt",
        ]
        if i == 5:
            command += ["-af", "aecho=0.8:0.9:35:0.25"]
        subprocess.run([*command, "-ar", "16000", "-ac", "1", str(path)], check=True)
        cases.append(
            dict(
                id=f"audio-{i}",
                task="audio",
                group_id=f"tts-{i}",
                path=path.name,
                mime_type="audio/wav",
                expected=text,
                required_terms=terms,
                language=language,
                category="code_switch" if i < 5 else "echo",
                expected_order={"items": items},
                generator="English libflite slt; romanized words, not native pronunciation",
            )
        )
    font = ImageFont.truetype("DejaVuSans.ttf", 24)
    specs = [
        (
            "Garden Demo Wholesale",
            [("Tomato", "5", "kg", "30", "150"), ("Onion", "3", "kg", "40", "120")],
            "270",
            "card",
            "MATCHED",
        ),
        (
            "Dairy Demo Depot",
            [("Milk", "2", "l", "50", "100")],
            "100",
            "landscape",
            "PENDING_REVIEW",
        ),
        (
            "Grain Demo Traders",
            [("Idli rice", "10", "kg", "60", "600"), ("Packaging fee", "1", "pc", "40", "40")],
            "640",
            "ledger",
            "PENDING_REVIEW",
        ),
    ]
    for i, (supplier, lines, total, layout, status) in enumerate(specs, 3):
        expected = {
            "supplier_name": supplier,
            "invoice_number": f"EXP-{i}",
            "invoice_date": "2026-10-19",
            "lines": [
                dict(zip(["name", "qty", "unit", "unit_price", "amount"], line, strict=True))
                for line in lines
            ],
            "total": total,
        }
        canvas = Image.new("RGB", (1100, 480) if layout == "landscape" else (800, 800), "white")
        d = ImageDraw.Draw(canvas)
        header = [
            "SYNTHETIC FIXTURE - NOT A REAL BILL",
            supplier,
            f"Invoice: EXP-{i}  Date: 2026-10-19",
        ]
        for row, line in enumerate(header):
            d.text((25, 25 + 45 * row), line, font=font, fill="black")
        for row, (name, qty, unit, price, amount) in enumerate(lines):
            y = 200 + row * (150 if layout == "card" else 80)
            d.text((25, y), f"{name} | {qty} {unit}", font=font, fill="black")
            d.text((25, y + 35), f"Rate INR {price}   Amount INR {amount}", font=font, fill="black")
            d.line((20, y - 10, canvas.width - 20, y - 10), fill="black", width=2)
        d.text((25, canvas.height - 65), f"TOTAL INR {total}", font=font, fill="black")
        path = root / f"invoice-{i}.png"
        canvas.save(path)
        po_lines = [{"name": line[0], "qty": line[1]} for line in lines]
        if i == 4:
            po_lines[0]["qty"] = "4"
        if i == 5:
            po_lines.pop()
        cases.append(
            dict(
                id=f"invoice-{i}",
                task="invoice",
                group_id=f"invoice-template-{i - 1}",
                language="en",
                category=layout,
                path=path.name,
                mime_type="image/png",
                expected=expected,
                purchase_order={"supplier": supplier, "lines": po_lines},
                expected_review_status=status,
            )
        )
    for c in cases:
        c.update(
            origin="synthetic",
            permission_ref="generated_for_this_repository",
            provider_use_allowed=True,
        )
        if c["task"] == "invoice" and "purchase_order" not in c:
            c["purchase_order"] = {
                "supplier": c["expected"]["supplier_name"],
                "lines": [
                    {"name": line["name"], "qty": line["qty"]} for line in c["expected"]["lines"]
                ],
            }
            c["expected_review_status"] = (
                "PENDING_REVIEW" if c["category"] == "arithmetic_error" else "MATCHED"
            )
        if "path" in c:
            c["sha256"] = hashlib.sha256((root / c["path"]).read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data, indent=2) + "\n")
