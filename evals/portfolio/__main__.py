import argparse
import asyncio
import json
from pathlib import Path

from .generate import generate
from .runner import run


def main():
    parser = argparse.ArgumentParser(
        description="Synthetic portfolio demos, separate from independent data"
    )
    parser.add_argument(
        "command", choices=["generate", "generate-expanded", "analyze-reviews", "run"]
    )
    parser.add_argument(
        "--manifest", type=Path, default=Path("evals/portfolio/fixtures/manifest.json")
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=["replay", "local", "live"], default="local")
    parser.add_argument("--tasks", nargs="+", choices=["review", "audio", "invoice"])
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.out)
        return 0
    if args.command == "generate-expanded":
        from .generate_expanded import generate_expanded

        generate_expanded(args.out)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as output:
        try:
            if args.command == "analyze-reviews":
                from .review_analysis import local_analysis
                from .runner import fingerprint, read_cases

                result = local_analysis(
                    [c for c in read_cases(args.manifest) if c.task == "review"]
                )
                result.update(status="complete", fingerprint=fingerprint(args.manifest))
            else:
                result = asyncio.run(run(args.manifest, args.mode, args.tasks))
        except Exception as exc:
            result = {"status": "error", "error": type(exc).__name__}
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    return int(result["status"] == "error")


if __name__ == "__main__":
    raise SystemExit(main())
