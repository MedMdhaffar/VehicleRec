"""Frozen R2 reference-index creation, persistence, compatibility, and matching."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .config import PluginConfig
from .dinov3 import DinoV3Encoder
from .errors import PluginError
from .manifest import load_reference_manifest
from .representations import VIEW_ORDER, VIEW_WEIGHTS, extract_r2
from .utils import canonical_sha256, json_ready, sha256_file


INDEX_SCHEMA_VERSION = 1


def compatibility_record(config: PluginConfig, encoder: DinoV3Encoder) -> dict[str, Any]:
    policy = config.policy
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "model_identifier": config.model_identifier,
        "checkpoint_filename": encoder.checkpoint.name,
        "checkpoint_sha256": encoder.checkpoint_sha256,
        "official_repository_revision": encoder.repo_revision,
        "patch_size_hw": [16, 16],
        "component_dimension": 384,
        "combined_dimension": 1536,
        "view_order": list(VIEW_ORDER),
        "view_weights": VIEW_WEIGHTS,
        "regions": {
            "left": "floor(0) to ceil(0.5W)",
            "centre": "floor(0.25W) to ceil(0.75W)",
            "right": "floor(0.5W) to ceil(W)",
        },
        "resolution_policy": {
            "target_area": policy.target_area,
            "max_long_edge": policy.max_long_edge,
            "max_patch_count": policy.max_patch_count,
            "padding_rgb": list(policy.padding_rgb),
            "interpolation": policy.interpolation,
            "rounding": "round-half-up",
            "odd_padding": "extra right or bottom",
        },
        "normalization": {
            "rgb": True,
            "rescale_factor": 1.0 / 255.0,
            "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225],
        },
    }


def deterministic_id_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


@dataclass
class ReferenceIndex:
    path: Path
    embeddings: np.ndarray
    references: list[dict[str, Any]]
    compatibility: dict[str, Any]
    compatibility_fingerprint: str

    @classmethod
    def build(
        cls,
        config: PluginConfig,
        encoder: DinoV3Encoder,
        output_path: Path | None = None,
    ) -> "ReferenceIndex":
        records, manifest = load_reference_manifest(config.reference_manifest)
        vectors, references = [], []
        for record in records:
            vector, metadata = extract_r2(record["image"], encoder, config.policy)
            vectors.append(vector)
            references.append(
                {
                    "id": record["id"],
                    "category": record["category"],
                    "path": str(record["path"]),
                    "sha256": record["sha256"],
                    "physical_plate_id": record.get("physical_plate_id"),
                    "group_id": record.get("group_id"),
                    "feature_metadata": metadata,
                }
            )
        embeddings = np.stack(vectors).astype(np.float32)
        compatibility = compatibility_record(config, encoder)
        fingerprint = canonical_sha256(compatibility)
        index = cls(
            path=(output_path or config.reference_index).resolve(),
            embeddings=embeddings,
            references=references,
            compatibility=compatibility,
            compatibility_fingerprint=fingerprint,
        )
        index.save(
            manifest_sha256=sha256_file(config.reference_manifest),
            manifest_payload=manifest,
        )
        return index

    def save(self, manifest_sha256: str, manifest_payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = self.path.with_suffix(".json")
        if self.path.exists() or metadata_path.exists():
            raise PluginError(f"Refusing to overwrite existing reference index: {self.path}")
        np.savez_compressed(self.path, embeddings=self.embeddings)
        metadata = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "compatibility": self.compatibility,
            "compatibility_fingerprint": self.compatibility_fingerprint,
            "reference_manifest_sha256": manifest_sha256,
            "reference_manifest": manifest_payload,
            "references": json_ready(self.references),
            "embedding_shape": list(self.embeddings.shape),
            "embedding_dtype": str(self.embeddings.dtype),
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(
        cls,
        path: Path,
        expected_compatibility: dict[str, Any] | None = None,
    ) -> "ReferenceIndex":
        path = path.resolve()
        metadata_path = path.with_suffix(".json")
        if not path.is_file() or not metadata_path.is_file():
            raise PluginError(f"Reference index or metadata is missing: {path}")
        try:
            with np.load(path, allow_pickle=False) as payload:
                embeddings = payload["embeddings"].astype(np.float32)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise PluginError(f"Cannot load reference index {path}: {exc}") from exc
        compatibility = metadata.get("compatibility")
        fingerprint = canonical_sha256(compatibility)
        if fingerprint != metadata.get("compatibility_fingerprint"):
            raise PluginError("Reference index compatibility fingerprint is corrupt.")
        if expected_compatibility is not None and compatibility != expected_compatibility:
            raise PluginError("Reference index is stale or incompatible with the active model/preprocessing configuration.")
        references = metadata.get("references")
        if not isinstance(references, list) or embeddings.shape != (len(references), 1536):
            raise PluginError(f"Invalid reference index dimensions: {embeddings.shape}")
        norms = np.linalg.norm(embeddings, axis=1)
        if not np.all(np.isfinite(embeddings)) or not np.allclose(norms, 1.0, atol=1e-5):
            raise PluginError("Reference index contains non-finite or non-normalized embeddings.")
        return cls(path, embeddings, references, compatibility, fingerprint)

    def match(
        self,
        query: np.ndarray,
        exclude_ids: Iterable[str] = (),
        exclude_groups: dict[str, Any] | None = None,
        tie_tolerance: float = 1e-12,
        expected_category: str | None = None,
    ) -> dict[str, Any]:
        if query.shape != (1536,) or not np.isfinite(query).all():
            raise PluginError("Query embedding must be a finite 1536-dimensional vector.")
        excluded = {str(value) for value in exclude_ids}
        eligible: list[int] = []
        for index, reference in enumerate(self.references):
            if str(reference["id"]) in excluded:
                continue
            if exclude_groups and any(
                exclude_groups.get(key) is not None
                and exclude_groups.get(key) == reference.get(key)
                for key in ("physical_plate_id", "group_id")
            ):
                continue
            eligible.append(index)
        if not eligible:
            raise PluginError("No eligible references remain after exclusions.")
        scores = self.embeddings @ query.astype(np.float32)
        best_score = max(float(scores[index]) for index in eligible)
        tied = [index for index in eligible if abs(float(scores[index]) - best_score) <= tie_tolerance]
        winner = min(tied, key=lambda index: deterministic_id_key(str(self.references[index]["id"])))
        category_max: dict[str, tuple[float, int]] = {}
        for index in eligible:
            category = str(self.references[index]["category"])
            score = float(scores[index])
            current = category_max.get(category)
            if current is None or score > current[0] + tie_tolerance or (
                abs(score - current[0]) <= tie_tolerance
                and deterministic_id_key(str(self.references[index]["id"]))
                < deterministic_id_key(str(self.references[current[1]]["id"]))
            ):
                category_max[category] = (score, index)
        predicted = str(self.references[winner]["category"])
        other_scores = [value[0] for category, value in category_max.items() if category != predicted]
        result: dict[str, Any] = {
            "visual_category": predicted,
            "nearest_reference_id": str(self.references[winner]["id"]),
            "nearest_reference_category": predicted,
            "nearest_similarity": best_score,
            "predicted_category_margin": None if not other_scores else best_score - max(other_scores),
            "tie_flag": len(tied) > 1
            or sum(abs(value[0] - best_score) <= tie_tolerance for value in category_max.values()) > 1,
            "eligible_reference_count": len(eligible),
        }
        if expected_category is not None:
            same = [(float(scores[i]), i) for i in eligible if self.references[i]["category"] == expected_category]
            different = [(float(scores[i]), i) for i in eligible if self.references[i]["category"] != expected_category]
            best_same = max(same, default=None)
            best_different = max(different, default=None)
            result.update(
                {
                    "best_same_category_reference_id": None if best_same is None else str(self.references[best_same[1]]["id"]),
                    "best_same_category_similarity": None if best_same is None else best_same[0],
                    "best_different_category_reference_id": None if best_different is None else str(self.references[best_different[1]]["id"]),
                    "best_different_category_similarity": None if best_different is None else best_different[0],
                    "correct_category_margin": None
                    if best_same is None or best_different is None
                    else best_same[0] - best_different[0],
                }
            )
        return result

