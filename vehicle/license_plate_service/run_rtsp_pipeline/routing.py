"""Local outcomes from validated number evidence."""
from dataclasses import dataclass

@dataclass(frozen=True)
class RouteDecision:
    route: str
    confidence: float
    reason: str


def route_plate(crop, result):
    if result.number and result.number.outcome == "recognized":
        return RouteDecision("private_local" if result.classification.final_category == "tunis" else "local",
                             result.ocr_confidence, "validated local registration evidence")
    return RouteDecision("unknown", 0.0, result.number.reason if result.number else "classification_failure")
