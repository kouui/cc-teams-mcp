"""Pydantic model serialization helpers."""

from __future__ import annotations

import json

from pydantic import BaseModel


def model_to_json(model: BaseModel, *, indent: int | None = None) -> str:
    """Serialize a Pydantic model to JSON (camelCase aliases, no None values)."""
    if indent is None:
        return model.model_dump_json(by_alias=True, exclude_none=True)
    return json.dumps(model.model_dump(by_alias=True, exclude_none=True), indent=indent)
