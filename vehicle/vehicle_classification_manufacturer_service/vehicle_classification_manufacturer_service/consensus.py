"""Canonical scalar vehicle class consensus."""
from .classification import PUBLIC_VEHICLE_TYPES

def aggregate_vehicle_type(confidence_sums: dict[str, float], observation_counts: dict[str, int], total_observations: int) -> tuple[str, float]:
    """Production consensus-aware, deterministic vehicle-type aggregation."""
    if total_observations <= 0:
        return "unknown", 0.0
    known = [label for label in PUBLIC_VEHICLE_TYPES if label != "unknown" and observation_counts.get(label, 0) > 0]
    if not known:
        count = observation_counts.get("unknown", 0)
        return "unknown", round(confidence_sums.get("unknown", 0.0) / count if count else 0.0, 4)
    winner = min(known, key=lambda label: (-confidence_sums.get(label, 0.0), -observation_counts.get(label, 0), label))
    winner_sum, winner_count = confidence_sums[winner], observation_counts[winner]
    total_sum = sum(confidence_sums.get(label, 0.0) for label in PUBLIC_VEHICLE_TYPES)
    second_sum = max((confidence_sums.get(label, 0.0) for label in PUBLIC_VEHICLE_TYPES if label != winner), default=0.0)
    mean = winner_sum / winner_count
    confidence_share = winner_sum / total_sum if total_sum else 0.0
    observation_share = winner_count / total_observations
    margin = max(0.0, winner_sum - second_sum) / total_sum if total_sum else 0.0
    return winner, round(mean * (confidence_share + observation_share + margin) / 3.0, 4)


