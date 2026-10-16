from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from spice_skills import assess, compare_results, generate_corners


def write_raw(path, rows):
    """Synthetic ASCII fixture writer, not an alternative RAW reader."""
    lines = ["Title: synthetic acceptance fixture", "Plotname: Transient Analysis", "Flags: real",
             "No. Variables: 2", f"No. Points: {len(rows)}", "Variables:",
             "\t0\ttime\ttime", "\t1\ti(branch)\tcurrent", "Values:"]
    for i, (t, value) in enumerate(rows):
        lines.extend([f"{i}\t{t}", f"\t{value}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def spec(metric="time_mean", **changes):
    check = {"name": "current", "signal": "I(BRANCH)", "metric": metric,
             "start_s": 0, "end_s": 1, "min": -100, "max": 100, "unit": "A"}
    check.update(changes)
    return {"schema_version": 1, "expected_stop_s": 1, "purpose": "requirements",
            "model_scope": "Synthetic piecewise linear current; no physical model.", "checks": [check]}


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.raw"
        self.receipt = {"completed": True, "numerical_complete": True, "process_success": True,
                        "raw_valid": True, "returncode": 0, "timed_out": False, "fatal": False,
                        "raw_path": str(self.path), "source_sha256": "synthetic"}

    def assess_rows(self, rows, specification):
        write_raw(self.path, rows)
        return assess(self.receipt, specification)

    def test_adaptive_sampling_uses_time_mean_not_sample_mean(self):
        rows = [(0, 0), (.01, 10), (.02, 0), (1, 0)]
        result = self.assess_rows(rows, spec(min=.099999, max=.100001))
        self.assertEqual(result["status"], "PASS")
        self.assertAlmostEqual(result["checks"][0]["value"], .1)
        self.assertNotAlmostEqual(result["checks"][0]["value"], sum(v for _, v in rows) / len(rows))

    def test_positive_charge_does_not_cancel_reverse_current(self):
        rows = [(0, -2), (1, 2)]
        positive = self.assess_rows(rows, spec("positive_charge", min=.5, max=.5, unit="C"))
        signed = self.assess_rows(rows, spec("integral", min=0, max=0, unit="C"))
        self.assertEqual(positive["status"], "PASS")
        self.assertEqual(signed["status"], "PASS")
        self.assertEqual(positive["checks"][0]["value"], .5)
        self.assertEqual(signed["checks"][0]["value"], 0)

    def test_polarity_and_interpolated_window_boundaries(self):
        result = self.assess_rows([(0, 0), (1, -2)], spec("positive_charge", polarity=-1,
                                 start_s=.25, end_s=.75, min=.5, max=.5, unit="C"))
        self.assertEqual(result["status"], "PASS")
        inverse_min = self.assess_rows([(0, 0), (1, -2)], spec("min", polarity=-1, min=0, max=0))
        inverse_max = self.assess_rows([(0, 0), (1, -2)], spec("max", polarity=-1, min=2, max=2))
        self.assertEqual(inverse_min["status"], "PASS")
        self.assertEqual(inverse_max["status"], "PASS")

    def test_duration_splits_threshold_crossing(self):
        result = self.assess_rows([(0, 0), (.5, 2), (1, 0)],
                                 spec("above_duration", threshold=1, min=.5, max=.5, unit="s"))
        self.assertEqual(result["status"], "PASS")

    def test_threshold_plateau_not_counted(self):
        result = self.assess_rows([(0, 1), (1, 1)],
                                 spec("above_duration", threshold=1, min=0, max=0, unit="s"))
        self.assertEqual(result["status"], "PASS")

    def test_extreme_finite_crossing_does_not_become_zero_duration(self):
        result = self.assess_rows([(0, -1e308), (1, 1e308)],
                                 spec("above_duration", threshold=0, min=0, max=.1, unit="s"))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["checks"][0]["value"], .5)
        json.dumps(result, allow_nan=False)
        offset = self.assess_rows([(0, -1e308), (1, 1e308)],
                                 spec("above_duration", threshold=-5e307, min=.75, max=.75, unit="s"))
        self.assertEqual(offset["status"], "PASS")

    def test_extreme_positive_charge_avoids_intermediate_square_overflow(self):
        result = self.assess_rows([(0, -1e308), (1, 1e308)],
                                 spec("positive_charge", min=2.5e307, max=2.5e307, unit="C"))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["checks"][0]["value"], 2.5e307)
        trapezoid = self.assess_rows([(0, 1e308), (1, 1e308)],
                                    spec("positive_charge", min=1e308, max=1e308, unit="C"))
        self.assertEqual(trapezoid["status"], "PASS")
        json.dumps(result, allow_nan=False)
        json.dumps(trapezoid, allow_nan=False)

    def test_unrepresentable_metric_is_incomplete_and_json_remains_finite(self):
        for metric in ("positive_charge", "integral", "time_mean"):
            with self.subTest(metric=metric):
                specification = spec(metric, end_s=10, min=0, max=1e308)
                specification["expected_stop_s"] = 10
                result = self.assess_rows([(0, 1e308), (10, 1e308)], specification)
                self.assertEqual(result["status"], "INCOMPLETE")
                self.assertIsNone(result["specification_pass"])
                json.dumps(result, allow_nan=False)

    def test_negative_control_is_not_specification_pass(self):
        negative = spec("max", min=1, max=2)
        negative["purpose"] = "negative_control"
        result = self.assess_rows([(0, 1), (1, 1)], negative)
        self.assertEqual(result["status"], "NEGATIVE_CONTROL_CONFIRMED")
        self.assertTrue(result["expectation_met"])
        self.assertIsNone(result["specification_pass"])
        failed = self.assess_rows([(0, 0), (1, 0)], negative)
        self.assertEqual(failed["status"], "NEGATIVE_CONTROL_FAILED")
        self.assertIsNone(failed["specification_pass"])

    def test_exit_zero_with_fatal_or_partial_trace_is_incomplete(self):
        write_raw(self.path, [(0, 0), (1, 0)])
        for key, value in (("fatal", True), ("completed", False), ("numerical_complete", None), ("timed_out", True)):
            with self.subTest(key=key):
                receipt = {**self.receipt, key: value}
                self.assertEqual(assess(receipt, spec())["status"], "INCOMPLETE")
        result = self.assess_rows([(0, 0), (.8, 0)], spec())
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertIsNone(result["specification_pass"])

    def test_missing_start_signal_nonfinite_or_repeated_time_is_incomplete(self):
        cases = [[(.1, 0), (1, 0)], [(0, 0), (0, 1), (1, 0)], [(0, 0), (1, math.nan)]]
        for rows in cases:
            with self.subTest(rows=rows):
                self.assertEqual(self.assess_rows(rows, spec())["status"], "INCOMPLETE")
        self.assertEqual(self.assess_rows([(0, 0), (1, 0)], spec(signal="v(missing)"))["status"], "INCOMPLETE")

    def test_completed_out_of_bounds_is_fail(self):
        result = self.assess_rows([(0, 2), (1, 2)], spec(max=1))
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["execution_complete"])
        self.assertFalse(result["specification_pass"])

    def test_tolerated_stop_rounding_cannot_extrapolate_a_measurement(self):
        result = self.assess_rows([(0, 0), (.99999999, 0)], spec())
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertTrue(any("extrapolation" in issue for issue in result["completion_issues"]))

    def test_one_ulp_end_difference_snaps_and_records_effective_window(self):
        specification = spec("integral", end_s=.11, min=.109, max=.111, unit="C")
        specification["expected_stop_s"] = .11
        endpoint = math.nextafter(.11, 0)
        result = self.assess_rows([(0, 1), (endpoint, 1)], specification)
        self.assertEqual(result["status"], "PASS")
        check = result["checks"][0]
        self.assertEqual(check["requested_end_s"], .11)
        self.assertEqual(check["effective_end_s"], endpoint)
        self.assertTrue(check["boundary_snapped"])
        self.assertEqual(check["value"], endpoint)

    def test_one_nanosecond_missing_end_is_not_rounding_noise(self):
        specification = spec("max", end_s=.11)
        specification["expected_stop_s"] = .11
        result = self.assess_rows([(0, 0), (.11 - 1e-9, 0)], specification)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertFalse(result["execution_complete"])

    def test_eight_ulp_rounding_limit_is_bounded(self):
        specification = spec("max", end_s=.11)
        specification["expected_stop_s"] = .11
        endpoint = .11
        for _ in range(8):
            endpoint = math.nextafter(endpoint, 0)
        self.assertEqual(self.assess_rows([(0, 0), (endpoint, 0)], specification)["status"], "PASS")
        endpoint = math.nextafter(endpoint, 0)
        self.assertEqual(self.assess_rows([(0, 0), (endpoint, 0)], specification)["status"], "INCOMPLETE")

    def test_initial_tiny_time_does_not_synthesize_missing_zero_sample(self):
        result = self.assess_rows([(1e-12, 0), (1, 0)], spec())
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertTrue(any("start time" in issue for issue in result["completion_issues"]))

    def test_nonobject_receipt_and_boolean_exit_code_are_rejected(self):
        with self.assertRaises(ValueError):
            assess([], spec())
        write_raw(self.path, [(0, 0), (1, 0)])
        self.assertEqual(assess({**self.receipt, "returncode": False}, spec())["status"], "INCOMPLETE")

    def test_malformed_spec_rejected_before_assessment(self):
        cases = [spec(max=math.nan), spec(polarity=0), spec(end_s=2), spec(min=2, max=1),
                 spec(unknown=True), spec("above_duration")]
        duplicate = spec()
        duplicate["checks"].append(copy.deepcopy(duplicate["checks"][0]))
        cases.append(duplicate)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                assess(self.receipt, value)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.result = {"execution_complete": True, "expectation_met": True, "spec_sha256": "same",
                       "checks": [{"name": "charge", "value": 1e-6, "unit": "C"}]}
        self.tolerances = {"coarse_max_step_s": 1e-5, "fine_max_step_s": 5e-6,
                           "checks": {"charge": {"abs_tol": 1e-8, "rel_tol": .01}}}

    def test_absolute_and_relative_tolerance(self):
        fine = copy.deepcopy(self.result)
        fine["checks"][0]["value"] = 1.005e-6
        self.assertEqual(compare_results(self.result, fine, self.tolerances)["status"], "STABLE")
        fine["checks"][0]["value"] = 2e-6
        self.assertEqual(compare_results(self.result, fine, self.tolerances)["status"], "UNSTABLE")

    def test_incomplete_failed_or_mismatched_spec_cannot_be_stable(self):
        for changed in ({"execution_complete": False}, {"expectation_met": False}, {"spec_sha256": "different"}):
            with self.subTest(changed=changed):
                output = compare_results(self.result, {**self.result, **changed}, self.tolerances)
                self.assertEqual(output["status"], "INCOMPLETE")

    def test_every_metric_needs_tolerance_and_half_step_is_declared(self):
        output = compare_results(self.result, {**self.result, "checks": []}, self.tolerances)
        self.assertEqual(output["status"], "INCOMPLETE")
        with self.assertRaises(ValueError):
            compare_results(self.result, self.result, {**self.tolerances, "fine_max_step_s": 1e-5})
        with self.assertRaises(ValueError):
            compare_results(self.result, self.result, {**self.tolerances, "coarse_max_step_s": 5e-324, "fine_max_step_s": 0})
        bad = copy.deepcopy(self.tolerances)
        bad["checks"]["charge"]["abs_tol"] = -1
        with self.assertRaises(ValueError):
            compare_results(self.result, self.result, bad)

    def test_malformed_or_duplicate_metric_inputs_rejected(self):
        duplicate = copy.deepcopy(self.result)
        duplicate["checks"].append(copy.deepcopy(duplicate["checks"][0]))
        for bad in ([], {**self.result, "checks": None}, duplicate):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                compare_results(bad, self.result, self.tolerances)

    def test_overflowing_difference_or_tolerance_is_incomplete_not_stable(self):
        coarse = copy.deepcopy(self.result)
        fine = copy.deepcopy(self.result)
        coarse["checks"][0]["value"] = 1e308
        fine["checks"][0]["value"] = -1e308
        tolerance = copy.deepcopy(self.tolerances)
        tolerance["checks"]["charge"] = {"abs_tol": 0, "rel_tol": 1.8}
        result = compare_results(coarse, fine, tolerance)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertTrue(result["issues"])
        self.assertEqual(result["checks"], [])
        json.dumps(result, allow_nan=False)
        # A finite difference must not pass against an overflowed allowance either.
        result = compare_results(coarse, coarse, tolerance)
        self.assertEqual(result["status"], "INCOMPLETE")
        json.dumps(result, allow_nan=False)


class CornerTests(unittest.TestCase):
    def test_deterministic_bounded_generation_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "corners"
            result = generate_corners("R1 a 0 @@R@@\nC1 a 0 @@C@@\n", {"R": [1, 2], "C": [1e-6, 2e-6]}, path)
            self.assertEqual(len(result["cases"]), 4)
            self.assertNotIn("@@", (path / "case_0001.cir").read_text())
            self.assertEqual(json.loads((path / "manifest.json").read_text()), result)
            with self.assertRaises(FileExistsError):
                generate_corners("R1 a 0 @@R@@", {"R": [1]}, path)

    def test_nonfinite_duplicate_missing_or_excess_corners_rejected_without_writing(self):
        cases = [({"R": [math.inf]}, 256), ({"R": [1, 1]}, 256), ({"X": [1]}, 256), ({"R": [1, 2]}, 1)]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "corners"
            for parameters, maximum in cases:
                with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                    generate_corners("R1 a 0 @@R@@", parameters, path, max_cases=maximum)
                self.assertFalse(path.exists())

    def test_invalid_or_unmatched_placeholder_rejected_before_output_creation(self):
        templates = ["R1 a 0 @@R@@\nC1 a 0 @@typo@@\n", "R1 a 0 @@R@@\n* @@OPEN\n",
                     "R1 a 0 @@R@@\n* @@9BAD@@\n", "R1 a 0 @@R@@@@\n"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "corners"
            for template in templates:
                with self.subTest(template=template), self.assertRaises(ValueError):
                    generate_corners(template, {"R": [1000]}, path)
                self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
