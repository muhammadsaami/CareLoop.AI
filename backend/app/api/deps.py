"""
CareLoop AI — API Dependencies
"""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db

# Typed alias for injection — use this in route function signatures
DbSession = Annotated[Session, Depends(get_db)]
