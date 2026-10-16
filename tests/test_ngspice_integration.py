from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from kicad_mcp.spice import run_spice
from spice_skills import assess, compare_results


@unittest.skipUnless(shutil.which("ngspice"), "ngspice is not installed")
class NgspiceIntegrationTests(unittest.TestCase):
    def test_real_rc_waveforms_and_half_timestep(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "rc"
        template = (example / "lowpass.template.cir").read_text()
        specification = json.loads((example / "spec.json").read_text())
        tolerance = json.loads((example / "convergence.json").read_text())
        results = []
        with tempfile.TemporaryDirectory() as temp:
            for index, step in enumerate(("1e-5", "5e-6")):
                source = Path(temp) / f"rc_{index}.cir"
                source.write_text(template.replace("@@R_OHM@@", "1000").replace("@@C_F@@", "1e-6").replace("@@MAX_STEP_S@@", step))
                receipt = run_spice(str(source), engine="ngspice", timeout=10, output_dir=temp)
                self.assertTrue(receipt["completed"], receipt)
                result = assess(receipt, specification, binary_format="ngspice")
                self.assertEqual(result["status"], "PASS", result)
                results.append(result)
            stability = compare_results(*results, tolerance)
            self.assertEqual(stability["status"], "STABLE", stability)


if __name__ == "__main__":
    unittest.main()
