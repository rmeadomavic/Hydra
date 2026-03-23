--[[
  Hydra Detect — ArduPilot Lua OSD Script (v2)
  =============================================
  Reads detection telemetry from SCR_USER parameters (set by the Jetson
  companion computer via PARAM_SET MAVLink messages) and displays it on
  the FC OSD via gcs:send_text (shown by MSP DisplayPort, MAX7456, etc.).

  This version cycles between up to 3 display panels every ~1 second
  for a richer demo experience.

  No external Lua modules required.

  Installation:
    1. Copy this file to the FC SD card: APM/scripts/hydra_osd.lua
    2. Set these ArduPilot parameters and reboot:
         SCR_ENABLE    = 1
         SCR_HEAP_SIZE = 65536
         OSD_TYPE      = 5      (MSP DisplayPort HD) or 1 (MAX7456)
         OSD1_ENABLE   = 1
         OSD1_MESSAGE_EN = 1
    3. In Hydra config.ini set:
         [osd]
         enabled = true
         mode = named_value

  SCR_USER parameter mapping (set by Jetson, read by this script):
    SCR_USER1  — detection pipeline FPS
    SCR_USER2  — inference time in milliseconds
    SCR_USER3  — number of active tracks
    SCR_USER4  — locked track ID (-1 = no lock)
    SCR_USER5  — lock mode (0=none, 1=track, 2=strike)
    SCR_USER6  — top class ID (COCO class number of highest-confidence
                 detection, -1 if none)

  Python osd.py must set SCR_USER6 = top_class_id (not GPS fix type).
  See MIGRATION NOTES at the bottom of this file.
--]]

-- Pre-fetch parameter objects for fast access (avoids slow name lookup)
local SCR_USER1 = Parameter()
local SCR_USER2 = Parameter()
local SCR_USER3 = Parameter()
local SCR_USER4 = Parameter()
local SCR_USER5 = Parameter()
local SCR_USER6 = Parameter()

-- Bind to the actual parameters
SCR_USER1:init('SCR_USER1')
SCR_USER2:init('SCR_USER2')
SCR_USER3:init('SCR_USER3')
SCR_USER4:init('SCR_USER4')
SCR_USER5:init('SCR_USER5')
SCR_USER6:init('SCR_USER6')

-- ---------------------------------------------------------------
-- COCO class name lookup (common classes only, keeps memory low)
-- ---------------------------------------------------------------
local COCO_NAMES = {
    [0]  = "person",
    [1]  = "bicycle",
    [2]  = "car",
    [3]  = "motorcycle",
    [4]  = "airplane",
    [5]  = "bus",
    [6]  = "train",
    [7]  = "truck",
    [8]  = "boat",
    [9]  = "trafficlght",
    [10] = "hydrant",
    [11] = "stopsign",
    [12] = "meter",
    [13] = "bench",
    [14] = "bird",
    [15] = "cat",
    [16] = "dog",
    [17] = "horse",
    [18] = "sheep",
    [19] = "cow",
    [20] = "elephant",
    [21] = "bear",
    [22] = "zebra",
    [23] = "giraffe",
    [24] = "backpack",
    [25] = "umbrella",
    [26] = "handbag",
    [27] = "tie",
    [28] = "suitcase",
    [29] = "frisbee",
    [30] = "skis",
    [31] = "snowboard",
    [32] = "ball",
    [33] = "kite",
    [34] = "bat",
    [35] = "glove",
    [36] = "skateboard",
    [37] = "surfboard",
    [38] = "racket",
    [39] = "bottle",
    [40] = "wineglass",
    [41] = "cup",
    [42] = "fork",
    [43] = "knife",
    [44] = "spoon",
    [45] = "bowl",
    [46] = "banana",
    [47] = "apple",
    [48] = "sandwich",
    [49] = "orange",
    [50] = "broccoli",
    [51] = "carrot",
    [52] = "hotdog",
    [53] = "pizza",
    [54] = "donut",
    [55] = "cake",
    [56] = "chair",
    [57] = "couch",
    [58] = "plant",
    [59] = "bed",
    [60] = "table",
    [61] = "toilet",
    [62] = "tv",
    [63] = "laptop",
    [64] = "mouse",
    [65] = "remote",
    [66] = "keyboard",
    [67] = "phone",
    [68] = "microwave",
    [69] = "oven",
    [70] = "toaster",
    [71] = "sink",
    [72] = "fridge",
    [73] = "book",
    [74] = "clock",
    [75] = "vase",
    [76] = "scissors",
    [77] = "teddy",
    [78] = "dryer",
    [79] = "toothbrush",
}

--- Look up a COCO class name by ID.  Returns short string or "cls:N".
local function class_name(id)
    if id < 0 then return "---" end
    local n = COCO_NAMES[id]
    if n then return n end
    return string.format("cls:%.0f", id)
end

-- ---------------------------------------------------------------
-- State variables (updated from params each tick)
-- ---------------------------------------------------------------
local osd_fps     = 0.0
local osd_infms   = 0.0
local osd_trks    = 0
local osd_lkid    = -1
local osd_lkmod   = 0
local osd_clsid   = -1

-- Panel cycling state
local panel        = 1      -- current panel (1 or 2 or 3)
local panel_millis = 0      -- millis() when we last switched panel
local PANEL_HOLD_MS = 1000  -- hold each panel for ~1 second

-- Link health tracking
local ever_received = false
local last_rx       = 0     -- millis() of last non-zero fps reading
local STALE_TIMEOUT_MS = 3000

-- ---------------------------------------------------------------
-- Read SCR_USER parameters and update local state
-- ---------------------------------------------------------------
local function read_params()
    local fps = SCR_USER1:get()
    if fps == nil then return end

    osd_fps   = fps
    osd_infms = SCR_USER2:get() or 0.0
    osd_trks  = math.floor((SCR_USER3:get() or 0) + 0.5)
    osd_lkid  = math.floor((SCR_USER4:get() or -1) + 0.5)
    osd_lkmod = math.floor((SCR_USER5:get() or 0) + 0.5)
    osd_clsid = math.floor((SCR_USER6:get() or -1) + 0.5)

    if osd_fps > 0 then
        ever_received = true
        last_rx = millis():toint()
    end
end

-- ---------------------------------------------------------------
-- Panel display functions
-- ---------------------------------------------------------------

--- Panel 1: Detection summary
--- Example: "HYDRA: 2 targets | person"
---          "HYDRA: 0 targets"
local function panel_detection()
    local cls = class_name(osd_clsid)
    if osd_trks > 0 and osd_clsid >= 0 then
        return string.format("HYDRA: %.0f targets | %s", osd_trks, cls)
    else
        return string.format("HYDRA: %.0f targets", osd_trks)
    end
end

--- Panel 2: System / performance
--- Example: "HYDRA: 12fps 38ms | T:3"
local function panel_system()
    return string.format(
        "HYDRA: %.0ffps %.0fms | T:%.0f",
        osd_fps, osd_infms, osd_trks
    )
end

--- Panel 3: Lock status (only when a target is locked)
--- Example: "TGT LOCKED #5 person [TRACK]"
---          "TGT LOCKED #12 car [STRIKE]"
local function panel_lock()
    local mode_str = "[TRACK]"
    if osd_lkmod == 2 then mode_str = "[STRIKE]" end

    -- Resolve locked target class name — we only have the top class id,
    -- which may or may not be the locked target.  Good enough for demo.
    local cls = class_name(osd_clsid)

    return string.format(
        "TGT LOCKED #%.0f %s %s",
        osd_lkid, cls, mode_str
    )
end

-- ---------------------------------------------------------------
-- Panel cycling logic
-- ---------------------------------------------------------------
local function has_lock()
    return osd_lkid >= 0 and osd_lkmod > 0
end

local function next_panel(now)
    -- Only advance if enough time has elapsed
    if (now - panel_millis) < PANEL_HOLD_MS then
        return
    end
    panel_millis = now

    if has_lock() then
        -- Cycle 1 -> 2 -> 3 -> 1 ...
        panel = panel + 1
        if panel > 3 then panel = 1 end
    else
        -- No lock: alternate 1 <-> 2 (skip 3)
        if panel == 1 then
            panel = 2
        else
            panel = 1
        end
    end
end

-- ---------------------------------------------------------------
-- Main display update
-- ---------------------------------------------------------------
local function update_display()
    local now = millis():toint()

    -- Pre-link state
    if not ever_received then
        gcs:send_text(6, "HYDRA: WAITING")
        return
    end

    -- Stale detection — Jetson link lost
    if (now - last_rx) > STALE_TIMEOUT_MS then
        gcs:send_text(6, "HYDRA: NO LINK")
        return
    end

    -- Advance panel if it is time
    next_panel(now)

    -- If panel 3 selected but no lock, fall back to panel 1
    local active_panel = panel
    if active_panel == 3 and not has_lock() then
        active_panel = 1
    end

    local msg
    if active_panel == 1 then
        msg = panel_detection()
    elseif active_panel == 2 then
        msg = panel_system()
    else
        msg = panel_lock()
    end

    -- Enforce 50-char HD OSD limit
    gcs:send_text(6, string.sub(msg, 1, 50))
end

-- ---------------------------------------------------------------
-- Main update function — called every 1000ms (1 Hz)
-- ---------------------------------------------------------------
function update()
    read_params()
    update_display()
    return update, 1000
end

gcs:send_text(6, "Hydra OSD v2 loaded (3-panel cycle)")
return update, 1000  -- first call after 1 second

--[[
  ================================================================
  MIGRATION NOTES — changes required in Python osd.py
  ================================================================

  SCR_USER6 mapping has changed:
    OLD: SCR_USER6 = GPS fix type (float, 0=no fix, 3=3D)
    NEW: SCR_USER6 = top_class_id (COCO class number of the highest-
         confidence detection, -1 if no detections)

  GPS fix type is no longer sent to the OSD.  The Lua script does not
  need it — ArduPilot already has native GPS OSD elements (OSD1_GPSFIX).

  Required changes in osd.py _send_named_values():

  1. Replace the SCR_USER6 line:
       OLD:  self._set_param("SCR_USER6", float(state.gps_fix))
       NEW:  # Send top detection class ID (-1 if none)
             top_cls_id = -1.0
             for t in ???:  # Need access to track_result here
                 ...

     Since _send_named_values receives an OSDState, add a field to
     OSDState and set it in build_osd_state():

     a) Add field to OSDState dataclass:
          top_class_id: int = -1

     b) In build_osd_state(), after finding latest_label/latest_conf,
        also capture the class_id:
          top_class_id = -1
          for t in track_result:
              if t.confidence > latest_conf:
                  latest_conf = t.confidence
                  latest_label = t.label
                  top_class_id = t.class_id
        Then pass top_class_id=top_class_id to OSDState().

     c) In _send_named_values(), change:
          self._set_param("SCR_USER6", float(state.gps_fix))
        to:
          self._set_param("SCR_USER6", float(state.top_class_id))

  2. Update the docstring/comment block in _send_named_values() to
     reflect the new mapping:
       SCR_USER6 = top_class_id (COCO class #, -1 = no detections)

  No other files need changes.  The statustext and msp_displayport
  modes are unaffected.
  ================================================================
--]]
