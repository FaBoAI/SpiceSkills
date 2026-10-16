---
name: spice-verification
description: Plan and assess transient SPICE verification with predefined waveform requirements, negative controls, corner sweeps, and timestep convergence using FaBoAI/KiCadMCP. Use for simulation evidence and model limitations, not PCB routing or hardware certification.
---

# SPICE verification

Define the expected behavior and numeric bounds before running the circuit. Save that specification with the netlist, model versions, solver settings, and results. Use `kicad_mcp.spice.run_spice` for execution and `kicad_mcp.raw.read_raw` / `summarize_raw` for RAW handling; this repository adds acceptance checks rather than another runner or parser.

Separate three questions in every report:

- Did the simulator reach the requested stop time with finite, monotonic time data and no fatal error? A zero exit code is insufficient.
- Did a completed run meet its predefined numeric bounds? A negative control that behaves as expected is `NEGATIVE_CONTROL_CONFIRMED`, not a protection pass.
- Does the model include the physical effect the claim requires? An average model omitting reverse current cannot establish zero battery charging. Numerical completion does not establish hardware safety.

Use the repository's `spice-verify` CLI to preserve these distinctions. Read [the acceptance schema](references/acceptance.md) when constructing a specification or choosing metrics. For adaptive timesteps, use time-weighted means and integrate the actual saved time intervals. State the current sign and exact post-event window; distinguish a short turn-off tail from sustained retransmission.

Choose a bounded set of corners that exercises the claimed behavior: nominal, relevant threshold tolerances, weak source / source recovery, load and capacitance limits, and a negative control where appropriate. Re-run critical cases with half the maximum timestep and compare explicitly tolerated metrics. Read [検証ノウハウ](references/knowhow-ja.md) for corner selection, convergence limitations, graph reporting, and causal replay of an actual C control core.

Keep manufacturer model redistribution terms and project confidentiality separate from reusable tools. This repository's examples use synthetic passive circuits only. Do not copy private CAD, firmware, keys, project paths, or restricted model files into a reusable skill package.
