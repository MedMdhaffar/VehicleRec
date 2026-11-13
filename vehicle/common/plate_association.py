"""Reusable runtime boundary for the P1 pure association rule.

The contract policy is dependency-free; never import its validator/reference
fixture harness. Call with active same-frame vehicle boxes only. Parent-change
segmentation and labelled-video evaluation belong to P2b and later phases.
"""
from contracts.db1.association import associate_plate

__all__ = ["associate_plate"]
