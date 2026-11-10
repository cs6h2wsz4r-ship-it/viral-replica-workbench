"""Compatibility surface for official image edits; transports live in gateway."""
from __future__ import annotations
import time
import uuid
from pathlib import Path
from typing import Callable
from .gateway.errors import ImageError


def image_size(ratio: str) -> str:
    from .gateway.adapters.official import image_size as implementation
    return implementation(ratio)


def _multipart(preview: dict) -> tuple[bytes, str]:
    from .gateway.adapters.official import _multipart as implementation
    return implementation(preview)


def generate(preview: dict, output: Path, *, before_send: Callable[[], None] | None = None) -> dict:
    from . import gateway
    return gateway.image_generate(preview, output, before_send=before_send, _compat=True)
