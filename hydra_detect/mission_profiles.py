"""Mission profile presets — bundle behavior + approach + post-action."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MissionProfile:
    """A mission profile preset."""
    name: str
    display_name: str
    description: str
    behavior: str           # "follow", "drop", "strike"
    approach_method: str    # "gps_waypoint", "rc_override", "hybrid"
    post_action: str        # "SMART_RTL", "LOITER", "HOLD", "DOGLEG_RTL", "RTL"
    icon: str = ""          # emoji or icon name for dashboard


# Default profiles
DEFAULT_PROFILES: dict[str, MissionProfile] = {
    "recon": MissionProfile(
        name="recon",
        display_name="RECON",
        description="Track and follow target, return via breadcrumb path",
        behavior="follow",
        approach_method="gps_waypoint",
        post_action="SMART_RTL",
        icon="eye",
    ),
    "delivery": MissionProfile(
        name="delivery",
        display_name="DELIVERY",
        description="Approach target GPS, release payload at distance",
        behavior="drop",
        approach_method="gps_waypoint",
        post_action="SMART_RTL",
        icon="package",
    ),
    "strike": MissionProfile(
        name="strike",
        display_name="STRIKE",
        description="Continuous approach with two-stage arm",
        behavior="strike",
        approach_method="gps_waypoint",
        post_action="LOITER",
        icon="target",
    ),
}


def get_profiles() -> dict[str, MissionProfile]:
    """Return all available mission profiles."""
    return dict(DEFAULT_PROFILES)


def get_profile(name: str) -> MissionProfile | None:
    """Get a profile by name."""
    return DEFAULT_PROFILES.get(name.lower())


def get_vehicle_post_action(profile: MissionProfile, vehicle_type: str) -> str:
    """Get the post-action mode adjusted for vehicle type.

    Vehicle type affects the post-action:
    - DOGLEG_RTL is only available for drones; other vehicles fall back to SMART_RTL.
    - Strike post-action is HOLD for ground vehicles, LOITER for everything else.
    """
    vehicle_type = vehicle_type.lower() if vehicle_type else ""

    # Dogleg RTL only for drones
    if profile.post_action == "DOGLEG_RTL" and vehicle_type != "drone":
        return "SMART_RTL"

    # HOLD only for ground vehicles
    if profile.name == "strike":
        if vehicle_type == "ugv":
            return "HOLD"
        return "LOITER"

    return profile.post_action
