"""Strict local model output; no generated confidence is accepted."""
import json
from dataclasses import asdict, dataclass
from .brands import REGIONAL_MANUFACTURERS
from .taxonomy import normalize_manufacturer

BODY_TYPES = ("sedan", "hatchback", "suv", "wagon", "coupe", "convertible",
              "van", "pickup", "bus", "truck", "motorcycle", "bicycle", "unknown")
BODY_ALIASES = {"sport_utility_vehicle": "suv", "station_wagon": "wagon",
                "pick_up": "pickup", "pickup_truck": "pickup", "minivan": "van"}

COLORS = ("white", "black", "red", "blue", "green", "yellow", "orange", "unknown")
COLOR_ALIASES = {
    "gray": "white", "grey": "white", "silver": "white", "brown": "black",
    "off_white": "white", "ivory": "white", "burgundy": "red", "maroon": "red",
    "navy": "blue", "olive": "green",
}

@dataclass(frozen=True)
class RecognitionResult:
    manufacturer: str = "unknown"
    vehicle_type: str = "unknown"
    reason: str = ""
    color: str = "unknown"
    raw_output: str = ""

    def to_dict(self):
        return asdict(self)

class ModelOutputError(ValueError):
    def __init__(self, text):
        super().__init__("invalid_model_json")
        self.raw_output = str(text)[:2048]

def parse_result(text):
    try:
        if not isinstance(text, str):
            raise ValueError()
        payload = text.strip()
        lines = payload.splitlines()
        if len(lines) >= 3 and lines[0].lower() in {"```", "```json"} and lines[-1] == "```":
            payload = "\n".join(lines[1:-1])
        data = json.loads(payload)
        if not isinstance(data, dict) or set(data) != {"manufacturer", "vehicle_type", "color"}:
            raise ValueError()
        if any(not isinstance(value, str) for value in data.values()):
            raise ValueError()
    except (ValueError, TypeError):
        raise ModelOutputError(text) from None
    brand = normalize_manufacturer(data["manufacturer"])
    body = data["vehicle_type"].strip().lower().replace("-", "_").replace(" ", "_")
    body = BODY_ALIASES.get(body, body)
    reasons = []
    if brand not in (*REGIONAL_MANUFACTURERS, "unknown"):
        brand = "unknown"
        reasons.append("unsupported_manufacturer")
    if body not in BODY_TYPES:
        body = "unknown"
        reasons.append("unsupported_body_type")
    color = data["color"].strip().lower().replace("-", "_").replace(" ", "_")
    color = COLOR_ALIASES.get(color, color)
    if color not in COLORS:
        color = "unknown"
        reasons.append("unsupported_color")
    return RecognitionResult(brand, body, reason=";".join(reasons), color=color, raw_output=text[:2048])
