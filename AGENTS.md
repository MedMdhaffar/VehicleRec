# Repository Guidelines

## Project Structure & Module Organization

This repository is a standalone computer-vision proof of concept. `compare_plate_embeddings.py` contains the three-image CLI workflow, `plate_preprocessing.py` implements rectangular resize/pad policies, `compare_preprocessing_methods.py` runs reproducibility comparisons, and `evaluate_plate_dataset.py` evaluates either category folders or the controlled manifest experiment. `plate_variants.py` owns colour/grayscale/CLAHE preparation and shared evaluation helpers. `evaluate_plate_representations.py` and `plate_representations.py` implement the separate R0–R3 representation experiment. `evaluate_encoder_colour_shape.py` and `plate_feature_branches.py` implement the frozen-encoder, colour-descriptor, and manual-marker shape experiment. `evaluate_arabic_marker_ocr.py` and `arabic_marker_ocr.py` implement the separate seven-crop Arabic recognition experiment. The older `annotate_plate_geometry.py` is retained for reproducibility but is not used by the current complete-crop experiment. `requirements.txt` lists runtime dependencies, and `README.md` documents setup and interpretation.

Sample `.jpg` files and `best.pt` are local experiment inputs. Treat audit/comparison output directories and `__pycache__/` as generated artifacts; do not build application logic around their contents. Focused checks live under `tests/`.

## Build, Test, and Development Commands

Use Python 3.10 or newer in an isolated environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m py_compile compare_plate_embeddings.py plate_preprocessing.py
python -m unittest discover -s tests -v
```

Run a cropped-image smoke test with:

```bash
python compare_plate_embeddings.py --cropped-plates \
  --image1 nt1.jpg --image2 nt2.jpg --image3 tunis1.jpg \
  --output-dir variable_outputs_run1
```

For full vehicle images, omit `--cropped-plates` and provide `--yolo-weights best.pt`. The first embedding run may download the configured Hugging Face model.

## Coding Style & Naming Conventions

Follow PEP 8, use four-space indentation, and retain type hints for public helpers. Use `snake_case` for functions and variables, `PascalCase` for classes, and uppercase names for module constants. Prefer `pathlib.Path`, small single-purpose functions, and actionable `PlateExperimentError` messages. Keep imports grouped as standard library, third-party packages, then local modules. No formatter or linter is currently configured; avoid unrelated reformatting.

## Testing Guidelines

There is no coverage threshold. For changes, run `py_compile`, the `unittest` suite, inspect `--help`, and exercise the affected CLI mode. Verify machine-readable results, comparison figures, clean crop files, tensor reconstructions, patch divisibility, and embedding dimensions. Name new tests `test_*.py` under `tests/`.

## Commit & Pull Request Guidelines

Git history is unavailable in this directory, so no existing commit convention can be inferred. Use short, imperative subjects such as `Validate output paths before inference`. Keep commits focused. Pull requests should explain the experiment scenario, commands run, and behavioral impact; link relevant issues and include representative output images when visualization changes. Do not commit credentials, model caches, virtual environments, or large generated outputs unless they are intentional review fixtures.
