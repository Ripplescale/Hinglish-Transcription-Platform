"""Local, optional-dependency ASR runtimes. Importing this package loads no models."""

from .api import inspect_capabilities, reset_runtime_cache, transcribe
from .assets import verify_conversion_provenance, verify_model_assets

__all__ = [
    "transcribe", "inspect_capabilities", "reset_runtime_cache",
    "verify_model_assets", "verify_conversion_provenance",
]
