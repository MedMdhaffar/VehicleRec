from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from vehicle_classification import app as vehicle_app
from plate_recognition import app as plate_app


class Upload:
    async def read(self):
        return b""


@pytest.mark.parametrize("module", [vehicle_app, plate_app])
def test_empty_upload_becomes_controlled_client_error(module):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(module._decode_image(Upload(), None))
    assert caught.value.status_code == 422


@pytest.mark.parametrize("module", [vehicle_app, plate_app])
def test_invalid_base64_becomes_controlled_client_error(module):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(module._decode_image(None, "%%%"))
    assert caught.value.status_code == 422
