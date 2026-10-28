# DINOv2 Input Audit

## Scope and status

This audit covers image selection and the exact tensor supplied to the existing `facebook/dinov2-small` model. It does not change `compare_plate_embeddings.py`, compute new similarities, benchmark models, or alter existing result folders.

Runtime verification succeeded for the locally available cropped inputs `nt1.jpg`, `nt2.jpg`, and `tunis1.jpg`. YOLO-path re-execution is blocked because the three source photos recorded as `image1.jpg`, `image2.jpg`, and `image3.jpg` are absent. The annotated `outputs/detection_*.jpg` files are generated, altered images and were not silently substituted. The saved cropped-mode result also references missing `204.jpg`; its other references, `outputs/30.jpg` and `outputs/plate_1.jpg`, exist.

## Confirmed current preprocessing

The current cached processor is `transformers.models.bit.image_processing_bit.BitImageProcessor`, loaded for `facebook/dinov2-small` at resolved model revision `ed25f3a31f01632728cabb09d1542f84ab7b0056`. Its effective sequence in the verified environment is:

1. The experiment reads BGR with OpenCV, converts it to RGB, and creates a PIL image. The processor's `do_convert_rgb=True` therefore preserves RGB.
2. Convert to a channels-first `uint8` tensor.
3. Resize the shorter edge to 256 while preserving aspect ratio. The longer dimension is `int(256 * long / short)`, so it is truncated toward zero. Transformers 5.15.0 maps processor resample value `3` to torchvision bicubic interpolation with antialiasing enabled.
4. Center-crop 224×224. Each positive crop offset is `int((resized_dimension - 224) / 2.0)`.
5. Produce `float32` RGB values equivalent to `(pixel / 255 - mean) / std`, with mean `[0.485, 0.456, 0.406]` and standard deviation `[0.229, 0.224, 0.225]`. Transformers performs rescaling and normalization as one fused operation.

Every audited output tensor was `[1, 3, 224, 224]`, `torch.float32`. The audit independently replayed the processor's actual stage methods and obtained a bit-exact tensor. That same tensor was passed to DINOv2, then the existing extraction logic—CLS token, float32 conversion, and L2 normalization—was exercised without computing similarities. Package versions and tensor hashes are in the [aggregate machine record](input_audit_cropped/audit_record.json).

## What reaches the model

| Input | Plate crop | Exact resized image | Exact center window (x1,y1,x2,y2) | Approximate retained box in crop | Approx. area retained |
|---|---:|---:|---:|---:|---:|
| `nt1.jpg` | 67×32 | 536×256 | (156,16,380,240) | (19.5,2.0,47.5,30.0) | 36.6% |
| `nt2.jpg` | 129×35 | 943×256 | (359,16,583,240) | (49.11,2.19,79.75,32.81) | 20.8% |
| `tunis1.jpg` | 244×61 | 1024×256 | (400,16,624,240) | (95.31,3.81,148.69,57.19) | 19.1% |

The resized dimensions and center windows are exact. The inverse rectangles are geometric approximations based on actual dimension ratios; bicubic sample coordinates and the interpolation kernel can draw from neighboring pixels just outside them.

Visual inspection supports these content findings:

- [`nt1` stages](input_audit_cropped/image_1/preprocessing_stages.png): the reconstruction contains a central numeric fragment. Leftmost digits and the right-hand Arabic category marking are outside the retained region.
- [`nt2` stages](input_audit_cropped/image_2/preprocessing_stages.png): only a central numeric fragment is visible. Most left-hand digits and the right-hand `ن ت` marking are absent from the reconstruction.
- [`tunis1` stages](input_audit_cropped/image_3/preprocessing_stages.png): the central `تونس` marker is visible, while both number groups (`105` and `395`) are outside the retained region.

All three inputs are upscaled, not downscaled, because their heights are below 256 pixels. Thus the dominant content loss is geometric horizontal center-cropping. Bicubic enlargement also smooths already low-resolution detail, but it cannot recover detail absent from the source; this is distinct from excluding digits or markers.

For the saved YOLO results, the code path is confirmed statically: YOLO selects the highest-confidence box, adds 5% padding per side, floors the top/left, ceils the bottom/right, clamps to the source, and sends the in-memory crop to the processor. The original source photos are required to recreate the exact crop pixels and tensor. Saved `plate_*.jpg` files are lossy JPEG encodings written before embedding and are not exact substitutes for those in-memory arrays.

## Figures versus model input

The current comparison figures accurately show the selected preprocessor crops, but not the final image content seen by DINOv2. They display each full wide plate; the processor supplies only a square center crop. The audit's `model_input_display_reconstruction.png` files come directly from the exact normalized tensors by reversing normalization and rescaling. They are explicitly display reconstructions: values are clipped to `[0,255]`, rounded to nearest integer, and saved as `uint8` PNG. No audited image required clipping, but rounding was applied.

Per-image machine records and overlays are available for [image 1](input_audit_cropped/image_1/audit_record.json), [image 2](input_audit_cropped/image_2/audit_record.json), and [image 3](input_audit_cropped/image_3/audit_record.json).

## Interpretation and reproducibility limits

Existing scores are cosine similarities between three normalized CLS embeddings for the pixels that survived preprocessing. They can rank those three embeddings for that run. They cannot establish category identity, classifier accuracy, generalization, statistical separation, or that cropping caused any particular score. Such a causal claim requires a controlled preprocessing comparison.

The dependency file is unpinned, and saved results do not record package versions, processor class, resolved model revision, or tensor hashes. Therefore the exact runtime used to produce historical scores is unavailable. The current audit used Python 3.11.15, Transformers 5.15.0, Torch 2.12.1, torchvision 0.27.1, Pillow 12.2.0, and only the local Hugging Face cache; it downloaded nothing. The active default shell Python lacks several declared dependencies, so the existing `ml` Conda environment was used.

## Recommended next comparisons (not implemented)

In a later controlled stage, hold the encoder, source crops, tensor dtype, normalization, and metric fixed while comparing: the current shortest-edge resize plus square center crop; aspect-preserving resize with padding/letterboxing; direct resize to the model size; and plate-aware wide crops or tiled embeddings. Record exact tensors and evaluate each policy on a labeled, leakage-controlled dataset. These are recommendations only; this audit implements none of them.

## Reproduce

The audit tool refuses to overwrite an existing output directory and defaults to local-cache-only model loading:

```bash
MPLCONFIGDIR=/tmp/matplotlib-audit TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 \
  /home/mohamed/miniconda3/envs/ml/bin/python audit_image_preprocessing.py \
  --cropped-plates --images nt1.jpg nt2.jpg tunis1.jpg \
  --output-dir input_audit_rerun
```

For future restored vehicle sources, omit `--cropped-plates`, pass those source paths to `--images`, and add `--yolo-weights best.pt`.
