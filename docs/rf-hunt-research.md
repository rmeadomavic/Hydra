# RF Signal Hunting Research — Approaches & GitHub Projects

Research conducted 2026-03-29 for improving Hydra's RF hunt subsystem.

## Current Hydra Approach
- `rf/hunt.py`: State machine (IDLE→SCANNING→SEARCHING→HOMING→CONVERGED)
- `rf/navigator.py`: Gradient ascent — fly a step, measure RSSI, rotate if signal drops
- `rf/signal.py`: Sliding-window RSSI averaging
- Supports WiFi (BSSID via Kismet) and SDR (frequency via rtl_power)
- Lawnmower and spiral search patterns

## Key GitHub Projects

### Tier 1 — Directly Applicable

| Repo | Stars | Description |
|------|-------|-------------|
| [krakenrf/krakensdr_doa](https://github.com/krakenrf/krakensdr_doa) | 291 | Gold standard coherent SDR direction finding. 5 RTL-SDRs, MUSIC/Capon/Bartlett DoA algorithms, web UI, multi-station triangulation |
| [krakenrf/heimdall_daq_fw](https://github.com/krakenrf/heimdall_daq_fw) | 91 | DAQ firmware for KrakenSDR — coherent IQ capture with shared clock |
| [IQTLabs/BirdsEye](https://github.com/IQTLabs/BirdsEye) | 51 | **Most relevant.** RL-based RF target localization. Monte Carlo Tree Search + Deep Q-Learning to navigate toward RF emitters. Could replace GradientNavigator |
| [petotamas/pyArgus](https://github.com/petotamas/pyArgus) | 200 | Python antenna array processing — Bartlett, Capon, MUSIC DoA algorithms for ULA/UCA arrays. Pure Python, runs on Jetson |

### Tier 2 — Reference Implementations

| Repo | Stars | Description |
|------|-------|-------------|
| [fquitin/Wi_UAV_tx_localization](https://github.com/fquitin/Wi_UAV_tx_localization) | 17 | DJI drone + USRP SDR autonomous homing — closest analog to Hydra RF hunt |
| [gabiga7/pirdf](https://github.com/gabiga7/pirdf) | 18 | Raspberry Pi radio direction finder using 4 RTL-SDRs |
| [mossmann/pseudo-doppler](https://github.com/mossmann/pseudo-doppler) | 24 | Michael Ossmann's pseudo-Doppler direction finding with SDR |
| [tvrusso/DFLib](https://github.com/tvrusso/DFLib) | 7 | C++ bearing-intersection math for triangulation from multiple positions |

## Approaches Ranked by Bang-for-Buck

| Rank | Approach | Hardware | Accuracy | Effort |
|------|----------|----------|----------|--------|
| 1 | **Dual SDR (scan+home)** | 2nd RTL-SDR (~$30) | Same as now | Low |
| 2 | **Differential RSSI** | 2nd RTL-SDR + cable (~$50) | Coarse L/R bearing | Medium |
| 3 | **KrakenSDR coherent array** | KrakenSDR + 5 antennas (~$300) | 1-5° bearing | Medium-High |
| 4 | **Yagi on servo (yaw search)** | Yagi + servo (~$60) | 5-15° bearing | Medium |
| 5 | **Null-based triangulation** | Yagi + servo (same as #4) | 1-3° per fix | Medium |
| 6 | **BirdsEye RL navigator** | None (software) | Better path efficiency | High |

## Two-SDR Approach

**Option A — Simultaneous frequency monitoring (easiest):**
SDR 1 monitors target for RSSI homing. SDR 2 does wideband scan. Currently
these are sequential. Two SDRs let both run in parallel.

**Option B — Differential RSSI with spaced antennas:**
Two omnis on opposite sides of vehicle. Compare RSSI: left antenna stronger =
signal is left. Gives crude bearing without probe movements. Works well on the
Enforcer boat (wide hull for antenna separation).

**Option C — Coherent phase measurement (harder):**
Requires shared clock between dongles (rtlsdrblog/rtl-sdr-kerberos driver).
Computes angle of arrival from phase difference. Two channels = single-axis
bearing with 180° ambiguity.

## Directional Antenna Yaw Search

Mount Yagi on servo, rotate 360°, record RSSI at each angle. Peak = bearing.
**Advantages:** Gets bearing from single position (no probe flights needed).
**Disadvantages:** Mechanical servo adds weight/complexity/vibration sensitivity.
**Hybrid approach:** Station vehicle, yaw scan for coarse bearing, then gradient
ascent along that bearing for fine homing. Periodically re-scan to correct.

## Reverse Azimuth / Signal Null

Directional antenna nulls are sharper than peaks (-3dB beamwidth ~30° vs null
~5°). Find two nulls bracketing the main lobe — true bearing bisects them.
Take null bearings from 2-3 positions, intersect for fix.
DFLib repo implements the bearing-intersection math.
**Caveat:** Unreliable under ~50m (near-field effects). Switch to gradient
ascent for final approach.

## KrakenSDR for Hydra

- Single USB device with 5 coherent RTL-SDR channels
- 5-element circular array: ~20-30cm diameter for WiFi
- Easy to mount on Enforcer boat; weight constraint on drones
- pyArgus provides DoA math as pure Python — integrate directly without
  full KrakenSDR software stack
- Runs on Pi 4/5, should work on Jetson Orin Nano (ARM64, more compute)

## Recommendation

**Near-term:** Second RTL-SDR dongle for parallel scan + home ($30, low effort).
**Medium-term:** KrakenSDR for instantaneous bearing — gradient ascent becomes
gradient descent on known bearing error instead of blind exploration.
