"""Finite, time-aware waveform checks; execution evidence is a separate gate."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import hashlib
import itertools
import json
import math
from pathlib import Path
import re

from kicad_mcp.raw import read_raw, summarize_raw


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ArithmeticError(f"Non-finite intermediate computation: {label}")
    return value


def _product(*values: float) -> float:
    """Multiply finite factors without a spurious intermediate overflow."""
    for value in values:
        _finite(value, "product factor")
    if any(value == 0 for value in values):
        return 0.0
    mantissa, exponent = 1.0, 0
    for value in values:
        part, shift = math.frexp(value)
        mantissa *= part
        exponent += shift
    result = _finite(math.ldexp(mantissa, exponent), "product")
    if result == 0:
        raise ArithmeticError("Underflow in a nonzero product")
    return result


def _crossing_fraction(above: float, below: float, threshold: float) -> float:
    """Fraction of a linear segment above threshold; avoid huge differences."""
    _, exponent = math.frexp(max(abs(above), abs(below), abs(threshold)))
    scaled = [math.ldexp(value, -exponent) for value in (above, below, threshold)]
    if any(original != 0 and normalized == 0 for original, normalized in zip((above, below, threshold), scaled)):
        raise ArithmeticError("Underflow while normalizing a threshold crossing")
    numerator, denominator = scaled[0] - scaled[2], scaled[0] - scaled[1]
    if numerator <= 0 or denominator <= 0:
        raise ArithmeticError("Threshold crossing lost precision during normalization")
    fraction = _finite(numerator / denominator, "threshold crossing fraction")
    if not 0 < fraction <= 1:
        raise ArithmeticError("Threshold crossing fraction is outside (0, 1]")
    return fraction


def _keys(value: dict, allowed: set[str], required: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if set(value) - allowed or required - set(value):
        raise ValueError(f"{label}: unknown or missing keys")


def _window(axis, values, start: float, end: float):
    """Clip saved samples with linear boundary interpolation, never extrapolate."""
    def at(t):
        i = bisect_left(axis, t)
        if i < len(axis) and axis[i] == t:
            return values[i]
        numerator = _finite(t - axis[i - 1], "window interpolation offset")
        denominator = _finite(axis[i] - axis[i - 1], "window interpolation interval")
        f = numerator / denominator
        return _finite(values[i - 1] * (1 - f) + values[i] * f, "window interpolation value")

    first, last = bisect_right(axis, start), bisect_left(axis, end)
    return ([start] + list(axis[first:last]) + [end],
            [at(start)] + list(values[first:last]) + [at(end)])


def _snap_boundary(bound: float, lower: float, upper: float) -> float:
    """Allow only floating-point endpoint representation noise, not missing data."""
    endpoint = lower if bound < lower else upper if bound > upper else None
    if endpoint is not None and abs(bound - endpoint) <= 8 * max(math.ulp(bound), math.ulp(endpoint)):
        return endpoint
    return bound


def _positive_area(x, y) -> float:
    terms = []
    for a, b, ya, yb in zip(x, x[1:], y, y[1:]):
        dt = _finite(b - a, "positive-charge interval")
        if ya >= 0 and yb >= 0:
            halves = (ya / 2, yb / 2)
            if any(original != 0 and half == 0 for original, half in zip((ya, yb), halves)):
                raise ArithmeticError("Underflow in a positive-charge trapezoid")
            terms.append(_product(dt, _finite(math.fsum(halves), "positive-charge mean")))
        elif ya > 0:
            terms.append(_product(dt, ya, _crossing_fraction(ya, yb, 0), .5))
        elif yb > 0:
            terms.append(_product(dt, yb, _crossing_fraction(yb, ya, 0), .5))
    return _finite(math.fsum(terms), "positive-charge sum")


def _above_duration(x, y, threshold: float) -> float:
    terms = []
    for a, b, ya, yb in zip(x, x[1:], y, y[1:]):
        dt = _finite(b - a, "threshold-duration interval")
        if ya > threshold and yb > threshold:
            terms.append(dt)
        elif ya > threshold:
            terms.append(_product(dt, _crossing_fraction(ya, yb, threshold)))
        elif yb > threshold:
            terms.append(_product(dt, _crossing_fraction(yb, ya, threshold)))
    return _finite(math.fsum(terms), "threshold-duration sum")


def _validated_spec(spec: dict) -> list[dict]:
    _keys(spec, {"schema_version", "expected_stop_s", "expected_start_s", "model_scope", "purpose", "checks"},
          {"schema_version", "expected_stop_s", "model_scope", "purpose", "checks"}, "spec")
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise ValueError("Only schema_version 1 is supported")
    stop = _number(spec["expected_stop_s"], "expected_stop_s")
    start = _number(spec.get("expected_start_s", 0), "expected_start_s")
    if stop <= start:
        raise ValueError("expected_stop_s must exceed expected_start_s")
    if spec["purpose"] not in ("requirements", "negative_control"):
        raise ValueError("purpose must be requirements or negative_control")
    if not isinstance(spec["model_scope"], str) or not spec["model_scope"].strip():
        raise ValueError("model_scope must describe the model's limitations")
    checks = spec["checks"]
    if not isinstance(checks, list) or not checks:
        raise ValueError("At least one predefined check is required")
    names = set()
    for check in checks:
        _keys(check, {"name", "signal", "metric", "start_s", "end_s", "polarity", "threshold", "min", "max", "unit"},
              {"name", "signal", "metric", "start_s", "end_s", "unit"}, "check")
        for field in ("name", "signal", "unit"):
            if not isinstance(check[field], str) or not check[field].strip():
                raise ValueError(f"check {field} must be a nonempty string")
        if check["name"] in names:
            raise ValueError("Check names must be unique")
        names.add(check["name"])
        if check["metric"] not in ("min", "max", "time_mean", "integral", "positive_charge", "above_duration"):
            raise ValueError("Unsupported metric")
        a, b = (_number(check[k], k) for k in ("start_s", "end_s"))
        if not start <= a < b <= stop:
            raise ValueError("Every check needs a positive interval inside the expected run")
        if _number(check.get("polarity", 1), "polarity") not in (-1, 1):
            raise ValueError("polarity must be 1 or -1")
        if not {"min", "max"} & set(check):
            raise ValueError("At least one acceptance bound is required")
        for key in ("min", "max"):
            if key in check:
                _number(check[key], key)
        if "min" in check and "max" in check and check["min"] > check["max"]:
            raise ValueError("min exceeds max")
        if check["metric"] == "above_duration":
            _number(check.get("threshold"), "threshold")
        elif "threshold" in check:
            raise ValueError("threshold is only valid for above_duration")
    return checks


def assess(receipt: dict, spec: dict, *, binary_format: str = "auto") -> dict:
    """Evaluate a KiCadMCP receipt; a failed completion gate can never be PASS.

    Units and model fidelity remain the caller's responsibility. `positive_charge`
    integrates max(polarity * signal, 0): use an ampere-valued current signal.
    """
    checks = _validated_spec(spec)
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be a KiCadMCP result object")
    result = {"schema_version": 1, "purpose": spec["purpose"], "status": "INCOMPLETE",
              "execution_complete": False, "expectation_met": False, "specification_pass": None,
              "model_scope": spec["model_scope"], "checks": [], "completion_issues": [],
              "spec": spec, "spec_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True, allow_nan=False).encode()).hexdigest(),
              "scope": "Saved samples with linear interpolation; no prediction between unsaved samples or hardware guarantee."}
    issues = result["completion_issues"]
    for key in ("completed", "numerical_complete", "process_success", "raw_valid"):
        if receipt.get(key) is not True:
            issues.append(f"KiCadMCP receipt {key} is not true")
    if type(receipt.get("returncode")) is not int or receipt.get("returncode") != 0 or receipt.get("timed_out") is not False or receipt.get("fatal") is not False:
        issues.append("Simulator exit, timeout, or fatal-log gate failed")
    raw_path = receipt.get("raw_path")
    if not isinstance(raw_path, str) or not Path(raw_path).is_file():
        issues.append("RAW file is unavailable")
        return result
    path = Path(raw_path)
    result["raw_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    result["source_sha256"] = receipt.get("source_sha256")
    try:
        raw = read_raw(path, binary_format=binary_format)
        axis = raw.axis
        if raw.variables[0].lower() != "time":
            issues.append("Only time-domain transient data is supported")
        if not all(math.isfinite(v) for vector in raw.vectors.values() for v in vector):
            issues.append("RAW contains non-finite values")
        if len(axis) < 2 or any(b <= a for a, b in zip(axis, axis[1:])):
            issues.append("Time axis must have at least two strictly increasing points")
        stop = spec["expected_stop_s"]
        if not math.isclose(axis[-1], stop, rel_tol=1e-6, abs_tol=max(1e-15, abs(stop) * 1e-9)):
            issues.append("RAW did not reach the independently specified stop time")
        if axis[0] > spec.get("expected_start_s", 0):
            issues.append("RAW omits the independently specified start time")
        windows = [(_snap_boundary(check["start_s"], axis[0], axis[-1]),
                    _snap_boundary(check["end_s"], axis[0], axis[-1])) for check in checks]
        if any(a < axis[0] or b > axis[-1] or a >= b for a, b in windows):
            issues.append("RAW does not cover every measurement window; extrapolation is not allowed")
        if issues:
            return result
        result["execution_complete"] = True
        lookup = {name.casefold(): name for name in raw.variables[1:]}
        for check, (a, b) in zip(checks, windows):
            try:
                signal = lookup[check["signal"].casefold()]
            except KeyError as exc:
                raise ValueError(f"Unknown signal: {check['signal']}") from exc
            polarity = check.get("polarity", 1)
            metric = check["metric"]
            if metric in ("min", "max", "time_mean"):
                stats = summarize_raw(path, [signal], a, b, binary_format=binary_format)["signals"][signal]
                field = {"min": "min", "max": "max", "time_mean": "axis_weighted_mean"}[metric]
                if polarity == -1 and metric in ("min", "max"):
                    field = "max" if metric == "min" else "min"
                value = polarity * stats[field]
            else:
                x, y = _window(axis, raw.vectors[signal], a, b)
                y = [polarity * value for value in y]
                if metric == "integral":
                    value = math.fsum((v - u) * (ya + yb) / 2 for u, v, ya, yb in zip(x, x[1:], y, y[1:]))
                elif metric == "positive_charge":
                    value = _positive_area(x, y)
                else:
                    value = _above_duration(x, y, check["threshold"])
            _finite(value, f"metric {check['name']}")
            passed = ("min" not in check or value >= check["min"]) and ("max" not in check or value <= check["max"])
            result["checks"].append({"name": check["name"], "signal": signal, "metric": metric,
                                     "value": value, "unit": check["unit"], "passed": passed,
                                     "requested_start_s": check["start_s"], "requested_end_s": check["end_s"],
                                     "effective_start_s": a, "effective_end_s": b,
                                     "boundary_snapped": a != check["start_s"] or b != check["end_s"]})
    except (ArithmeticError, ValueError, OSError) as exc:
        issues.append(str(exc))
        result["execution_complete"] = False
        return result
    result["expectation_met"] = all(check["passed"] for check in result["checks"])
    if spec["purpose"] == "negative_control":
        result["status"] = "NEGATIVE_CONTROL_CONFIRMED" if result["expectation_met"] else "NEGATIVE_CONTROL_FAILED"
    else:
        result["specification_pass"] = result["expectation_met"]
        result["status"] = "PASS" if result["expectation_met"] else "FAIL"
    return result


def compare_results(coarse: dict, fine: dict, tolerances: dict) -> dict:
    """Compare predefined metrics, retaining prerequisite failures separately.

    The caller must run the same circuit with a halved maximum timestep. This
    compares observed metrics; it does not infer solver settings from samples.
    """
    for label, value in (("coarse", coarse), ("fine", fine)):
        if not isinstance(value, dict) or not isinstance(value.get("checks"), list):
            raise ValueError(f"{label} must be an assessment object with a checks array")
        names = []
        for row in value["checks"]:
            if not isinstance(row, dict) or not {"name", "value", "unit"} <= set(row):
                raise ValueError(f"{label} contains an invalid metric row")
            if not isinstance(row["name"], str) or not isinstance(row["unit"], str):
                raise ValueError(f"{label} metric names and units must be strings")
            names.append(row["name"])
        if len(names) != len(set(names)):
            raise ValueError(f"{label} contains duplicate metric names")
    _keys(tolerances, {"coarse_max_step_s", "fine_max_step_s", "checks"},
          {"coarse_max_step_s", "fine_max_step_s", "checks"}, "tolerances")
    coarse_step = _number(tolerances["coarse_max_step_s"], "coarse_max_step_s")
    fine_step = _number(tolerances["fine_max_step_s"], "fine_max_step_s")
    if coarse_step <= 0 or fine_step <= 0 or not math.isclose(fine_step, coarse_step / 2, rel_tol=1e-12, abs_tol=0):
        raise ValueError("Declared fine maximum step must be half the coarse maximum step")
    limits = tolerances["checks"]
    if not isinstance(limits, dict) or not limits:
        raise ValueError("Explicit tolerances are required")
    output = {"status": "INCOMPLETE", "checks": [], "issues": [],
              "declared_max_steps_s": [coarse_step, fine_step],
              "scope": "Metric stability at declared timesteps; deck settings and identical model/stimulus must be verified separately."}
    for label, value in (("coarse", coarse), ("fine", fine)):
        if value.get("execution_complete") is not True or value.get("expectation_met") is not True:
            output["issues"].append(f"{label} run did not complete and meet its expected outcome")
    if coarse.get("spec_sha256") != fine.get("spec_sha256") or not coarse.get("spec_sha256"):
        output["issues"].append("The two runs do not share the same predefined acceptance specification")
    a = {row["name"]: row for row in coarse.get("checks", [])}
    b = {row["name"]: row for row in fine.get("checks", [])}
    if set(a) != set(b) or set(a) != set(limits):
        output["issues"].append("Tolerance coverage must exactly match both runs' metrics")
    if output["issues"]:
        return output
    for name, tolerance in limits.items():
        _keys(tolerance, {"abs_tol", "rel_tol"}, {"abs_tol", "rel_tol"}, "metric tolerance")
        absolute, relative = (_number(tolerance[k], k) for k in ("abs_tol", "rel_tol"))
        if absolute < 0 or relative < 0:
            raise ValueError("Tolerances must be nonnegative")
        x, y = (_number(row["value"], "observed metric") for row in (a[name], b[name]))
        if a[name]["unit"] != b[name]["unit"]:
            raise ValueError("Compared metric units differ")
        delta, allowed = abs(x - y), absolute + relative * max(abs(x), abs(y))
        if not math.isfinite(delta) or not math.isfinite(allowed):
            output["issues"].append(f"{name}: non-finite intermediate difference or allowed tolerance")
            return output
        output["checks"].append({"name": name, "coarse": x, "fine": y, "unit": a[name]["unit"],
                                  "delta": delta, "allowed_delta": allowed, "stable": delta <= allowed})
    output["status"] = "STABLE" if all(check["stable"] for check in output["checks"]) else "UNSTABLE"
    return output


def generate_corners(template: str, parameters: dict, output_dir: str | Path, *, max_cases: int = 256) -> dict:
    """Render finite numeric Cartesian corners into a new directory, no eval."""
    pattern = re.compile(r"@@([A-Z][A-Z0-9_]*)@@")
    if "@@" in pattern.sub("", template):
        raise ValueError("Template contains malformed or unmatched @@ placeholders")
    tokens = set(pattern.findall(template))
    if not tokens or not isinstance(parameters, dict) or tokens != set(parameters):
        raise ValueError("Parameter keys must exactly match template @@UPPERCASE@@ tokens")
    if type(max_cases) is not int or max_cases < 1:
        raise ValueError("max_cases must be a positive integer")
    names = sorted(parameters)
    for name, values in parameters.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"{name} requires a nonempty list")
        for value in values:
            _number(value, name)
        if len(values) != len(set(values)):
            raise ValueError(f"{name} contains duplicate values")
    count = math.prod(len(parameters[name]) for name in names)
    if count > max_cases:
        raise ValueError(f"{count} corners exceeds max_cases={max_cases}")
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "template_sha256": hashlib.sha256(template.encode()).hexdigest(), "cases": []}
    for index, values in enumerate(itertools.product(*(parameters[name] for name in names)), 1):
        mapping = dict(zip(names, values))
        deck = template
        for name, value in mapping.items():
            deck = deck.replace(f"@@{name}@@", format(value, ".17g"))
        if "@@" in deck:
            raise ValueError("Rendered deck still contains an @@ placeholder")
        filename = f"case_{index:04d}.cir"
        (directory / filename).write_text(deck, encoding="utf-8")
        manifest["cases"].append({"netlist": filename, "parameters": mapping,
                                  "sha256": hashlib.sha256(deck.encode()).hexdigest()})
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return manifest
