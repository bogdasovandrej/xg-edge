"""Experimental, leakage-safe models for national-team tournaments."""

from xgedge.international.fifa import (
    FIFA_RANKINGS_URL,
    FIFA_WORLD_CUP_CALENDAR_URL,
    load_fifa_fixtures,
    load_fifa_rankings,
)
from xgedge.international.model import WorldCupModel

__all__ = [
    "FIFA_RANKINGS_URL",
    "FIFA_WORLD_CUP_CALENDAR_URL",
    "WorldCupModel",
    "load_fifa_fixtures",
    "load_fifa_rankings",
]
