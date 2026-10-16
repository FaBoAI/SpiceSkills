"""Acceptance checks built on FaBoAI/KiCadMCP, without a second SPICE runner."""

from .verification import assess, compare_results, generate_corners

__all__ = ["assess", "compare_results", "generate_corners"]
