"""
Algo E MVP Pipeline Exceptions

Exception classes for the MVP pipeline, one per stage so callers can
catch precisely what they need. All inherit from BaseError.

Change Log:
-----------
2026-08-18      Initialize
"""


class BaseError(Exception):
    """Base class for all Algo E pipeline errors."""


class ExtractionError(BaseError):
    """Data extraction failure (step 1)."""


class FeatureEngineeringError(BaseError):
    """Feature/preprocessing failure (step 2)."""


class PricingError(BaseError):
    """True probability / sell odds failure (step 4)."""


class ModelError(BaseError):
    """Unknown or mis-configured model (step 3 / 4)."""


class OptimizationError(BaseError):
    """TG/SUP optimizer failure (step 5)."""


class StoreError(BaseError):
    """SQLite output store failure (step 6)."""