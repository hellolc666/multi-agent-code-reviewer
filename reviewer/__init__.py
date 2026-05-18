"""Multi-agent code reviewer package.

Suppresses one specific cosmetic warning at import time:

``langchain-core``'s structured-output transform parser emits a Pydantic
``UserWarning("Pydantic serializer warnings: PydanticSerializationUnexpectedValue
... [field_name='parsed', ...])`` when it serializes its internal
``_AllReturnType`` wrapper. The warning is harmless — the actual parsed value
is always correct — but it's noisy in CLI output. We filter it narrowly so
genuine Pydantic serialization issues still surface.
"""

from __future__ import annotations

import warnings

__version__ = "0.1.0"

warnings.filterwarnings(
    "ignore",
    message=r"^Pydantic serializer warnings:",
    category=UserWarning,
    module=r"pydantic\.main",
)
