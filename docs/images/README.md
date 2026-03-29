# Screenshot Capture Checklist

Capture these screenshots from a running Hydra instance for documentation.
Use a browser at 1280x800 minimum. Crop to the relevant UI area.
Save as PNG with descriptive filenames matching the list below.

## Dashboard

- [ ] dashboard-ops.png -- Operations tab with active detections and track list visible
- [ ] dashboard-ops-locked.png -- Operations tab with a target locked (green corner brackets)
- [ ] dashboard-ops-strike.png -- Operations tab during active strike (red brackets, STRIKE badge)
- [ ] dashboard-settings.png -- Settings tab with config editor open
- [ ] dashboard-preflight-pass.png -- Pre-flight checklist overlay, all checks green
- [ ] dashboard-preflight-fail.png -- Pre-flight checklist with camera fail (red indicator)
- [ ] dashboard-stale-video.png -- VIDEO STALE overlay on the stream
- [ ] dashboard-low-light.png -- LOW LIGHT badge visible in the topbar
- [ ] dashboard-mobile.png -- Mobile control page (/control) on a phone-width viewport
- [ ] dashboard-instructor.png -- Instructor overview page (/instructor) with multiple vehicles

## OSD

- [ ] osd-statustext.png -- FPV goggles view showing STATUSTEXT OSD line
- [ ] osd-named-value.png -- FPV goggles view showing named_value Lua OSD layout
- [ ] osd-msp-displayport.png -- HDZero VTX OSD canvas with detection telemetry

## RF Hunt

- [ ] rf-hunt-idle.png -- RF hunt panel in IDLE state
- [ ] rf-hunt-searching.png -- RF hunt during SEARCHING with lawnmower pattern
- [ ] rf-hunt-homing.png -- RF hunt in HOMING state with RSSI graph rising
- [ ] rf-hunt-converged.png -- RF hunt CONVERGED with final RSSI reading

## TAK Integration

- [ ] tak-atak-markers.png -- ATAK showing detection markers on the map
- [ ] tak-atak-self-sa.png -- ATAK showing Hydra vehicle self-SA position
- [ ] tak-geochat-command.png -- ATAK GeoChat with a HYDRA LOCK command

## Review

- [ ] review-map.png -- Post-mission review page with detection markers on map
- [ ] review-timeline.png -- Event timeline with vehicle track and action markers
- [ ] review-filter.png -- Review page with confidence filter slider active

## Setup

- [ ] setup-wizard.png -- First-boot setup wizard (/setup) with device selection
- [ ] setup-wizard-complete.png -- Setup wizard after successful save

## Architecture

- [ ] arch-hardware.png -- Photo of Jetson mounted on vehicle with camera and Pixhawk visible
- [ ] arch-wiring-usv.png -- Wiring diagram for USV (Enforcer) setup
- [ ] arch-wiring-drone.png -- Wiring diagram for drone setup
