from dataclasses import dataclass
from collections import deque
from typing import Dict, Any, Tuple
import time

# ============================================================
# STATE
# ============================================================


'''

| Message             | PX4         | ArduPilot |
| ------------------- | ----------- | --------- |
| HEARTBEAT           | ✅ Always    | ✅ Always  |
| SYS_STATUS          | ✅ Yes       | ✅ Yes     |
| GPS_RAW_INT         | ✅ Yes       | ✅ Yes     |
| GLOBAL_POSITION_INT | ✅ Yes       | ✅ Yes     |
| LOCAL_POSITION_NED  | ✅ Yes       | ✅ Yes     |
| ATTITUDE            | ✅ Yes       | ✅ Yes     |
| VFR_HUD             | ⚠ Sometimes | ✅ Often   |



| Message                    | PX4                | ArduPilot        |
| -------------------------- | ------------------ | ---------------- |
| ATTITUDE_TARGET            | ❌ Only in OFFBOARD | ❌ Only in GUIDED |
| POSITION_TARGET_LOCAL_NED  | ❌ OFFBOARD only    | ❌ GUIDED only    |
| POSITION_TARGET_GLOBAL_INT | ❌ Rare             | ❌ Rare           |
| ATTITUDE_QUATERNION        | ⚠ Optional         | ⚠ Optional       |



| Message          | PX4           | ArduPilot                              |
| ---------------- | ------------- | -------------------------------------- |
| SERVO_OUTPUT_RAW | ❌ Not typical | ✅ Very common (especially Plane/Rover) |
| MISSION_CURRENT  | ✅ Yes         | ✅ Yes                                  |


'''

@dataclass
class StreamConfig:
    rate_hz: float
    next_send: float



@dataclass
class CommonState:
    # ------------------------
    # Time bases
    # ------------------------
    time_boot_ms: int = 0     # used by many messages
    time_usec: int = 0        # used by GPS_RAW_INT, SERVO_OUTPUT_RAW, etc.

    # ------------------------
    # HEARTBEAT
    # ------------------------
    hb_type: int = 2          # MAV_TYPE_QUADROTOR (example)
    hb_autopilot: int = 12    # MAV_AUTOPILOT_PX4 (example)
    base_mode: int = 0
    custom_mode: int = 0
    system_status: int = 4
    mavlink_version: int = 3

    # ------------------------
    # SYS_STATUS
    # ------------------------
    voltage_battery: int = 12000          # mV
    current_battery: int = -1             # cA (10*mA). -1 = not measured
    battery_remaining: int = 100           # %
    load: int = 400                       # 0..1000
    onboard_control_sensors_present: int = 0
    onboard_control_sensors_enabled: int = 0
    onboard_control_sensors_health: int = 0
    drop_rate_comm: int = 0
    errors_comm: int = 0
    errors_count1: int = 0
    errors_count2: int = 0
    errors_count3: int = 0
    errors_count4: int = 0

    # ------------------------
    # GPS_RAW_INT
    # ------------------------
    gps_time_usec: int = 0                 # can mirror time_usec
    gps_lat: int = 0                       # 1e7 deg
    gps_lon: int = 0                       # 1e7 deg
    gps_alt: int = 0                       # mm (MSL)
    gps_alt_ellipsoid: int = 0             # mm
    gps_fix_type: int = 3
    gps_eph: int = 100                     # cm
    gps_epv: int = 100                     # cm
    gps_vel: int = 0                       # cm/s
    gps_cog: int = 0                       # cdeg
    gps_satellites_visible: int = 10
    gps_h_acc: int = 0                     # mm
    gps_v_acc: int = 0                     # mm
    gps_vel_acc: int = 0                   # mm/s
    gps_hdg_acc: int = 0                   # cdeg
    gps_yaw: int = 0                       # cdeg (if used)

    # ------------------------
    # GLOBAL_POSITION_INT
    # ------------------------
    gpi_time_boot_ms: int = 0
    gpi_lat: int = 0
    gpi_lon: int = 0
    gpi_alt: int = 0                       # mm
    gpi_relative_alt: int = 0              # mm
    gpi_vx: int = 0                        # cm/s
    gpi_vy: int = 0                        # cm/s
    gpi_vz: int = 0                        # cm/s
    gpi_hdg: int = 0                       # cdeg

    # ------------------------
    # LOCAL_POSITION_NED //SKIP for now
    # ------------------------
    lpn_time_boot_ms: int = 0
    lpn_x: float = 0.0                     # meters
    lpn_y: float = 0.0
    lpn_z: float = 0.0                     # NED: down is +, up is -
    lpn_vx: float = 0.0                    # m/s
    lpn_vy: float = 0.0
    lpn_vz: float = 0.0

    # ------------------------
    # ATTITUDE
    # ------------------------
    att_time_boot_ms: int = 0
    roll: float = 0.0                      # rad
    pitch: float = 0.0
    yaw: float = 0.0
    rollspeed: float = 0.0                 # rad/s
    pitchspeed: float = 0.0
    yawspeed: float = 0.0

    # ------------------------
    # VFR_HUD
    # ------------------------
    vfr_airspeed: float = 0.0              # m/s
    vfr_groundspeed: float = 0.0           # m/s
    vfr_heading: int = 0                   # deg
    vfr_throttle: int = 0                  # %
    vfr_alt: float = 0.0                   # m
    vfr_climb: float = 0.0                 # m/s

    # ------------------------
    # ATTITUDE_QUATERNION || Skip for now
    # ------------------------
    aq_time_boot_ms: int = 0
    q1: float = 1.0
    q2: float = 0.0
    q3: float = 0.0
    q4: float = 0.0
    repr_offset_q: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    # ------------------------
    # ATTITUDE_TARGET || Skip for now
    # ------------------------
    at_time_boot_ms: int = 0
    at_type_mask: int = 0
    at_q: Tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    body_roll_rate: float = 0.0
    body_pitch_rate: float = 0.0
    body_yaw_rate: float = 0.0
    thrust: float = 0.0

    # ------------------------
    # POSITION_TARGET_LOCAL_NED
    # ------------------------
    ptl_time_boot_ms: int = 0
    ptl_coordinate_frame: int = 1
    ptl_type_mask: int = 0
    ptl_x: float = 0.0
    ptl_y: float = 0.0
    ptl_z: float = 0.0
    ptl_vx: float = 0.0
    ptl_vy: float = 0.0
    ptl_vz: float = 0.0
    ptl_afx: float = 0.0
    ptl_afy: float = 0.0
    ptl_afz: float = 0.0
    ptl_yaw: float = 0.0
    ptl_yaw_rate: float = 0.0

    # ------------------------
    # POSITION_TARGET_GLOBAL_INT
    # ------------------------
    ptg_time_boot_ms: int = 0
    ptg_coordinate_frame: int = 6
    ptg_type_mask: int = 0
    ptg_lat_int: int = 0
    ptg_lon_int: int = 0
    ptg_alt: float = 0.0
    ptg_vx: float = 0.0
    ptg_vy: float = 0.0
    ptg_vz: float = 0.0
    ptg_afx: float = 0.0
    ptg_afy: float = 0.0
    ptg_afz: float = 0.0
    ptg_yaw: float = 0.0
    ptg_yaw_rate: float = 0.0

    # ------------------------
    # MISSION_CURRENT
    # ------------------------
    mission_seq: int = 0
    mission_total: int = 0
    mission_state: int = 0
    mission_mode: int = 0

    # ------------------------
    # SERVO_OUTPUT_RAW
    # ------------------------
    servo_time_usec: int = 0
    servo_port: int = 0
    servo_raw: Tuple[int, ...] = (1500,) * 16  # servo1..servo16


# /////////// commands /////////////

# ============================
# HISTORY BUFFER (last 10 HB + last 10 TELEM + last 10 CMDS)
# ============================
class HistoryBuffer:
    def __init__(self, max_hb=10, max_telem=10, max_cmd=10):
        self.hb = deque(maxlen=max_hb)         # [{"ts":..., "base_mode":..., ...}]
        self.telem = deque(maxlen=max_telem)   # [{"ts":..., "name":..., "fields":{...}}]
        self.cmd = deque(maxlen=max_cmd)       # [{"ts":..., "command":..., "params":{...}}]

    def add_hb(self, state_obj) -> None:
        self.hb.append({
            "ts": time.time(),
            "hb_type": int(state_obj.hb_type),
            "hb_autopilot": int(state_obj.hb_autopilot),
            "base_mode": int(state_obj.base_mode),
            "custom_mode": int(state_obj.custom_mode),
            "system_status": int(state_obj.system_status),
            "mavlink_version": int(state_obj.mavlink_version),
        })

    def add_telem(self, name: str, fields: Dict[str, Any]) -> None:
        self.telem.append({
            "ts": time.time(),
            "name": str(name),
            "fields": dict(fields),
        })

    def add_cmd(self, command_id: int, params: Dict[str, Any]) -> None:
        self.cmd.append({
            "ts": time.time(),
            "command": int(command_id),
            "params": dict(params),
        })

    def snapshot(self) -> Dict[str, Any]:
        return {
            "last_heartbeat": list(self.hb),
            "last_telemetry": list(self.telem),
            "recent_commands": list(self.cmd),
        }