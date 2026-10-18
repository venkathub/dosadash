"""Audit → freeze → validation → explicit one-time final-test access."""

import argparse
import asyncio
import json
from pathlib import Path

from .contracts import Protocol
from .dataset import ROOT, audit, freeze, load_records, load_references


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit", "freeze", "run"])
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument(
        "--references", type=Path, help="Additional known training/tuning texts (JSONL)"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="New output path; never overwritten"
    )
    parser.add_argument("--dataset-id")
    parser.add_argument("--version")
    parser.add_argument("--backend", choices=["onnx", "llm", "compare"], default="onnx")
    parser.add_argument("--model-dir", type=Path, default=ROOT / "packages/ml/artifacts")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--split", choices=["val", "test"], default="val")
    parser.add_argument("--release-test", action="store_true")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output exists; preserve it and choose a new path")
    if args.command == "freeze" and (not args.dataset_id or not args.version):
        parser.error("freeze requires --dataset-id and --version")
    if args.command == "run" and not args.manifest:
        parser.error("run requires --manifest")
    # Reserve the destination before opening a test split or making provider calls.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as output:
        try:
            references = load_references(args.references)
            if args.command == "audit":
                result = audit(load_records(args.records), references)
                success = result["eligible"]
            elif args.command == "freeze":
                result = freeze(
                    args.records,
                    references,
                    dataset_id=args.dataset_id,
                    version=args.version,
                    protocol=Protocol(backend=args.backend),
                    model_dir=args.model_dir,
                ).model_dump(mode="json")
                success = True
            else:
                from .review_runner import run

                result = asyncio.run(
                    run(
                        args.records,
                        args.manifest,
                        references,
                        model_dir=args.model_dir,
                        split=args.split,
                        release_test=args.release_test,
                    )
                )
                success = result["status"] == "complete"
        except Exception as exc:
            result = {
                "status": "error",
                "error": type(exc).__name__,
                "message": str(exc)
                if type(exc) is ValueError
                else ("Check the data audit, frozen inputs, and test-access ledger."),
            }
            success = False
        json.dump(result, output, indent=2, ensure_ascii=False)
        output.write("\n")
    print(f"{'completed' if success else 'failed'}: {args.out}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
