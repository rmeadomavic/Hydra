"""RTL-SDR raw power measurement client — works with FHSS radios.

Drop-in replacement for KismetClient when hunting frequency-hopping
radios (SiK, CRSF, ELRS) that rtl_433/Kismet can't decode.

Measures peak signal power across a frequency band using rtl_power.
"""

from __future__ import annotations

import logging
import os
import signal
import shutil
import subprocess

logger = logging.getLogger(__name__)


def _kill_proc(proc: subprocess.Popen) -> None:
    """Kill an rtl_power process reliably.

    rtl_power ignores SIGTERM while mid-scan, so we go straight to
    process-group SIGKILL after a brief grace period.
    """
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _start_rtl_power(cmd: list[str]) -> subprocess.Popen:
    """Start rtl_power in its own process group."""
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )


class RtlPowerClient:
    """Measures RF power using rtl_power from the rtl-sdr package.

    Implements the same interface as KismetClient so it can be used
    as a drop-in replacement in the hunt controller.

    Args:
        tolerance_mhz: Bandwidth to scan around the target frequency.
        step_khz: FFT bin size in kHz (smaller = finer resolution, slower).
    """

    def __init__(
        self,
        tolerance_mhz: float = 5.0,
        step_khz: float = 100.0,
    ):
        self._tolerance = tolerance_mhz
        self._step_khz = step_khz

    def check_connection(self) -> bool:
        """Return True if rtl_power is available and the dongle responds."""
        if shutil.which("rtl_power") is None:
            logger.error("rtl_power not found — install rtl-sdr package")
            return False
        # Quick test: one full scan of a small band
        result = self._scan_peak(435.0)
        return result is not None

    def get_rssi(
        self,
        *,
        mode: str = "sdr",
        bssid: str | None = None,
        freq_mhz: float | None = None,
    ) -> float | None:
        """Measure peak power near freq_mhz. Ignores mode/bssid."""
        if freq_mhz is None:
            return None
        return self._scan_peak(freq_mhz)

    def reset_auth(self) -> None:
        """No-op — rtl_power doesn't need authentication."""

    def close(self) -> None:
        """No-op — no persistent connection."""

    def __enter__(self) -> RtlPowerClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _scan_peak(self, center_mhz: float) -> float | None:
        """Scan a band around center_mhz and return peak power in dB."""
        start = center_mhz - self._tolerance
        stop = center_mhz + self._tolerance
        cmd = [
            "rtl_power",
            "-f", f"{start}M:{stop}M:{self._step_khz}k",
            "-1",
            "-",
        ]
        try:
            proc = _start_rtl_power(cmd)
        except FileNotFoundError:
            return None

        peak: float | None = None
        try:
            for line in proc.stdout:
                parts = line.strip().split(",")
                if len(parts) < 7:
                    continue
                for val in parts[6:]:
                    try:
                        db = float(val.strip())
                        if peak is None or db > peak:
                            peak = db
                    except ValueError:
                        continue
            # -1 flag means single sweep — process should exit on its own
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_proc(proc)
        except Exception:
            _kill_proc(proc)
            raise

        if peak is not None:
            logger.debug("rtl_power scan %.0f MHz: peak %.1f dB", center_mhz, peak)
        return peak
