"""Protocol defining the RSSI client interface for RF hunt."""

from __future__ import annotations

from typing import Protocol


class RSSIClient(Protocol):
    """Interface that KismetClient and RtlPowerClient both satisfy."""

    def check_connection(self) -> bool:
        ...

    def get_rssi(
        self,
        *,
        mode: str = "wifi",
        bssid: str | None = None,
        freq_mhz: float | None = None,
    ) -> float | None:
        ...

    def reset_auth(self) -> None:
        ...

    def close(self) -> None:
        ...
