from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class ClassifyRequest(BaseModel):
    """Request body for JSON callers. Multipart callers use form fields directly."""
    image_b64: Optional[str] = None
    return_image_inline: bool = False
    include_person: bool = False
    conf: Optional[float] = None
