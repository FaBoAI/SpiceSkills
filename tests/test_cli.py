from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spice_skills.cli import main


class CliTests(unittest.TestCase):
    def test_invalid_acceptance_is_rejected_before_launching_simulator(self):
        with tempfile.TemporaryDirectory() as temp:
            specification = Path(temp) / "invalid.json"
            specification.write_text('{"schema_version": 1}')
            with patch("spice_skills.cli.run_spice") as runner, redirect_stderr(StringIO()):
                code = main(["run", "trusted.cir", str(specification), "--output-dir", temp])
            self.assertEqual(code, 2)
            runner.assert_not_called()

    def test_nonfinite_json_rejected_before_creating_corner_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "template.cir"
            source.write_text("R1 a 0 @@R@@\n")
            parameters = Path(temp) / "parameters.json"
            parameters.write_text('{"R": [NaN]}')
            destination = Path(temp) / "corners"
            with redirect_stderr(StringIO()):
                code = main(["corners", str(source), str(parameters), "--output-dir", str(destination)])
            self.assertEqual(code, 2)
            self.assertFalse(destination.exists())

    def test_missing_raw_is_incomplete_not_success_and_output_is_not_overwritten(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "rc" / "spec.json"
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp) / "receipt.json"
            receipt.write_text('{"raw_path": null}')
            destination = Path(temp) / "result.json"
            with redirect_stdout(StringIO()):
                code = main(["assess", str(receipt), str(example), "--output", str(destination)])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(destination.read_text())["status"], "INCOMPLETE")
            before = destination.read_bytes()
            with redirect_stderr(StringIO()):
                self.assertEqual(main(["assess", str(receipt), str(example), "--output", str(destination)]), 2)
            self.assertEqual(destination.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
