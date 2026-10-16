"""Small JSON CLI. Simulator launching and RAW parsing belong to KiCadMCP."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from kicad_mcp.spice import run_spice

from .verification import assess, compare_results, generate_corners


def _load(path):
    with Path(path).open(encoding="utf-8") as stream:
        def reject(value):
            raise ValueError(f"Non-finite JSON constant: {value}")
        return json.load(stream, parse_constant=reject)


def _write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run one trusted deck through KiCadMCP and assess a predefined specification")
    run.add_argument("netlist")
    run.add_argument("spec")
    run.add_argument("--output-dir", required=True)
    run.add_argument("--engine", choices=("ngspice", "ltspice"), default="ngspice")
    run.add_argument("--executable")
    run.add_argument("--timeout", type=float, default=60)
    check = sub.add_parser("assess", help="Assess a saved KiCadMCP receipt")
    check.add_argument("receipt")
    check.add_argument("spec")
    check.add_argument("--output", required=True)
    compare = sub.add_parser("compare", help="Compare metric stability after a half-maximum-step rerun")
    compare.add_argument("coarse")
    compare.add_argument("fine")
    compare.add_argument("tolerances")
    compare.add_argument("--output", required=True)
    corners = sub.add_parser("corners", help="Generate a bounded grid of finite numeric template parameters")
    corners.add_argument("template")
    corners.add_argument("parameters")
    corners.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            spec = _load(args.spec)
            # Reject malformed acceptance inputs before launching a simulator.
            from .verification import _validated_spec
            _validated_spec(spec)
            receipt = run_spice(args.netlist, engine=args.engine, executable=args.executable,
                                timeout=args.timeout, output_dir=args.output_dir)
            folder = Path(receipt["run_dir"])
            _write(folder / "run_receipt.json", receipt)
            output = assess(receipt, spec, binary_format=args.engine)
            destination = folder / "assessment.json"
            _write(destination, output)
        elif args.command == "assess":
            output = assess(_load(args.receipt), _load(args.spec))
            destination = Path(args.output)
            _write(destination, output)
        elif args.command == "compare":
            output = compare_results(_load(args.coarse), _load(args.fine), _load(args.tolerances))
            destination = Path(args.output)
            _write(destination, output)
        else:
            output = generate_corners(Path(args.template).read_text(encoding="utf-8"),
                                      _load(args.parameters), args.output_dir)
            destination = Path(args.output_dir) / "manifest.json"
        print(json.dumps({"output": str(destination), "status": output.get("status", "GENERATED"),
                          "cases": len(output["cases"]) if "cases" in output else None}, ensure_ascii=False))
        if output.get("status") == "INCOMPLETE":
            return 2
        return 0 if output.get("status", "GENERATED") in ("PASS", "NEGATIVE_CONTROL_CONFIRMED", "STABLE", "GENERATED") else 1
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
