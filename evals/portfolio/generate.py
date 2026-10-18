"""Reproducible artificial review, TTS and rendered invoice challenge assets.

Requires ffmpeg with libflite and Pillow locally; generated assets are committed
so CI does not need speech synthesis or graphics system packages.
"""

import hashlib
import json
import subprocess
from pathlib import Path


def generate(root: Path):
    from PIL import Image, ImageDraw, ImageFont

    root.mkdir(parents=True, exist_ok=False)
    cases = []
    reviews = [
        (
            "The lid stayed shut, yet the meal arrived cold.",
            ["packaging:POSITIVE", "temperature:NEGATIVE"],
            "mixed",
        ),
        (
            "Not a bad taste, but paying this much hurts.",
            ["taste:POSITIVE", "price:NEGATIVE"],
            "negation",
        ),
        (
            "Packing nalla irundhuchu, delivery romba late.",
            ["packaging:POSITIVE", "delivery:NEGATIVE"],
            "tanglish",
        ),
        (
            "Khana tasty tha, quantity bahut kam thi.",
            ["taste:POSITIVE", "portion:NEGATIVE"],
            "hinglish",
        ),
        (
            "Five stars by mistake: everything tasted stale.",
            ["freshness:NEGATIVE", "taste:NEGATIVE"],
            "rating_conflict",
        ),
        (
            "Enough for both of us and still steaming when opened.",
            ["portion:POSITIVE", "temperature:POSITIVE"],
            "indirect",
        ),
    ]
    for i, (text, labels, category) in enumerate(reviews):
        cases.append(
            {
                "id": f"review-{i}",
                "task": "review",
                "group_id": f"review-{i}",
                "text": text,
                "rating": 5,
                "expected": labels,
                "language": category if category in {"tanglish", "hinglish"} else "en",
                "category": category,
            }
        )
    utterances = [
        (
            "Please send two masala dosas and one filter coffee.",
            ["two", "masala", "coffee"],
            "clean",
        ),
        (
            "Make that one dosa, not two, and no coffee.",
            ["one", "not two", "no coffee"],
            "correction",
        ),
        ("One coffee please. No sugar in the coffee.", ["one", "coffee", "no sugar"], "noise"),
    ]
    for i, (text, terms, category) in enumerate(utterances):
        path = root / f"audio-{i}.wav"
        source = f"flite=text='{text}':voice=slt"
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", source]
        if category == "noise":
            command += [
                "-f",
                "lavfi",
                "-i",
                "anoisesrc=color=white:amplitude=0.025:seed=7",
                "-filter_complex",
                "[0:a][1:a]amix=inputs=2:duration=first",
            ]
        subprocess.run([*command, "-ar", "16000", "-ac", "1", str(path)], check=True)
        cases.append(
            {
                "id": f"audio-{i}",
                "task": "audio",
                "group_id": f"tts-{i}",
                "path": path.name,
                "mime_type": "audio/wav",
                "expected": text,
                "required_terms": terms,
                "language": "en",
                "category": category,
                "generator": "ffmpeg libflite slt; synthetic voice",
            }
        )
    font = ImageFont.truetype("DejaVuSans.ttf", 22)
    for i, category in enumerate(["clean", "rotated", "arithmetic_error"]):
        total = "800" if category != "arithmetic_error" else "850"
        expected = {
            "supplier_name": "Portfolio Demo Supplies",
            "invoice_number": f"DEMO-{i + 1}",
            "invoice_date": "2026-10-01",
            "lines": [
                {
                    "name": "Idli rice",
                    "qty": "10",
                    "unit": "kg",
                    "unit_price": "60",
                    "amount": "600",
                },
                {"name": "Milk", "qty": "4", "unit": "l", "unit_price": "50", "amount": "200"},
            ],
            "total": total,
        }
        canvas = Image.new("RGB", (1000, 620), "white")
        draw = ImageDraw.Draw(canvas)
        lines = [
            "SYNTHETIC PORTFOLIO FIXTURE - NOT A REAL BILL",
            "Portfolio Demo Supplies",
            f"Invoice DEMO-{i + 1}    Date: 2026-10-01",
            "Item          Qty      Unit   Price(INR)   Amount(INR)",
            "Idli rice     10       kg     60           600",
            "Milk           4        l     50           200",
            f"TOTAL INR {total}",
        ]
        for row, line in enumerate(lines):
            draw.text((35, 30 + row * 65), line, fill="black", font=font)
        if category == "rotated":
            canvas = canvas.rotate(3, expand=True, fillcolor="white")
        path = root / f"invoice-{i}.png"
        canvas.save(path)
        cases.append(
            {
                "id": f"invoice-{i}",
                "task": "invoice",
                "group_id": "invoice-template-1",
                "path": path.name,
                "mime_type": "image/png",
                "expected": expected,
                "language": "en",
                "category": category,
            }
        )
    for case in cases:
        case["origin"] = "synthetic"
        case["permission_ref"] = "generated_for_this_repository"
        case["provider_use_allowed"] = True
        if "path" in case:
            case["sha256"] = hashlib.sha256((root / case["path"]).read_bytes()).hexdigest()
    (root / "manifest.json").write_text(
        json.dumps(
            {"schema_version": 1, "evidence": "synthetic_portfolio_demo", "cases": cases}, indent=2
        )
        + "\n"
    )
