"""Targeted Hunt intelligence layer.

The package stores reusable company/person/contact intelligence independently
from the mass-apply pipeline.  Automated research and user-supplied facts use
the same data model, with explicit provenance and confidence.
"""

from .models import (
    Company,
    Contact,
    EntryPoint,
    IntelligenceSource,
    Note,
    Person,
    Relationship,
    TargetedHuntCase,
)

__all__ = [
    "Company",
    "Contact",
    "EntryPoint",
    "IntelligenceSource",
    "Note",
    "Person",
    "Relationship",
    "TargetedHuntCase",
]
