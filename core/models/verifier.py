from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class FieldVerification(BaseModel):
    field: str
    verified: bool
    evidence_quote: Optional[str] = None

