# Third-Party Notices

## DINOv3

The plugin includes Meta's official DINOv3 implementation at `assets/vendor/dinov3` and the `dinov3_vits16_pretrain_lvd1689m-08c60483.pth` checkpoint at `assets/models/dinov3`. These are subject to the DINOv3 License Agreement. A copy from the pinned repository is retained at `assets/licenses/DINOV3_LICENSE.md`.

The package loads both from plugin-relative paths, never downloads model weights automatically, and contains no signed download URL.

Pinned source revision: `6876159a11b4df116f30f667f8c9888617df0751`.

## PaddleOCR and PaddlePaddle

The copied `arabic_PP-OCRv5_mobile_rec` inference assets are used through PaddleOCR/PaddlePaddle. The completed source experiment recorded the model license as Apache-2.0. Review the PaddleOCR project and model documentation before redistribution or product deployment:

- https://github.com/PaddlePaddle/PaddleOCR
- https://www.paddleocr.ai/main/en/version3.x/module_usage/text_recognition.html

