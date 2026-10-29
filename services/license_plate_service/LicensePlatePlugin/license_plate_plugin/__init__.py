"""Reusable Tunisian plate-category classification package."""

from .classifier import PlateCategoryClassifier
from .config import PluginConfig, load_config, load_default_config
from .errors import PluginError
from .parser import normalize_arabic_marker, parse_nt_evidence
from .reference_index import ReferenceIndex

__all__ = [
    "PlateCategoryClassifier",
    "PluginConfig",
    "PluginError",
    "ReferenceIndex",
    "load_config",
    "load_default_config",
    "normalize_arabic_marker",
    "parse_nt_evidence",
]

__version__ = "0.1.0"

