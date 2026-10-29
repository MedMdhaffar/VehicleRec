import unittest

import numpy as np
import torch
from PIL import Image

from dinov3_official_adapter import MetaDinoV3Processor, OfficialDinoV3Wrapper


class _FakeOfficial(torch.nn.Module):
    embed_dim = 4

    def forward_features(self, pixels):
        batch = pixels.shape[0]
        patches = (pixels.shape[-2] // 16) * (pixels.shape[-1] // 16)
        return {
            "x_norm_clstoken": torch.ones(batch, 4),
            "x_norm_regtokens": torch.full((batch, 4, 4), 9.0),
            "x_norm_patchtokens": torch.full((batch, patches, 4), 2.0),
        }


class OfficialAdapterTests(unittest.TestCase):
    def test_registers_are_excluded_and_rectangular_grid_is_preserved(self):
        model = OfficialDinoV3Wrapper(_FakeOfficial(), 16, 4)
        output = model(torch.zeros(1, 3, 32, 48)).last_hidden_state
        self.assertEqual(list(output.shape), [1, 7, 4])  # CLS + 2x3 patches only
        self.assertEqual(model.last_register_count, 4)
        self.assertFalse(torch.any(output == 9.0))

    def test_processor_preserves_geometry_and_returns_float32(self):
        image = Image.fromarray(np.zeros((19, 31, 3), dtype=np.uint8), "RGB")
        tensor = MetaDinoV3Processor()(image, return_tensors="pt")["pixel_values"]
        self.assertEqual(list(tensor.shape), [1, 3, 19, 31])
        self.assertEqual(tensor.dtype, torch.float32)
        self.assertTrue(torch.isfinite(tensor).all())


if __name__ == "__main__":
    unittest.main()
