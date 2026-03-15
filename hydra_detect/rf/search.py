"""Search pattern generators for RF homing."""

from __future__ import annotations

import math


def generate_lawnmower(
    center_lat: float,
    center_lon: float,
    width_m: float = 100.0,
    height_m: float = 100.0,
    spacing_m: float = 20.0,
    alt: float = 15.0,
) -> list[tuple[float, float, float]]:
    """Generate a boustrophedon (lawnmower) search pattern.

    Returns waypoints as (lat, lon, alt) tuples.  The pattern starts at
    the south-west corner and snakes northward.
    """
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(center_lat))

    half_w = width_m / 2.0
    half_h = height_m / 2.0
    waypoints: list[tuple[float, float, float]] = []

    y = -half_h
    leg = 0
    while y <= half_h:
        if leg % 2 == 0:
            xs, xe = -half_w, half_w
        else:
            xs, xe = half_w, -half_w
        for x in (xs, xe):
            waypoints.append((
                center_lat + y / m_per_deg_lat,
                center_lon + x / m_per_deg_lon,
                alt,
            ))
        y += spacing_m
        leg += 1

    return waypoints


def generate_spiral(
    center_lat: float,
    center_lon: float,
    max_radius_m: float = 80.0,
    spacing_m: float = 15.0,
    points_per_rev: int = 12,
    alt: float = 15.0,
) -> list[tuple[float, float, float]]:
    """Generate an expanding spiral search pattern.

    Starts at center and spirals outward.  Good for re-search after
    signal loss (tighter spacing near last-known position).
    """
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(center_lat))

    waypoints: list[tuple[float, float, float]] = [(center_lat, center_lon, alt)]
    angle = 0.0
    step_angle = 2 * math.pi / points_per_rev
    # Archimedes spiral: r = a * theta  where a = spacing / (2*pi)
    a = spacing_m / (2 * math.pi)

    while True:
        angle += step_angle
        r = a * angle
        if r > max_radius_m:
            break
        dx = r * math.cos(angle)
        dy = r * math.sin(angle)
        waypoints.append((
            center_lat + dy / m_per_deg_lat,
            center_lon + dx / m_per_deg_lon,
            alt,
        ))

    return waypoints


def offset_position(
    lat: float, lon: float, bearing_deg: float, distance_m: float,
) -> tuple[float, float]:
    """Project a GPS point along a bearing by *distance_m* metres."""
    R = 6_371_000.0
    brng = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)

    lat2 = math.asin(
        math.sin(lat1) * math.cos(distance_m / R)
        + math.cos(lat1) * math.sin(distance_m / R) * math.cos(brng)
    )
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(distance_m / R) * math.cos(lat1),
        math.cos(distance_m / R) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)
