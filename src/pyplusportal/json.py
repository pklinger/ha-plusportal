"""JSON coding tuned for billing-grade accuracy.

Consumption values are summed over hundreds of days and then multiplied by a
tariff, so binary floating point is not good enough: decoding every number as
``Decimal`` keeps the arithmetic exact all the way to the cent, and encoding
writes those decimals back out as bare JSON numbers rather than strings.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

#: Wraps a decimal while it passes through ``json.dumps``. The standard encoder
#: has no hook for emitting a custom *number*, only a custom string, so the
#: value is tagged on the way in and unquoted again on the way out.
_MARKER = "@@pyplusportal:decimal@@"

_TAGGED = re.compile(rf'"{re.escape(_MARKER)}(-?\d+(?:\.\d+)?)"')


def json_loads(payload: str | bytes) -> Any:
    """Decode JSON, representing every non-integer number as ``Decimal``."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    return json.loads(payload, parse_float=Decimal)


def _tag(value: Any) -> str:
    """Tag a decimal for later unquoting; reject anything else."""
    if isinstance(value, Decimal):
        return f"{_MARKER}{value:f}"
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_dumps(payload: Any, *, indent: int | None = None) -> str:
    """Encode JSON, writing ``Decimal`` values as exact, bare JSON numbers."""
    text = json.dumps(payload, indent=indent, ensure_ascii=False, default=_tag)
    return _TAGGED.sub(r"\1", text)
