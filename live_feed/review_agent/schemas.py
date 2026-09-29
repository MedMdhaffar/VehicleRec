from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# cam_id is a string now (dahua "4"/"1", rtsp "hik1"). coerce_numbers_to_str keeps
# old int-channel records (e.g. {"channel": 4}) loadable as "4".
_CAM_CFG = ConfigDict(coerce_numbers_to_str=True)


class ReviewInput(BaseModel):
    model_config = _CAM_CFG
    record_id: int
    channel: str          # cam_id ("4", "1", "hik1")
    track_id: int
    finalized_at: float
    vehicle_crop_path: str
    plate_crop_path: str | None = None
    # model predictions (to confirm / correct)
    vehicle_class_predicted: str
    colour_predicted: str
    ocr_predicted: str | None = None
    plate_type_predicted: str | None = None   # "tounes" | "other"
    # confidences (used to route and reconcile)
    type_conf: float = 0.0
    colour_conf: float = 0.0
    digits_conf: float = 0.0
    plate_type_conf: float = 0.0


class VehicleReview(BaseModel):
    vehicle_class: str = Field(description="confirmed or corrected vehicle class")
    colour: str = Field(description="confirmed or corrected colour")
    vehicle_maker: str = Field(
        description="brand only e.g. Kia, BMW, Renault; 'unknown' if unsure"
    )
    confidence: float = Field(ge=0, le=1, description="overall confidence 0..1")


class PlateReview(BaseModel):
    ocr: str = Field(description="confirmed or corrected plate digits; '' if unreadable")
    plate_type: str = Field(description="ONLY 'tounes' or 'other'")
    observed_category: str = Field(
        description=(
            "real plate nature for the DB: "
            "private/diplomatic/military/RS/taxi/transport/foreign/unknown — "
            "metadata only, does NOT change plate_type"
        )
    )
    confidence: float = Field(ge=0, le=1)


class HealthCheck(BaseModel):
    status: str = Field(description="echo the literal 'ok'")


class ReviewCheckpoint(BaseModel):
    model_config = _CAM_CFG
    channel: str
    last_record_id: int = 0
    updated_at: float = 0.0


class ReviewedRecord(BaseModel):
    model_config = _CAM_CFG
    record_id: int
    channel: str
    track_id: int
    vehicle: VehicleReview
    plate: PlateReview | None = None
    predicted: ReviewInput
    agreement: dict   # {"class": bool, "colour": bool, "ocr": bool, "plate_type": bool}
    routed_to: str    # "trusted" | "review_queue"
    reviewed_at: float
