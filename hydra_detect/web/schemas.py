"""Pydantic request models for the web API."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_PROMPTS = 20
MAX_PROMPT_LENGTH = 200
BSSID_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")


class LoginRequest(BaseModel):
    password: str


class PromptsRequest(BaseModel):
    prompts: list[str]

    @field_validator("prompts")
    @classmethod
    def _validate_prompts(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("prompts list must not be empty")
        if len(v) > MAX_PROMPTS:
            raise ValueError(f"max {MAX_PROMPTS} prompts allowed")
        cleaned: list[str] = []
        for p in v:
            pp = p.strip()
            if not pp:
                raise ValueError("prompts must not be empty or blank")
            cleaned.append(pp[:MAX_PROMPT_LENGTH])
        return cleaned


class ThresholdRequest(BaseModel):
    threshold: float = Field(ge=0.0, le=1.0)


class AlertClassesRequest(BaseModel):
    classes: list[str]


class VehicleModeRequest(BaseModel):
    mode: str


class TrackIdRequest(BaseModel):
    track_id: int


class StrikeRequest(BaseModel):
    track_id: int
    confirm: bool = False

    @model_validator(mode="after")
    def _validate_confirm(self) -> "StrikeRequest":
        if not self.confirm:
            raise ValueError("confirm must be true")
        return self


class BooleanToggleRequest(BaseModel):
    enabled: bool


class MavlinkVideoTuneRequest(BaseModel):
    width: int | None = Field(default=None, ge=40, le=320)
    height: int | None = Field(default=None, ge=30, le=240)
    quality: int | None = Field(default=None, ge=5, le=50)
    max_fps: float | None = Field(default=None, ge=0.1, le=5.0)


class RfStartRequest(BaseModel):
    mode: str | None = None
    target_bssid: str | None = None
    target_freq_mhz: float | None = Field(default=None, ge=1.0, le=6000.0)
    search_pattern: str | None = None
    search_area_m: float | None = Field(default=None, ge=10.0, le=2000.0)
    search_spacing_m: float | None = Field(default=None, ge=2.0, le=200.0)
    search_alt_m: float | None = Field(default=None, ge=3.0, le=120.0)
    rssi_threshold_dbm: float | None = Field(default=None, ge=-100.0, le=-10.0)
    rssi_converge_dbm: float | None = Field(default=None, ge=-90.0, le=0.0)
    gradient_step_m: float | None = Field(default=None, ge=1.0, le=50.0)

    @model_validator(mode="after")
    def _validate_mode_specific(self) -> "RfStartRequest":
        if self.mode and self.mode not in ("wifi", "sdr"):
            raise ValueError("mode must be 'wifi' or 'sdr'")
        if self.mode == "wifi" and not (self.target_bssid or "").strip():
            raise ValueError("target_bssid required for wifi mode")
        if self.target_bssid and not BSSID_RE.fullmatch(self.target_bssid.strip()):
            raise ValueError("target_bssid must be MAC format AA:BB:CC:DD:EE:FF")
        if self.search_pattern and self.search_pattern not in ("lawnmower", "spiral"):
            raise ValueError("search_pattern must be 'lawnmower' or 'spiral'")
        return self


class SetupSaveRequest(BaseModel):
    camera_source: str = "auto"
    serial_port: str = "/dev/ttyTHS1"
    vehicle_type: str = ""
    team_number: str = ""
    callsign: str = ""

    @field_validator("camera_source", "serial_port")
    @classmethod
    def _short_device_fields(cls, v: str) -> str:
        if len(v) > 200:
            raise ValueError("field too long")
        return v

    @field_validator("vehicle_type", "team_number")
    @classmethod
    def _short_fields(cls, v: str) -> str:
        if len(v) > 20:
            raise ValueError("field too long")
        return v

    @field_validator("callsign")
    @classmethod
    def _callsign_len(cls, v: str) -> str:
        if len(v) > 50:
            raise ValueError("callsign too long")
        return v

    @model_validator(mode="after")
    def _validate_vehicle_type(self) -> "SetupSaveRequest":
        if self.vehicle_type and self.vehicle_type not in ("drone", "usv", "ugv", "fw"):
            raise ValueError("vehicle_type must be drone, usv, ugv, or fw")
        return self


class ConfigBodyRequest(BaseModel):
    body: dict[str, Any]
