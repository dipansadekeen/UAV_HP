# this code generates heartbeat from llm at the beginning then keeps sending heartbeat
# going to experiment with telemetry and commands

# comment 8 March : we will fix the heartbeat update issue first.
from dataclasses import dataclass, field
from typing import Tuple, List
import socket
import time
import threading
from pymavlink import mavutil

from dataclasses import dataclass
from typing import Dict
import threading

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Any, Optional, List
import threading
import requests
import json, os, re, math
from collections import defaultdict, deque

# ----------- RAG Integration -----
RAG_FILES = [
    "../RAGS/mavlink_rag_corpus.jsonl",
    "../out3/px4_command_sequences.jsonl",
    "../out3/px4_command_trace.jsonl",
]

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


# ============================
# RAG LOADER (command transitions)
# ============================
def load_command_rag_jsonl(path: str) -> List[Dict[str, Any]]:
    rows = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:
        print(f"[RAG] failed to load {path}: {e}", flush=True)
    print(f"[RAG] loaded {len(rows)} examples from {path}", flush=True)
    return rows


# def rag_retrieve_examples(rows: List[Dict[str, Any]], command_id: int, k: int = 3) -> List[Dict[str, Any]]:
#     out = []
#     for ex in rows:
#         cmd = ex.get("command", {})
#         ex_id = cmd.get("id") or cmd.get("command") or ex.get("command_id") or ex.get("cmd_id")
#         try:
#             if int(ex_id) == int(command_id):
#                 out.append(ex)
#         except Exception:
#             pass
#         if len(out) >= k:
#             break
#     return out

def rag_retrieve_examples(rows: List[Dict[str, Any]], command_id: int, k: int = 3) -> List[Dict[str, Any]]:
    out = []
    for ex in rows:
        if not isinstance(ex, dict):
            continue

        cmd = ex.get("command", {})

        # cmd can be dict OR int depending on jsonl format
        if isinstance(cmd, dict):
            ex_id = cmd.get("id") or cmd.get("command") or ex.get("command_id") or ex.get("cmd_id")
        else:
            # cmd is int/str
            ex_id = cmd

        try:
            if int(ex_id) == int(command_id):
                out.append(ex)
        except Exception:
            pass

        if len(out) >= k:
            break
    return out


# ============================
# ACK mapping (LLM string -> MAV_RESULT)
# ============================
ACK_RESULT_MAP = {
    "ACCEPTED": mavutil.mavlink.MAV_RESULT_ACCEPTED,
    "DENIED": mavutil.mavlink.MAV_RESULT_DENIED,
    "UNSUPPORTED": mavutil.mavlink.MAV_RESULT_UNSUPPORTED,
    "TEMPORARILY_REJECTED": mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED,
}
#  ////// commands ends ///////

# ============================================================
# TIME UPDATE FUNCTION (from your code)
# ============================================================

def update_time_fields(state: CommonState, boot_time: float):
    dt = max(0.0, time.monotonic() - boot_time)
    state.time_boot_ms = int(dt * 1000)
    state.time_usec = int(dt * 1_000_000.0)   # <-- ADDing this for telemetry GPS

# ============================================================
# HEARTBEAT BUILDER (from your code)
# ============================================================

def make_heartbeat(mav, state: CommonState):
    return mav.heartbeat_encode(
        int(state.hb_type),
        int(state.hb_autopilot),
        int(state.base_mode),
        int(state.custom_mode),
        int(state.system_status),
    )

def extract_json(text: str):
    """
    Extract first JSON object from LLM response.
    Handles cases where model adds extra text.
    """
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except Exception as e:
        print(f"[JSON EXTRACT ERROR] {e}")
        return None


# ////////// telemetry //////////
def make_sys_status(mav, s: CommonState):
    return mav.sys_status_encode(
        int(s.onboard_control_sensors_present),
        int(s.onboard_control_sensors_enabled),
        int(s.onboard_control_sensors_health),
        int(s.load),
        int(s.voltage_battery),
        int(s.current_battery),
        int(s.battery_remaining),
        int(s.drop_rate_comm),
        int(s.errors_comm),
        int(s.errors_count1),
        int(s.errors_count2),
        int(s.errors_count3),
        int(s.errors_count4),
    )

def make_gps_raw_int(mav, s: CommonState):
    return mav.gps_raw_int_encode(
        int(s.time_usec),
        int(s.gps_fix_type),
        int(s.gps_lat),
        int(s.gps_lon),
        int(s.gps_alt),
        int(s.gps_eph),
        int(s.gps_epv),
        int(s.gps_vel),
        int(s.gps_cog),
        int(s.gps_satellites_visible),
    )

def make_global_position_int(mav, s: CommonState):
    return mav.global_position_int_encode(
        int(s.time_boot_ms),
        int(s.gpi_lat),
        int(s.gpi_lon),
        int(s.gpi_alt),
        int(s.gpi_relative_alt),
        int(s.gpi_vx),
        int(s.gpi_vy),
        int(s.gpi_vz),
        int(s.gpi_hdg),
    )

def make_attitude(mav, s: CommonState):
    return mav.attitude_encode(
        int(s.time_boot_ms),
        float(s.roll),
        float(s.pitch),
        float(s.yaw),
        float(s.rollspeed),
        float(s.pitchspeed),
        float(s.yawspeed),
    )

def make_vfr_hud(mav, s: CommonState):
    return mav.vfr_hud_encode(
        float(s.vfr_airspeed),
        float(s.vfr_groundspeed),
        int(s.vfr_heading),
        int(s.vfr_throttle),
        float(s.vfr_alt),
        float(s.vfr_climb),
    )

# new
def make_battery_status(mav, s: CommonState):
    return mav.battery_status_encode(
        0,
        0,
        0,
        0,
        [4050, 4050, 4050, 4050, 65535, 65535, 65535, 65535, 65535, 65535],
        -1,
        -1,
        -1,
        int(s.battery_remaining)   # 👈 reuse existing value
    )

TELEM_BUILDERS = {
    "SYS_STATUS": make_sys_status,
    "GPS_RAW_INT": make_gps_raw_int,
    "GLOBAL_POSITION_INT": make_global_position_int,
    "ATTITUDE": make_attitude,
    "VFR_HUD": make_vfr_hud,
    "BATTERY_STATUS": make_battery_status, #new
}
# ////////// telemetry //////////

# ////////////// for commands /////////////
def load_rag_transitions(self, path: str) -> None:
    self.rag_transitions = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                self.rag_transitions.append(json.loads(line))
            except Exception:
                continue

def rag_retrieve(self, command_id: int, k: int = 3) -> list:
    # simplest: filter by command id/name field that exists in your jsonl
    out = []
    for ex in getattr(self, "rag_transitions", []):
        cmd = ex.get("command", {})
        # adjust this based on your dataset field names:
        ex_id = cmd.get("id") or cmd.get("command") or cmd.get("name")
        if ex_id == command_id:
            out.append(ex)
        if len(out) >= k:
            break
    return out

#  /////////// commands //////////


# retrieval for commands////////
def extract_heartbeat_transition_examples(rows: List[Dict[str, Any]], command_id: int, k: int = 2) -> List[Dict[str, Any]]:
    """
    Extract command-centered heartbeat transition examples:
      prev HEARTBEAT -> COMMAND_LONG -> optional COMMAND_ACK -> next HEARTBEAT
    """

    out = []
    n = len(rows)

    def msg_type(ex: Dict[str, Any]) -> str:
        return str(ex.get("mavpackettype") or ex.get("_type") or "").upper()

    def is_heartbeat(ex: Dict[str, Any]) -> bool:
        return msg_type(ex) == "HEARTBEAT"

    def is_command_long(ex: Dict[str, Any], cmd_id: int) -> bool:
        if msg_type(ex) != "COMMAND_LONG":
            return False
        try:
            return int(ex.get("command", -1)) == int(cmd_id)
        except Exception:
            return False

    def is_command_ack(ex: Dict[str, Any], cmd_id: int) -> bool:
        if msg_type(ex) != "COMMAND_ACK":
            return False
        try:
            return int(ex.get("command", -1)) == int(cmd_id)
        except Exception:
            return False

    def compact_heartbeat(ex: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "base_mode": ex.get("base_mode"),
            "custom_mode": ex.get("custom_mode"),
            "system_status": ex.get("system_status"),
            "type": ex.get("type"),
            "autopilot": ex.get("autopilot"),
            "mavlink_version": ex.get("mavlink_version"),
            "_ts": ex.get("_ts"),
        }

    def compact_command(ex: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "command": ex.get("command"),
            "param1": ex.get("param1"),
            "param2": ex.get("param2"),
            "param3": ex.get("param3"),
            "param4": ex.get("param4"),
            "param5": ex.get("param5"),
            "param6": ex.get("param6"),
            "param7": ex.get("param7"),
            "_ts": ex.get("_ts"),
        }

    def compact_ack(ex: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "command": ex.get("command"),
            "result": ex.get("result"),
            "progress": ex.get("progress"),
            "result_param2": ex.get("result_param2"),
            "_ts": ex.get("_ts"),
        }

    for i, ex in enumerate(rows):
        if not is_command_long(ex, command_id):
            continue

        prev_hb = None
        next_hb = None
        ack = None

        # nearest previous heartbeat
        for j in range(i - 1, -1, -1):
            if is_heartbeat(rows[j]):
                prev_hb = compact_heartbeat(rows[j])
                break

        # nearest next ack and next heartbeat
        for j in range(i + 1, n):
            if ack is None and is_command_ack(rows[j], command_id):
                ack = compact_ack(rows[j])

            if next_hb is None and is_heartbeat(rows[j]):
                next_hb = compact_heartbeat(rows[j])
                break

        out.append({
            "command": compact_command(ex),
            "prev_heartbeat": prev_hb,
            "ack": ack,
            "next_heartbeat": next_hb,
        })

        if len(out) >= k:
            break

    return out


# def retrieve_heartbeat_examples_from_sequences(rows: List[Dict[str, Any]], command_id: int, k: int = 2) -> List[Dict[str, Any]]:
#     """
#     Retrieve heartbeat transition examples from px4_command_sequences.jsonl.

#     Expected sequence row structure:
#       {
#         "context_prev_heartbeat": {...},
#         "request": {...},
#         "ack": {...},
#         "followups": [...]
#       }
#     """

#     out = []

#     def compact_hb(hb: Dict[str, Any]) -> Optional[Dict[str, Any]]:
#         if not isinstance(hb, dict):
#             return None
#         return {
#             "base_mode": hb.get("base_mode"),
#             "custom_mode": hb.get("custom_mode"),
#             "system_status": hb.get("system_status"),
#             "type": hb.get("type"),
#             "autopilot": hb.get("autopilot"),
#             "mavlink_version": hb.get("mavlink_version"),
#             "_ts": hb.get("_ts"),
#         }

#     def compact_request(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
#         if not isinstance(req, dict):
#             return None
#         return {
#             "command": req.get("command"),
#             "param1": req.get("param1"),
#             "param2": req.get("param2"),
#             "param3": req.get("param3"),
#             "param4": req.get("param4"),
#             "param5": req.get("param5"),
#             "param6": req.get("param6"),
#             "param7": req.get("param7"),
#             "_ts": req.get("_ts"),
#         }

#     def compact_ack(ack: Dict[str, Any]) -> Optional[Dict[str, Any]]:
#         if not isinstance(ack, dict):
#             return None
#         return {
#             "command": ack.get("command"),
#             "result": ack.get("result"),
#             "progress": ack.get("progress"),
#             "result_param2": ack.get("result_param2"),
#             "_ts": ack.get("_ts"),
#         }

#     def first_followup_heartbeat(followups: Any) -> Optional[Dict[str, Any]]:
#         if not isinstance(followups, list):
#             return None
#         for item in followups:
#             if not isinstance(item, dict):
#                 continue
#             mtype = str(item.get("mavpackettype") or item.get("_type") or "").upper()
#             if mtype == "HEARTBEAT":
#                 return compact_hb(item)
#         return None

#     for row in rows:
#         if not isinstance(row, dict):
#             continue

#         req = row.get("request", {})
#         try:
#             req_cmd = int(req.get("command", -1))
#         except Exception:
#             continue

#         if req_cmd != int(command_id):
#             continue

#         ex = {
#             "command": compact_request(req),
#             "prev_heartbeat": compact_hb(row.get("context_prev_heartbeat")),
#             "ack": compact_ack(row.get("ack")),
#             "next_heartbeat": first_followup_heartbeat(row.get("followups", [])),
#         }

#         out.append(ex)

#         if len(out) >= k:
#             break

#     return out
def retrieve_heartbeat_examples_from_sequences(rows: List[Dict[str, Any]], command_id: int, k: int = 2) -> List[Dict[str, Any]]:
    out = []

    def compact_hb(hb: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(hb, dict):
            return None
        return {
            "base_mode": hb.get("base_mode"),
            "custom_mode": hb.get("custom_mode"),
            "system_status": hb.get("system_status"),
            "type": hb.get("type") or hb.get("mavpackettype") or hb.get("_type"),
            "autopilot": hb.get("autopilot"),
            "mavlink_version": hb.get("mavlink_version"),
            "_ts": hb.get("_ts"),
        }

    def compact_request(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(req, dict):
            return None
        return {
            "command": req.get("command"),
            "param1": req.get("param1"),
            "param2": req.get("param2"),
            "param3": req.get("param3"),
            "param4": req.get("param4"),
            "param5": req.get("param5"),
            "param6": req.get("param6"),
            "param7": req.get("param7"),
            "_ts": req.get("_ts"),
        }

    def compact_ack(ack: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(ack, dict):
            return None
        return {
            "command": ack.get("command"),
            "result": ack.get("result"),
            "progress": ack.get("progress"),
            "result_param2": ack.get("result_param2"),
            "_ts": ack.get("_ts"),
        }

    def first_followup_heartbeat(followups: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(followups, list):
            return None

        for item in followups:
            if not isinstance(item, dict):
                continue

            mtype = str(
                item.get("type") or
                item.get("mavpackettype") or
                item.get("_type") or
                ""
            ).upper()

            if mtype == "HEARTBEAT":
                return compact_hb(item)

        return None

    for row in rows:
        if not isinstance(row, dict):
            continue

        req = row.get("request", {})
        if not isinstance(req, dict):
            continue

        try:
            req_cmd = int(req.get("command", -1))
        except Exception:
            continue

        if req_cmd != int(command_id):
            continue

        ex = {
            "command": compact_request(req),
            "prev_heartbeat": compact_hb(row.get("context_prev_heartbeat")),
            "ack": compact_ack(row.get("ack")),
            "next_heartbeat": first_followup_heartbeat(row.get("followups", [])),
        }

        out.append(ex)

        if len(out) >= k:
            break

    return out
# retrieval for commands////////


# helpers: handling for telemetry//////////
TELEM_GROUPS = {

    "GLOBAL_POSITION_INT": [
        "lat",
        "lon",
        "alt",
        "relative_alt",
        "vx",
        "vy",
        "vz",
        "hdg",
    ],

    "ATTITUDE": [
        "roll",
        "pitch",
        "yaw",
    ],

    "VFR_HUD": [
        "groundspeed",
        "heading",
        "throttle",
        "alt",
        "climb",
    ],

    "SYS_STATUS": [
        "battery_remaining",
        "voltage_battery",
        "load",
    ],

    "GPS_RAW_INT": [
        "fix_type",
    ],
}
TELEM_GROUPS_SET = {k: set(v) for k, v in TELEM_GROUPS.items()}

INTERNAL_TO_CANONICAL = {
    "gpi_lat": ("GLOBAL_POSITION_INT", "lat"),
    "gpi_lon": ("GLOBAL_POSITION_INT", "lon"),
    "gpi_alt": ("GLOBAL_POSITION_INT", "alt"),
    "gpi_relative_alt": ("GLOBAL_POSITION_INT", "relative_alt"),
    "gpi_vx": ("GLOBAL_POSITION_INT", "vx"),
    "gpi_vy": ("GLOBAL_POSITION_INT", "vy"),
    "gpi_vz": ("GLOBAL_POSITION_INT", "vz"),
    "gpi_hdg": ("GLOBAL_POSITION_INT", "hdg"),

    "roll": ("ATTITUDE", "roll"),
    "pitch": ("ATTITUDE", "pitch"),
    "yaw": ("ATTITUDE", "yaw"),

    "vfr_groundspeed": ("VFR_HUD", "groundspeed"),
    "vfr_heading": ("VFR_HUD", "heading"),
    "vfr_throttle": ("VFR_HUD", "throttle"),
    "vfr_alt": ("VFR_HUD", "alt"),
    "vfr_climb": ("VFR_HUD", "climb"),

    "battery_remaining": ("SYS_STATUS", "battery_remaining"),
    "voltage_battery": ("SYS_STATUS", "voltage_battery"),
    "load": ("SYS_STATUS", "load"),

    "gps_fix_type": ("GPS_RAW_INT", "fix_type"),
}

CANONICAL_TO_INTERNAL = {
    ("GLOBAL_POSITION_INT", "lat"): "gpi_lat",
    ("GLOBAL_POSITION_INT", "lon"): "gpi_lon",
    ("GLOBAL_POSITION_INT", "alt"): "gpi_alt",
    ("GLOBAL_POSITION_INT", "relative_alt"): "gpi_relative_alt",
    ("GLOBAL_POSITION_INT", "vx"): "gpi_vx",
    ("GLOBAL_POSITION_INT", "vy"): "gpi_vy",
    ("GLOBAL_POSITION_INT", "vz"): "gpi_vz",
    ("GLOBAL_POSITION_INT", "hdg"): "gpi_hdg",

    ("ATTITUDE", "roll"): "roll",
    ("ATTITUDE", "pitch"): "pitch",
    ("ATTITUDE", "yaw"): "yaw",

    ("VFR_HUD", "groundspeed"): "vfr_groundspeed",
    ("VFR_HUD", "heading"): "vfr_heading",
    ("VFR_HUD", "throttle"): "vfr_throttle",
    ("VFR_HUD", "alt"): "vfr_alt",
    ("VFR_HUD", "climb"): "vfr_climb",

    ("SYS_STATUS", "battery_remaining"): "battery_remaining",
    ("SYS_STATUS", "voltage_battery"): "voltage_battery",
    ("SYS_STATUS", "load"): "load",

    ("GPS_RAW_INT", "fix_type"): "gps_fix_type",
}

def canonicalize_grouped_snapshot(snapshot: dict) -> dict:
    """
    Input example:
    {
        "GLOBAL_POSITION_INT": {...},
        "ATTITUDE": {...},
        "GPS_RAW_INT": {...}
    }

    Output:
    same grouped structure, but only allowed telemetry groups/fields.
    """
    if not isinstance(snapshot, dict):
        return {}

    out = {}

    for msg_name, fields in snapshot.items():
        if msg_name not in TELEM_GROUPS_SET:
            continue
        if not isinstance(fields, dict):
            continue

        allowed = TELEM_GROUPS_SET[msg_name]
        kept = {k: v for k, v in fields.items() if k in allowed}

        if kept:
            out[msg_name] = kept

    return out


def canonicalize_followup_message(msg: dict) -> dict:
    """
    Input example:
    {"type": "GLOBAL_POSITION_INT", "lat": ..., "lon": ..., "vx": ...}

    Output:
    {"GLOBAL_POSITION_INT": {"lat": ..., "lon": ..., "vx": ...}}
    """
    if not isinstance(msg, dict):
        return {}

    msg_type = msg.get("type") or msg.get("mavpackettype") or msg.get("_type")
    if msg_type not in TELEM_GROUPS_SET:
        return {}

    allowed = TELEM_GROUPS_SET[msg_type]
    kept = {k: v for k, v in msg.items() if k in allowed}

    if not kept:
        return {}

    return {msg_type: kept}

def canonicalize_internal_history_fields(fields: dict) -> dict:
    """
    Convert your live HistoryBuffer flat internal fields into grouped canonical format.
    """
    if not isinstance(fields, dict):
        return {}

    out = {}

    for k, v in fields.items():
        mapped = INTERNAL_TO_CANONICAL.get(k)
        if not mapped:
            continue

        msg_name, field_name = mapped
        if msg_name not in out:
            out[msg_name] = {}

        out[msg_name][field_name] = v

    return out


def translate_canonical_fields_to_internal(grouped_fields: dict) -> dict:
    """
    Input:
    {
        "GLOBAL_POSITION_INT": {"lat": ..., "vx": ...},
        "ATTITUDE": {"roll": ...}
    }

    Output:
    {
        "gpi_lat": ...,
        "gpi_vx": ...,
        "roll": ...
    }
    """
    if not isinstance(grouped_fields, dict):
        return {}

    out = {}

    for raw_msg_name, msg_fields in grouped_fields.items():
        msg_name = str(raw_msg_name).upper()

        if msg_name not in TELEM_GROUPS_SET:
            continue
        if not isinstance(msg_fields, dict):
            continue

        for field_name, value in msg_fields.items():
            key = (msg_name, field_name)
            internal_name = CANONICAL_TO_INTERNAL.get(key)
            if internal_name:
                out[internal_name] = value

    return out

def translate_canonical_series_to_internal(series: list) -> list:
    """
    Input:
    [
        {"dt": 0.0, "fields": {"GLOBAL_POSITION_INT": {"vx": 100}}},
        ...
    ]

    Output:
    [
        {"dt": 0.0, "fields": {"gpi_vx": 100}},
        ...
    ]
    """
    if not isinstance(series, list):
        return []

    out = []

    for step in series:
        if not isinstance(step, dict):
            continue

        dt = float(step.get("dt", 0.0))
        grouped_fields = step.get("fields", {})
        flat_fields = translate_canonical_fields_to_internal(grouped_fields)

        out.append({
            "dt": dt,
            "fields": flat_fields,
        })

    return out


# ///// Step 2 — retriever from cmd_transition.jsonl
def retrieve_telemetry_examples_from_cmd_transition(rows, command_id: int, k: int = 2):
    """
    Retrieve telemetry transition examples from cmd_transition.jsonl.

    Expected row keys:
      Prev_HB, Prev_Telemetry, Command, Command_ACK, NEXT_Telemetry

    Prev_Telemetry and NEXT_Telemetry are grouped telemetry snapshots, not lists.
    """
    out = []

    for row in rows:

        if not isinstance(row, dict):
            continue

        cmd = row.get("Command", {})
        if not isinstance(cmd, dict):
            continue

        try:
            cmd_id = int(cmd.get("command", -1))
        except Exception:
            continue

        if cmd_id != int(command_id):
            continue

        prev_telem = canonicalize_grouped_snapshot(
            row.get("Prev_Telemetry", {})
        )

        future_telem = canonicalize_grouped_snapshot(
            row.get("NEXT_Telemetry", {})
        )

        ex = {
            "command": {
                "command": cmd.get("command"),
                "param1": cmd.get("param1"),
                "param2": cmd.get("param2"),
                "param3": cmd.get("param3"),
                "param4": cmd.get("param4"),
                "param5": cmd.get("param5"),
                "param6": cmd.get("param6"),
                "param7": cmd.get("param7"),
            },
            "prev_heartbeat": row.get("Prev_HB", {}),
            "prev_telemetry": prev_telem,
            "command_ack": row.get("Command_ACK", {}),
            "future_telemetry": future_telem,
        }

        out.append(ex)

        if len(out) >= k:
            break

    return out


# ///// Step 3 — retriever from px4_command_sequences.jsonl
def retrieve_telemetry_examples_from_sequences(rows, command_id: int, k: int = 2):
    """
    Retrieve followup telemetry examples from px4_command_sequences.jsonl.

    followups is a list of MAVLink-like message objects.
    """
    out = []

    for row in rows:

        if not isinstance(row, dict):
            continue

        req = row.get("request", {})
        if not isinstance(req, dict):
            continue

        try:
            cmd_id = int(req.get("command", -1))
        except Exception:
            continue

        if cmd_id != int(command_id):
            continue

        future_telem = []
        followups = row.get("followups", [])

        if isinstance(followups, list):
            for item in followups:
                if not isinstance(item, dict):
                    continue

                grouped = canonicalize_followup_message(item)

                if grouped:
                    future_telem.append(grouped)

                if len(future_telem) >= 5:
                    break

        ex = {
            "command": {
                "command": req.get("command"),
                "param1": req.get("param1"),
                "param2": req.get("param2"),
                "param3": req.get("param3"),
                "param4": req.get("param4"),
                "param5": req.get("param5"),
                "param6": req.get("param6"),
                "param7": req.get("param7"),
            },
            "ack": row.get("ack", {}),
            "future_telemetry": future_telem
        }

        out.append(ex)

        if len(out) >= k:
            break

    return out
# handling for telemetry//////////



#  /////////// command_ack //////////
# ----------------------------------------
# Rule based ACK logic
# ----------------------------------------
def _is_armed(state) -> bool:
    # PX4: armed flag is base_mode bit 7 (0x80)
    return (int(getattr(state, "base_mode", 0)) & 0x80) != 0

def _alt_m(state) -> float:
    # Prefer relative altitude in mm if you have it; else use vfr_alt; else 0
    if hasattr(state, "gpi_relative_alt"):
        return float(getattr(state, "gpi_relative_alt", 0)) / 1000.0
    if hasattr(state, "vfr_alt"):
        return float(getattr(state, "vfr_alt", 0.0))
    if hasattr(state, "gps_alt"):
        return float(getattr(state, "gps_alt", 0)) / 1000.0
    return 0.0

def rule_based_ack(cmd, params, state):
    alt = _alt_m(state)
    armed = _is_armed(state)

    # ARM/DISARM (400)
    if cmd == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
        arm_flag = int(round(float(params.get("param1", 0.0))))

        if arm_flag == 1:  # ARM
            if not armed:
                return mavutil.mavlink.MAV_RESULT_ACCEPTED, "arm allowed"
            else:
                return mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED, "already armed"

        else:  # DISARM
            if alt < 0.2:
                return mavutil.mavlink.MAV_RESULT_ACCEPTED, "disarm allowed"
            else:
                return mavutil.mavlink.MAV_RESULT_DENIED, "cannot disarm in air"

    # TAKEOFF (22)
    if cmd == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF:
        target_alt = float(params.get("param7", 3.0))

        if armed and alt < 0.3 and target_alt > 0.5:
            return mavutil.mavlink.MAV_RESULT_ACCEPTED, "takeoff allowed"
        else:
            return mavutil.mavlink.MAV_RESULT_DENIED, "takeoff preconditions failed"

    # LAND (21)
    if cmd == mavutil.mavlink.MAV_CMD_NAV_LAND:
        if armed and alt > 0.5:
            return mavutil.mavlink.MAV_RESULT_ACCEPTED, "landing allowed"
        else:
            return mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED, "not flying"

    # WAYPOINT (16)
    if cmd == mavutil.mavlink.MAV_CMD_NAV_WAYPOINT:
        if armed:
            return mavutil.mavlink.MAV_RESULT_ACCEPTED, "waypoint accepted"
        else:
            return mavutil.mavlink.MAV_RESULT_DENIED, "not armed"

    return mavutil.mavlink.MAV_RESULT_UNSUPPORTED, "unsupported command"
#  /////////// command_ack //////////


# ============================================================
# CONNECTIVITY CLASS (extracted from LLMHoneypot)
# ============================================================

class LLMHoneypot:

    # ---------------------------
    # __init__  (GCS socket setup)
    # ---------------------------
    def __init__(self, listen_ip="0.0.0.0", listen_port=14550):

        self.listen_ip = listen_ip
        self.listen_port = listen_port

        # UDP socket setup  ← from your code
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        #self.sock.bind((self.listen_ip, self.listen_port)) //for qgc
        self.sock.bind(("127.0.0.1", 14551)) # for qgc new
        self.gcs_addr = ("127.0.0.1", 14550) #for qgc new
        self.sock.settimeout(0.2)

        # MAVLink encoder/decoder
        self.mav_out = mavutil.mavlink.MAVLink(None)
        self.mav_out.srcSystem = 1
        self.mav_out.srcComponent = 1

        self.mav_in = mavutil.mavlink.MAVLink(None)

        #self.gcs_addr = None  //for qgc
        self.state = CommonState()
        self.boot_time = time.monotonic()

        print(f"[INIT] Listening on {self.listen_ip}:{self.listen_port}")

        self.llm_enabled = True
        self.llm_log = []
        self.state_lock = threading.Lock()
        self.identity_locked = False


        # /////// telemetry helpers ///////
        self.stream_lock = threading.Lock()
        self.streams = {}  # name -> [rate_hz, next_send]
        # heartbeat is handled by heartbeat_loop, so don't add it here

        self.telemetry_stop = threading.Event()
        self.telemetry_thread = threading.Thread(target=self.telemetry_loop, daemon=True)

        # //////// command helpers ///////
        # --- history buffers ---
        self.hist = HistoryBuffer(max_hb=10, max_telem=10, max_cmd=10)

        # --- command rag (mounted file path from your upload) ---
        self.cmd_trace_rows = load_command_rag_jsonl("../out/px4_command_trace.jsonl")
        self.cmd_seq_rows   = load_command_rag_jsonl("../out/px4_command_sequences.jsonl")

        # --- telemetry override queue (10-step series after a command) ---
        self.override_lock = threading.Lock()
        self.override_series = deque()  # each item: {"apply_at": float_monotonic, "fields": {...}}


        # ////////// for QGC
        self.params = [
            ("SYS_AUTOSTART", 4010.0, mavutil.mavlink.MAV_PARAM_TYPE_INT32),
            ("COM_ARM_WO_GPS", 0.0,   mavutil.mavlink.MAV_PARAM_TYPE_INT32),
            ("MPC_XY_VEL_MAX", 5.0,   mavutil.mavlink.MAV_PARAM_TYPE_REAL32),
            ("MIS_TAKEOFF_ALT", 10.0, mavutil.mavlink.MAV_PARAM_TYPE_REAL32),
            ("RTL_RETURN_ALT", 15.0,  mavutil.mavlink.MAV_PARAM_TYPE_REAL32),
        ]
        self.param_index = {name: i for i, (name, _, _) in enumerate(self.params)}
        # ////////// for QGC


        self.cmd_transition_rows = load_command_rag_jsonl("../out/cmd_transition.jsonl")

        # ---- CONTINUATION STATE ---- # new 2 --telemety patch
        self.continuation_enabled = False
        self.continuation_inflight = False
        self.last_action_cmd = None
        self.last_action_params = None

        self.no_continuation_cmds = {512, 521} #cmd ignore

    

    # //////// command helpers ///////

    def log_llm_io(self, tag: str, system_text: str, user_text: str, raw: str, parsed: dict, latency_ms: float):
        row = {
            "ts": time.time(),
            "tag": tag,                    # e.g., "startup_identity", "command"
            "latency_ms": latency_ms,
            "system_text": system_text,    # optional (can be huge)
            "user_text": user_text,        # optional (can be huge)
            "raw": raw,
            "parsed": parsed,
        }
        try:
            with open("llm_io_log.jsonl", "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            print(f"[LLM LOG ERROR] {e}", flush=True)

    # ---------------------------
    # send_mav()  (GCS TX)
    # ---------------------------
    def send_mav(self, msg):
        if not self.gcs_addr:
            return
        pkt = msg.pack(self.mav_out)
        self.sock.sendto(pkt, self.gcs_addr)
        # print(f"[SEND_MAV] dst={self.gcs_addr} bytes={len(pkt)}", flush=True)

    # ///////////////HEARTBEAT /////////////////////////

    def heartbeat_loop(self):
        while True:
            if self.gcs_addr:
                update_time_fields(self.state, self.boot_time)               
    #  to make llm give heartbeat all the time | uncomment below ////////////////////////
                # response = self.llm_prompt({"event": "heartbeat_tick"},context_type="heartbeat")
                # if response and self.verify_response(response):
                #     self.llm_output_process(response)
    #  to make llm give heartbeat all the time ////////////////////////
                # log last heartbeat snapshot (for LLM prompt)               
                hb = make_heartbeat(self.mav_out, self.state)
                # print(
                #     "[HEARTBEAT TX]", "base_mode=", int(self.state.base_mode),
                #     "custom_mode=", int(self.state.custom_mode), "system_status=", int(self.state.system_status),
                #     "armed=", bool(self.state.base_mode & 0x80),flush=True) # debug delete later dlt

                self.send_mav(hb)
                with self.state_lock: # log last 10 hb
                    self.hist.add_hb(self.state) # log last hb (for LLM prompt)
            time.sleep(1)


    def initialize_heartbeat_identity(self):
        """
        Call LLM once to initialize heartbeat identity.
        """

        response = self.llm_prompt({"event": "startup_identity"})

        if response and self.verify_response(response):

            with self.state_lock:
                for k, v in response["state_patch"].items():
                    setattr(self.state, k, v)

            print("[HEARTBEAT] Identity initialized by LLM")
            print("[DEBUG] Initial HB State:",
                self.state.hb_type,
                self.state.hb_autopilot,
                self.state.base_mode,
                self.state.system_status)
            self.identity_locked = True

    # static HB        
    # def initialize_heartbeat_identity(self):
    #     """
    #     Initialize heartbeat identity safely at startup.
    #     LLM is not allowed to invent startup heartbeat bits.
    #     """

    #     with self.state_lock:
    #         # identity
    #         self.state.hb_type = mavutil.mavlink.MAV_TYPE_QUADROTOR
    #         self.state.hb_autopilot = mavutil.mavlink.MAV_AUTOPILOT_PX4
    #         self.state.mavlink_version = 3

    #         # startup heartbeat state
    #         self.state.base_mode = 65
    #         self.state.custom_mode = 0
    #         self.state.system_status = 3

    #     print("[HEARTBEAT] Identity initialized safely")
    #     print(
    #         "[DEBUG] Initial HB State:",
    #         self.state.hb_type,
    #         self.state.hb_autopilot,
    #         self.state.base_mode,
    #         self.state.custom_mode,
    #         self.state.system_status,
    #     )

    #     self.identity_locked = True

    # ////////////////////// telemetry ///////////////////////

    def _msg_name_from_id(self, msg_id: int) -> str:
        """
        Best-effort msg_id -> message name (e.g., 1 -> SYS_STATUS).
        Works across pymavlink variants.
        """
        try:
            cls = mavutil.mavlink.mavlink_map.get(int(msg_id))
            if cls is None:
                return ""
            # Many builds provide .msgname
            name = getattr(cls, "msgname", "")
            if name:
                return str(name)
            # Fallback: class name like MAVLink_sys_status_message
            cname = getattr(cls, "__name__", "")
            if cname.startswith("MAVLink_") and cname.endswith("_message"):
                return cname[len("MAVLink_"):-len("_message")].upper()
            return cname.upper()
        except Exception:
            return ""


    def _apply_message_interval_request(self, msg_id: int, interval_us: int) -> bool:
        """
        Returns True if handled, False if unsupported/unknown.
        """
        # Normalize (COMMAND_LONG params often come in as floats)
        try:
            msg_id_i = int(round(float(msg_id)))
            interval_us_i = int(round(float(interval_us)))
        except Exception as e:
            print(f"[STREAM] invalid params msg_id={msg_id!r} interval_us={interval_us!r} err={e}", flush=True)
            return False

        name = self._msg_name_from_id(msg_id_i)
        if not name:
            print(f"[STREAM] unknown msg_id={msg_id_i} interval_us={interval_us_i}", flush=True)
            return False

        # HEARTBEAT handled elsewhere
        if name == "HEARTBEAT":
            print(f"[STREAM] ignoring HEARTBEAT request interval_us={interval_us_i}", flush=True)
            return True

        # Only stream what we can build
        if name not in TELEM_BUILDERS:
            print(f"[STREAM] not supported {name} (msg_id={msg_id_i})", flush=True)
            return False

        # disable stream
        if interval_us_i <= 0:
            with self.stream_lock:
                self.streams.pop(name, None)
            print(f"[STREAM] disabled {name}", flush=True)
            return True

        rate_hz = 1e6 / float(interval_us_i)
        rate_hz = max(0.1, min(rate_hz, 50.0))

        now = time.monotonic()
        with self.stream_lock:
            self.streams[name] = [rate_hz, now]

        print(f"[STREAM] enabled {name} (msg_id={msg_id_i}) @ {rate_hz:.2f} Hz", flush=True)
        return True



    def telemetry_loop(self):
        """
        Sends telemetry ONLY for streams requested via SET_MESSAGE_INTERVAL.
        HEARTBEAT is not handled here.
        """
        tick = 0.01  # internal scheduler rate (100 Hz)

        while not self.telemetry_stop.is_set():
            if not self.gcs_addr:
                time.sleep(tick)
                continue

            now = time.monotonic()

            with self.state_lock:
                update_time_fields(self.state, self.boot_time)



            # ///////////// Apply queued telemetry “command impact” inside
            # --- apply scheduled command-impact telemetry steps (if any) ---
            with self.override_lock:
                while self.override_series and now >= self.override_series[0]["apply_at"]:
                    step = self.override_series.popleft()
                    fields = step.get("fields", {})
                    with self.state_lock:
                        for k, v in fields.items():
                            if hasattr(self.state, k):
                                setattr(self.state, k, v)

            # ///////////// Apply queued telemetry “command impact” inside

            with self.stream_lock:
                items = list(self.streams.items())  # copy
                # print("[ACTIVE STREAMS]", list(self.streams.keys()), flush=True) # new///

            for name, (rate_hz, next_send) in items:
                if now < next_send:
                    continue

                builder = TELEM_BUILDERS.get(name)
                if not builder:
                    continue

                try:
                    with self.state_lock:
                        msg = builder(self.mav_out, self.state)
                    self.send_mav(msg)
                    # print(f"[TELEM TX] {name}", flush=True) # new///
                except Exception as e:
                    print(f"[TELEM ERROR] {name}: {e}", flush=True)
                    continue

                # after you successfully send a telemetry message, log what you sent (from state fields relevant to that message):
                # log telemetry snapshot (minimal fields per message type)
                # with self.state_lock:
                #     if name == "SYS_STATUS":
                #         self.hist.add_telem(name, {
                #             "voltage_battery": self.state.voltage_battery,
                #             "current_battery": self.state.current_battery,
                #             "battery_remaining": self.state.battery_remaining,
                #             "load": self.state.load,
                #         })
                #     elif name == "GPS_RAW_INT":
                #         self.hist.add_telem(name, {
                #             "time_usec": self.state.time_usec,
                #             "gps_fix_type": self.state.gps_fix_type,
                #             "gps_lat": self.state.gps_lat,
                #             "gps_lon": self.state.gps_lon,
                #             "gps_alt": self.state.gps_alt,
                #             "gps_vel": self.state.gps_vel,
                #             "gps_cog": self.state.gps_cog,
                #             "gps_satellites_visible": self.state.gps_satellites_visible,
                #         })
                #     elif name == "GLOBAL_POSITION_INT":
                #         self.hist.add_telem(name, {
                #             "time_boot_ms": self.state.time_boot_ms,
                #             "gpi_lat": self.state.gpi_lat,
                #             "gpi_lon": self.state.gpi_lon,
                #             "gpi_alt": self.state.gpi_alt,
                #             "gpi_relative_alt": self.state.gpi_relative_alt,
                #             "gpi_vx": self.state.gpi_vx,
                #             "gpi_vy": self.state.gpi_vy,
                #             "gpi_vz": self.state.gpi_vz,
                #             "gpi_hdg": self.state.gpi_hdg,
                #         })
                #     elif name == "ATTITUDE":
                #         self.hist.add_telem(name, {
                #             "time_boot_ms": self.state.time_boot_ms,
                #             "roll": self.state.roll,
                #             "pitch": self.state.pitch,
                #             "yaw": self.state.yaw,
                #             "rollspeed": self.state.rollspeed,
                #             "pitchspeed": self.state.pitchspeed,
                #             "yawspeed": self.state.yawspeed,
                #         })
                #     elif name == "VFR_HUD":
                #         self.hist.add_telem(name, {
                #             "vfr_airspeed": self.state.vfr_airspeed,
                #             "vfr_groundspeed": self.state.vfr_groundspeed,
                #             "vfr_heading": self.state.vfr_heading,
                #             "vfr_throttle": self.state.vfr_throttle,
                #             "vfr_alt": self.state.vfr_alt,
                #             "vfr_climb": self.state.vfr_climb,
                #         })

                # ///// new part
                # log telemetry snapshot AFTER successful send
                with self.state_lock:
                    if name == "SYS_STATUS":
                        self.hist.add_telem(name, {
                            "battery_remaining": self.state.battery_remaining,
                            "voltage_battery": self.state.voltage_battery,
                            "load": self.state.load,
                        })

                    elif name == "GPS_RAW_INT":
                        self.hist.add_telem(name, {
                            "gps_fix_type": self.state.gps_fix_type,
                        })

                    elif name == "GLOBAL_POSITION_INT":
                        self.hist.add_telem(name, {
                            "gpi_lat": self.state.gpi_lat,
                            "gpi_lon": self.state.gpi_lon,
                            "gpi_alt": self.state.gpi_alt,
                            "gpi_relative_alt": self.state.gpi_relative_alt,
                            "gpi_vx": self.state.gpi_vx,
                            "gpi_vy": self.state.gpi_vy,
                            "gpi_vz": self.state.gpi_vz,
                            "gpi_hdg": self.state.gpi_hdg,
                        })

                    elif name == "ATTITUDE":
                        self.hist.add_telem(name, {
                            "roll": self.state.roll,
                            "pitch": self.state.pitch,
                            "yaw": self.state.yaw,
                        })

                    elif name == "VFR_HUD":
                        self.hist.add_telem(name, {
                            "vfr_groundspeed": self.state.vfr_groundspeed,
                            "vfr_heading": self.state.vfr_heading,
                            "vfr_throttle": self.state.vfr_throttle,
                            "vfr_alt": self.state.vfr_alt,
                            "vfr_climb": self.state.vfr_climb,
                        })
                # /////////////// telemetry logging /////

                period = 1.0 / max(0.0001, float(rate_hz))
                with self.stream_lock:
                    # stream may have been removed meanwhile
                    if name in self.streams:
                        self.streams[name][1] = now + period

            time.sleep(tick)

            # ////////////////////// telemetry ///////////////////////

    # send telemetry helpers # new///
    def enable_default_telem_streams(self):
        now = time.monotonic()
        with self.stream_lock:
            self.streams["SYS_STATUS"] = [5.0, now]
            self.streams["GPS_RAW_INT"] = [1.0, now]
            self.streams["GLOBAL_POSITION_INT"] = [5.0, now]
            self.streams["ATTITUDE"] = [10.0, now]
            self.streams["VFR_HUD"] = [5.0, now]
            self.streams["BATTERY_STATUS"] = [2.0, now] #new

        print("[DEFAULT STREAMS ENABLED]", list(self.streams.keys()), flush=True)


    # //////////////////////QGC ///////////////////////
    def _send_param_value(self, name: str, value: float, ptype: int, index: int, count: int):
        name16 = name[:16]  # MAVLink param_id is 16 chars
        msg = self.mav_out.param_value_encode(
            name16.encode("ascii"),
            float(value),
            int(ptype),
            int(count),
            int(index),
        )
        self.send_mav(msg)

    def _send_all_params(self):
        count = len(self.params)
        for i, (name, val, ptype) in enumerate(self.params):
            self._send_param_value(name, val, ptype, i, count)
    # //////////////////////QGC ///////////////////////


    # ---------------------------
    # core()  (Main loop)
    # ---------------------------

    OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.1.22:11434")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")
    OLLAMA_TIMEOUT_SEC = 20

    LLM_OUTPUT_RULES = """
    Return ONLY valid JSON in this exact shape:
    {
    "verdict": {"label": "benign|suspicious|attack", "reason": "short"},
    "state_patch": { "<CommonState field>": <value>, ... },
    "telemetry_series": [
        {"dt": 0.0, "fields": {"<CommonState field>": <value>, ...}},
        {"dt": 0.1, "fields": {...}},
        ...
    ]
    }

    Rules:
    - telemetry_series covers ~1-3 seconds, dt is non-decreasing.
    - Only use fields that exist in CommonState.
    - Keep values plausible.
    Units (MUST follow):
    - gps_lat, gps_lon, gpi_lat, gpi_lon: integer in range -900000000 to 900000000
    - gps_alt, gpi_alt, gpi_relative_alt: int millimeters
    - gps_vel: int cm/s
    - gps_cog, gpi_hdg: int centi-degrees (0..35999)
    - roll, pitch, yaw: radians
    """
    # new ///
    def call_ollama_cloud(self, system_text: str, user_text: str, tag: str = "general") -> str:
        t0 = time.monotonic()

        try:
            with open("api_key.txt", "r") as f:
                api_key = f.read().strip()

            payload = {
                "model": "gpt-oss:20b-cloud",
                "messages": [
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_text}
                ],
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0,
                    "top_p": 0.9
                }
            }

            r = requests.post(
                "https://ollama.com/api/chat",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=360
            )

            if r.status_code != 200:
                print(f"[LLM CLOUD ERROR {tag}] {r.text[:300]}", flush=True)

            r.raise_for_status()

            raw = r.json()["message"]["content"]
            dt_ms = (time.monotonic() - t0) * 1000.0

            parsed = extract_json(raw)
            self.log_llm_io(tag, system_text, user_text, raw, parsed, dt_ms)

            return raw

        except Exception as e:
            print(f"[LLM CLOUD FAIL {tag}] {e}", flush=True)
            return ""


    def call_ollama(self, system_text: str, user_text: str, tag: str = "general") -> str:
        payload = {
            "model": self.OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text}
            ],
            "stream": False,
            "format": "json",  #  FORCE VALID JSON
            "options": {
                "temperature": 0.2
                # "top_p": 1
            }
        }
        t0 = time.monotonic()
        r = requests.post(
            f"{self.OLLAMA_URL}/api/chat",
            json=payload,
            timeout=self.OLLAMA_TIMEOUT_SEC,
        )
        r.raise_for_status()
        
        raw = r.json()["message"]["content"]
        dt_ms = (time.monotonic() - t0) * 1000.0

        parsed = extract_json(raw)  # may be None
        self.log_llm_io(tag, system_text, user_text, raw, parsed, dt_ms)

        return raw


    # def llm_prompt(self, attacker_msg: Optional[dict] = None) -> Optional[dict]:
    def llm_prompt(self, attacker_msg=None, context_type="general"):
        """
        Build prompt + call Ollama + extract JSON.
        Returns parsed dict or None.
        """

        HEARTBEAT_PROMPT_PATCH = """
        Context: You are generating HEARTBEAT state updates.

        Rules:
        - Only modify: base_mode, custom_mode, system_status.
        - Do NOT modify: hb_type, hb_autopilot, mavlink_version.
        - Maintain identity consistency.
        - Armed flag is base_mode bit 7.
        - system_status must be realistic (e.g., STANDBY=3, ACTIVE=4).
        - Do not generate large sudden changes.
        """


        # -------------------------
        # 1️⃣ Snapshot state safely
        # -------------------------
        with self.state_lock:
            snapshot = self.state.__dict__.copy()

        # /////////////////// HEARTBEAT /////////////////////////
        if context_type == "heartbeat":
            system_text = self.LLM_OUTPUT_RULES + "\n" + HEARTBEAT_PROMPT_PATCH
        else:
            system_text = self.LLM_OUTPUT_RULES
        # /////////////////// HEARTBEAT /////////////////////////
        

        user_text = json.dumps({
            "current_state": snapshot,
            "attacker_input": attacker_msg,
            "instruction": "Evaluate situation and respond per output rules."
        })

        try:
            # -------------------------
            # 2️⃣ Call model
            # -------------------------
            # raw = call_ollama(LLM_OUTPUT_RULES, user_text)
            # print("\n[LLM PROMPT] system_text:\n", system_text)
            # print("\n[LLM PROMPT] user_text:\n", user_text)
            # raw = self.call_ollama(system_text, user_text)
            raw = self.call_ollama(system_text, user_text, tag=context_type)


            # -------------------------
            # 3️⃣ Extract JSON
            # -------------------------
            # print("\n[RAW LLM OUTPUT]\n", raw, "\n")
            parsed = extract_json(raw)

            if parsed is None:
                print("[LLM] JSON extraction failed")
                return None

            return parsed

        except Exception as e:
            print(f"[LLM ERROR] {e}")
            return None




    def verify_response(self, response: dict) -> bool:

        if not isinstance(response, dict):
            return False

        required = {"verdict", "state_patch", "telemetry_series"}
        if not required.issubset(response.keys()):
            print("[VERIFY FAIL] Missing required keys")
            return False

        valid_fields = set(self.state.__dict__.keys())

        # Validate state_patch
        state_patch = response.get("state_patch", {})
        if not isinstance(state_patch, dict):
            return False

        for k in state_patch.keys():
            if k not in valid_fields:
                print(f"[VERIFY FAIL] Invalid state field: {k}")
                return False

        # Validate telemetry_series
        series = response.get("telemetry_series", [])
        if not isinstance(series, list):
            return False

        last_dt = -1
        for step in series:
            if "dt" not in step or "fields" not in step:
                print("[VERIFY FAIL] Bad telemetry structure")
                return False

            if step["dt"] < last_dt:
                print("[VERIFY FAIL] dt not non-decreasing")
                return False

            last_dt = step["dt"]

            for k in step["fields"].keys():
                if k not in valid_fields:
                    print(f"[VERIFY FAIL] Invalid telemetry field: {k}")
                    return False
        # ///////////////////////HEARTBEAT /////////////////////////
        if self.identity_locked:
            protected = {"hb_type", "hb_autopilot", "mavlink_version"}
            for k in state_patch.keys():
                if k in protected:
                    print(f"[VERIFY FAIL] Attempt to modify locked identity field: {k}")
                    return False
        # ///////////////////////HEARTBEAT /////////////////////////
        return True


    def llm_output_process(self, response: dict):

        if not self.verify_response(response):
            print("[LLM] Response rejected.")
            return

        with self.state_lock:
            # Apply immediate state patch
            for k, v in response["state_patch"].items():
                setattr(self.state, k, v)

        print("[LLM] State patch applied.")



    def llm_logging(self, attacker_msg: dict, response: dict):

        entry = {
            "timestamp": time.time(),
            "attacker_input": attacker_msg,
            "verdict": response.get("verdict"),
            "state_patch": response.get("state_patch"),
            "telemetry_steps": len(response.get("telemetry_series", []))
        }

        self.llm_log.append(entry)

        try:
            with open("llm_decision_log.jsonl", "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"[LOG ERROR] {e}")



    # ////////////handle commands////////////////////////
    # logging the command and response
    def log_cmd_vs_llm(self, cmd: int, params: dict, llm_raw: str, llm_parsed: dict):
        row = {
            "ts": time.time(),
            "cmd": int(cmd),
            "params": params,
            "llm_raw": llm_raw,
            "llm_parsed": llm_parsed,
        }
        try:
            with open("./logs/cmd_llm_log.jsonl", "a") as f:
                f.write(json.dumps(row) + "\n")
            print("[LOG] log_cmd_vs_llm CALLED", flush=True)
            print("[LOG] path=./logs/cmd_llm_log.jsonl", flush=True)
        except Exception as e:
            print(f"[CMD_LLM_LOG_ERROR] {e}", flush=True)

    def llm_prompt_command(self, command_id: int, params: Dict[str, Any]) -> Optional[dict]:
        # Snapshot state + history
        with self.state_lock:
            snapshot = self.state.__dict__.copy()
        hist = self.hist.snapshot()

        # fewshot = rag_retrieve_examples(self.command_rag_rows, command_id, k=3)
        fewshot_trace = rag_retrieve_examples(self.cmd_trace_rows, command_id, k=2)
        fewshot_seq   = rag_retrieve_examples(self.cmd_seq_rows, command_id, k=2)

        fewshot = {
            "trace_examples": fewshot_trace,
            "sequence_examples": fewshot_seq,
        }

        system_text = """
        You are an autopilot behavior generator for a MAVLink honeypot.
        Return ONLY valid JSON. No prose.

        Output EXACTLY:
        {
        "ack": {"command": <int>, "result": "ACCEPTED|DENIED|UNSUPPORTED|TEMPORARILY_REJECTED", "reason": "short"},
        "heartbeat_patch": {"base_mode": <int optional>, "custom_mode": <int optional>, "system_status": <int optional>},
        "state_patch": { "<CommonState field>": <value>, ... },
        "telemetry_series": [
            {"dt": 0.0, "fields": {...}},
            ...
            {"dt": 0.4, "fields": {...}}
        ]
        }

        Rules:
        - telemetry_series MUST have exactly 5 steps
        - dt values MUST be: 0.0, 0.1, 0.2, 0.3, 0.4 (no other dt allowed).
        - Do NOT include dt=0.5 or dt=1.0.
        - Only use fields that exist in CommonState.
        - Smooth realistic changes (no big jumps).
        - heartbeat_patch may ONLY change base_mode, custom_mode, system_status.
        - base_mode armed flag is bit 7 (0x80); system_status: 3=STANDBY, 4=ACTIVE.
        """.strip()

        user_text = json.dumps({
            "command": {"id": int(command_id), "params": params},
            "current_state": snapshot,
            "history": hist,
            "fewshot": fewshot,
            "instruction": "Generate ACK and the next 1 second of telemetry impact from this command."
        })

        try:
            # raw = self.call_ollama(system_text, user_text)
            # raw = self.call_ollama(system_text, user_text, tag="command")
            # parsed = extract_json(raw)
            # return parsed
            print("\n[LLM USER PROMPT]")
            print(user_text)
            raw = self.call_ollama(system_text, user_text, tag="command")
            parsed = extract_json(raw)
            return {"_raw": raw, "_parsed": parsed}
        except Exception as e:
            print(f"[LLM CMD ERROR] {e}", flush=True)
            return None


    def apply_llm_command_result(self, response: dict) -> None:
        """
        1) Send COMMAND_ACK
        2) Apply heartbeat_patch + state_patch immediately
        3) Schedule telemetry_series over next 1 second (10 steps)
        """
        if not isinstance(response, dict):
            return

        ack = response.get("ack", {})
        cmd_id = int(ack.get("command", -1)) if isinstance(ack, dict) else -1
        result_str = (ack.get("result") if isinstance(ack, dict) else "UNSUPPORTED") or "UNSUPPORTED"
        result_str = str(result_str).upper().strip()
        mav_result = ACK_RESULT_MAP.get(result_str, mavutil.mavlink.MAV_RESULT_UNSUPPORTED)

        # //// commented out for ack time delay /////
        # if cmd_id >= 0:
        #     ack_msg = self.mav_out.command_ack_encode(cmd_id, mav_result)
        #     self.send_mav(ack_msg)
        # //// commented out for ack time delay /////
        

        # apply patches
        hb_patch = response.get("heartbeat_patch", {}) if isinstance(response.get("heartbeat_patch", {}), dict) else {}
        state_patch = response.get("state_patch", {}) if isinstance(response.get("state_patch", {}), dict) else {}

        with self.state_lock:
            for k, v in hb_patch.items():
                if k in ("base_mode", "custom_mode", "system_status"):
                    setattr(self.state, k, v)
            for k, v in state_patch.items():
                if hasattr(self.state, k):
                    setattr(self.state, k, v)

        # schedule telemetry series
        series = response.get("telemetry_series", [])
        if not isinstance(series, list) or len(series) == 0:
            return

        base = time.monotonic()
        with self.override_lock:
            self.override_series.clear()
            for step in series:
                dt = float(step.get("dt", 0.0))
                fields = step.get("fields", {})
                if not isinstance(fields, dict):
                    continue
                self.override_series.append({
                    "apply_at": base + dt,
                    "fields": fields
                })

        print(f"[CMD APPLY] ack={result_str} scheduled_steps={len(self.override_series)}", flush=True)
    # for heartbeat based on commands /////////
    def handle_command_heartbeat(self, command_id: int, params: dict) -> Optional[dict]:
        """
        Heartbeat-only LLM handler.
        Input:
        - rag traces as example
        - current command
        - previous heartbeat only
        - optional telemetry slot kept commented for later
        Output:
        - parsed response dict, or None
        Side effect:
        - applies heartbeat patch once if valid
        """
        fewshot_seq = retrieve_heartbeat_examples_from_sequences(self.cmd_seq_rows, command_id, k=5)
        fewshot_trace = []   # keep trace disabled for heartbeat for now
        # 1) collect only previous heartbeat
        with self.state_lock:
            prev_hb = {
                "hb_type": int(self.state.hb_type),
                "hb_autopilot": int(self.state.hb_autopilot),
                "base_mode": int(self.state.base_mode),
                "custom_mode": int(self.state.custom_mode),
                "system_status": int(self.state.system_status),
                "mavlink_version": int(self.state.mavlink_version),
            }

        # Optional telemetry context for later
        # last_telem = self.hist.snapshot().get("last_telemetry", [])
        # last_telem = last_telem[-1] if last_telem else None

        system_text = """
        You are a MAVLink heartbeat patch generator for a drone honeypot.

        You are given:
        - the current command
        - the current previous heartbeat
        - a few command-centered heartbeat transition examples from past traces

        Your task:
        - compare the current command with the examples
        - start from previous_heartbeat
        - produce only the immediate next heartbeat patch

        Return ONLY valid JSON in exactly this format:
        {
        "heartbeat_patch": {
            "base_mode": <int>,
            "custom_mode": <int>,
            "system_status": <int>
        },
        "reason": "<short>"
        }

        Rules:
        - Do NOT invent a new heartbeat from scratch.
        - Start from previous_heartbeat.
        - Use the fewshot transition examples to infer whether this command changes heartbeat.
        - If examples do not show a heartbeat-changing effect, preserve previous heartbeat.
        - Only command 400 may change the armed bit in base_mode.
        - For commands other than 400, preserve the armed bit exactly.
        - heartbeat_patch must contain exactly: base_mode, custom_mode, system_status.
        - Do not output hb_type, hb_autopilot, mavlink_version, or telemetry fields.
        - Return JSON only.
        """.strip()

        user_payload = {
            "command": {
                "id": int(command_id),
                "params": {
                    "param1": float(params.get("param1", 0.0)),
                    "param2": float(params.get("param2", 0.0)),
                    "param3": float(params.get("param3", 0.0)),
                    "param4": float(params.get("param4", 0.0)),
                    "param5": float(params.get("param5", 0.0)),
                    "param6": float(params.get("param6", 0.0)),
                    "param7": float(params.get("param7", 0.0)),
                },
            },
            "previous_heartbeat": prev_hb,
            "fewshot": {
                "sequence_examples": fewshot_seq,
                "trace_examples": fewshot_trace,
            },
            # "previous_telemetry": last_telem,
            "instruction": "Generate one heartbeat patch for this command."
        }

        user_text = json.dumps(user_payload)

        try:
            print("\n[HB LLM USER PROMPT]")
            print(user_text, flush=True) # view llm prompt

            # raw = self.call_ollama(system_text, user_text, tag="heartbeat_command")
            raw = self.call_ollama_cloud(system_text, user_text, tag="heartbeat_command") # *heartbeat cloud
            parsed = extract_json(raw)

            print("\n[HB LLM RAW RESPONSE]")
            print(raw, flush=True) # view llm prompt

            if not parsed:
                print("[HB LLM] JSON parse failed", flush=True)
                return None

            if not self.validate_heartbeat_patch_response(parsed, prev_hb):
                print("[HB LLM] heartbeat patch validation failed", flush=True)
                return None

            patch = parsed.get("heartbeat_patch", {})
            self.apply_heartbeat_patch(patch)

            print("\n[HB PATCH APPLIED]")
            print(json.dumps(patch, indent=2), flush=True)

            return parsed

        except Exception as e:
            print(f"[HB LLM ERROR] {e}", flush=True)
            return None

    def validate_heartbeat_patch_response(self, response: dict, prev_hb: dict) -> bool:
        """
        Validate heartbeat-only LLM response.
        """

        if not isinstance(response, dict):
            return False

        if "heartbeat_patch" not in response:
            print("[HB VERIFY FAIL] missing heartbeat_patch", flush=True)
            return False

        patch = response.get("heartbeat_patch")
        if not isinstance(patch, dict):
            print("[HB VERIFY FAIL] heartbeat_patch is not dict", flush=True)
            return False

        allowed = {"base_mode", "custom_mode", "system_status"}
        required = {"base_mode", "custom_mode", "system_status"}

        # Must contain exactly the required fields for this version
        if set(patch.keys()) != required:
            print(f"[HB VERIFY FAIL] patch keys must be exactly {required}, got {set(patch.keys())}", flush=True)
            return False

        for k, v in patch.items():
            if k not in allowed:
                print(f"[HB VERIFY FAIL] invalid field: {k}", flush=True)
                return False
            if not isinstance(v, int):
                # allow float that is integer-like
                if isinstance(v, float) and float(v).is_integer():
                    patch[k] = int(v)
                else:
                    print(f"[HB VERIFY FAIL] non-integer value for {k}: {v}", flush=True)
                    return False

        # protect identity fields implicitly by not allowing them at all
        # optional plausibility checks
        if patch["base_mode"] < 0:
            print("[HB VERIFY FAIL] base_mode negative", flush=True)
            return False

        if patch["system_status"] < 0:
            print("[HB VERIFY FAIL] system_status negative", flush=True)
            return False

        return True


    def apply_heartbeat_patch(self, patch: dict) -> None:
        """
        Apply heartbeat-only patch once.
        """

        if not isinstance(patch, dict):
            return

        allowed = {"base_mode", "custom_mode", "system_status"}

        with self.state_lock:
            for k, v in patch.items():
                if k in allowed:
                    setattr(self.state, k, int(v))


    # for heartbeat based on commands /////////

    # for telemetry based on commands /////////
    def get_current_heartbeat_snapshot(self) -> dict:
        with self.state_lock:
            return {
                "base_mode": int(self.state.base_mode),
                "custom_mode": int(self.state.custom_mode),
                "system_status": int(self.state.system_status),
            }


    # ------ validator 

    def get_last_5_telemetry_snapshots(self) -> list:
        hist = self.hist.snapshot().get("last_telemetry", [])
        out = []

        for item in hist[-5:]:
            if not isinstance(item, dict):
                continue

            fields = item.get("fields", {})
            if not isinstance(fields, dict):
                continue

            grouped = canonicalize_internal_history_fields(fields)
            if not grouped:
                continue

            out.append({
                "name": item.get("name"),
                "ts": item.get("ts"),
                "fields": grouped,
            })

        return out
        
    # ----prompting
    def handle_command_telemetry(self, command_id: int, params: dict) -> Optional[dict]:
        """
        Telemetry-only LLM handler.
        Uses:
          - current command
          - current heartbeat
          - last 5 live telemetry
          - transition examples from cmd_transition.jsonl
          - sequence followup examples from px4_command_sequences.jsonl
        """

        last_5_telem = self.get_last_5_telemetry_snapshots()
        current_hb = self.get_current_heartbeat_snapshot()

        transition_examples = retrieve_telemetry_examples_from_cmd_transition(
            self.cmd_transition_rows, command_id, k=2
        )
        sequence_examples = retrieve_telemetry_examples_from_sequences(
            self.cmd_seq_rows, command_id, k=2
        )
        print("[--DEBUG--] last_5_telemetry_count =", len(last_5_telem))
        print("[--DEBUG--] transition_examples =", len(transition_examples))
        print("[--DEBUG--] sequence_examples =", len(sequence_examples))
        
        system_text = """
        You are a MAVLink telemetry predictor for a drone honeypot.

        You are given:
        - the current command
        - the current heartbeat
        - the last 5 live telemetry snapshots
        - transition examples from past traces
        - short future telemetry examples from past traces
        - the allowed telemetry schema
        - always generate the battery remaining
        - your alt will change for taking off or landing

        Your task:
        - generate the next 5 telemetry states after this command
        - use only canonical MAVLink telemetry names
        - group telemetry by MAVLink message name
        - follow the allowed telemetry schema exactly

        Return ONLY valid JSON in exactly this format:
        {
        "telemetry_series": [
            {"dt": 0.0, "fields": {}},
            {"dt": 0.1, "fields": {}},
            {"dt": 0.2, "fields": {}},
            {"dt": 0.3, "fields": {}},
            {"dt": 0.4, "fields": {}}
        ],
        "reason": "<short>"
        }

        Rules:
        - Only use message groups and fields defined in allowed_telemetry_groups.
        - Do not use internal/private variable names.
        - Keep changes smooth and realistic.
        - If a field is not needed, omit it.
        - Return JSON only.
        """.strip()
        user_payload = {
            "command": {
                "id": int(command_id),
                "params": {
                    "param1": float(params.get("param1", 0.0)),
                    "param2": float(params.get("param2", 0.0)),
                    "param3": float(params.get("param3", 0.0)),
                    "param4": float(params.get("param4", 0.0)),
                    "param5": float(params.get("param5", 0.0)),
                    "param6": float(params.get("param6", 0.0)),
                    "param7": float(params.get("param7", 0.0)),
                },
            },
            "current_heartbeat": current_hb,
            "last_5_telemetry": last_5_telem,
            "transition_examples": transition_examples,
            "sequence_examples": sequence_examples,
            "allowed_telemetry_groups": TELEM_GROUPS,
            "instruction": "Generate the next 5 telemetry states using canonical MAVLink field names grouped by message."
        }

        user_text = json.dumps(user_payload)

        try:
            print("\n[TELEM LLM USER PROMPT]")
            print(user_text, flush=True) # view LLM prompt

            # raw = self.call_ollama(system_text, user_text, tag="telemetry_command")
            raw = self.call_ollama_cloud(system_text, user_text, tag="telemetry_command")
            parsed = extract_json(raw)

            print("\n[TELEM LLM RAW RESPONSE]")
            print(raw, flush=True)

            if not parsed:
                print("[TELEM LLM] JSON parse failed", flush=True)
                return None

            if not self.validate_telemetry_response(parsed):
                print("[TELEM LLM] validation failed", flush=True)
                return None

            # series = parsed.get("telemetry_series", [])
            # base = time.monotonic()

            # with self.override_lock:
            #     self.override_series.clear()
            #     for step in series:
            #         self.override_series.append({
            #             "apply_at": base + float(step["dt"]),
            #             "fields": step["fields"],
            #         })

            series = parsed.get("telemetry_series", [])
            translated_series = translate_canonical_series_to_internal(series)

            # if not self.validate_internal_translated_series(translated_series):
            #     print("[TELEM LLM] translated series validation failed", flush=True)
            #     return None

            base = time.monotonic()

            with self.override_lock:
                self.override_series.clear()
                for step in translated_series:
                    self.override_series.append({
                        "apply_at": base + float(step["dt"]),
                        "fields": step["fields"],
                    })

            print(f"[TELEM SERIES SCHEDULED] steps={len(self.override_series)}", flush=True)
            return parsed

        except Exception as e:
            print(f"[TELEM LLM ERROR] {e}", flush=True)
            return None

    def validate_telemetry_response(self, response: dict) -> bool:
        if not isinstance(response, dict):
            print("[TELEM VERIFY FAIL] response is not dict", flush=True)
            return False

        series = response.get("telemetry_series")
        if not isinstance(series, list):
            print("[TELEM VERIFY FAIL] telemetry_series missing or not list", flush=True)
            return False

        if len(series) != 5:
            print("[TELEM VERIFY FAIL] telemetry_series must have 5 steps", flush=True)
            return False

        expected_dts = [0.0, 0.1, 0.2, 0.3, 0.4]

        for i, step in enumerate(series):
            if not isinstance(step, dict):
                print("[TELEM VERIFY FAIL] step is not dict", flush=True)
                return False

            if "dt" not in step or "fields" not in step:
                print("[TELEM VERIFY FAIL] step missing dt or fields", flush=True)
                return False

            try:
                dt = round(float(step["dt"]), 1)
            except Exception:
                print("[TELEM VERIFY FAIL] dt is not numeric", flush=True)
                return False

            if dt != expected_dts[i]:
                print(f"[TELEM VERIFY FAIL] bad dt at index {i}: {dt}", flush=True)
                return False

            fields = step["fields"]
            if not isinstance(fields, dict):
                print("[TELEM VERIFY FAIL] fields is not dict", flush=True)
                return False

            for msg_name, msg_fields in fields.items():
                if msg_name not in TELEM_GROUPS_SET:
                    print(f"[TELEM VERIFY FAIL] invalid message group: {msg_name}", flush=True)
                    return False

                if not isinstance(msg_fields, dict):
                    print(f"[TELEM VERIFY FAIL] fields for {msg_name} must be dict", flush=True)
                    return False

                allowed_fields = TELEM_GROUPS_SET[msg_name]

                for field_name in msg_fields.keys():
                    if field_name not in allowed_fields:
                        print(f"[TELEM VERIFY FAIL] invalid field {msg_name}.{field_name}", flush=True)
                        return False

        return True
    # for telemetry based on commands /////////

    def handle_inbound_msg(self, msg):
        """
        # Handles inbound MAVLink messages from the GCS.

        # Priority order:
        # 1) Telemetry stream control (SET_MESSAGE_INTERVAL / MAV_CMD_SET_MESSAGE_INTERVAL)
        # - handled immediately
        # - always ACKed (ACCEPTED if supported else UNSUPPORTED)
        # - NEVER sent to the LLM

        # 2) Other COMMAND_LONG commands
        # - ACK immediately (so GCS sees a response)
        # - optionally send to LLM to generate state_patch + telemetry_series
        # - apply patch to state (which affects subsequent telemetry + heartbeat)

        update: Full patched handler:
        - Stream control (SET_MESSAGE_INTERVAL / MAV_CMD_SET_MESSAGE_INTERVAL): handled locally, ACKed, NOT sent to LLM
        - Other COMMAND_LONG: logged -> LLM (llm_prompt_command) -> apply_llm_command_result (ACK + patches + schedule series)
        - Everything else: ignore (or extend later)
            """
        msg_type = msg.get_type()
        attacker_msg = {"type": msg_type}

        # to fix the ack thing /////////
        # ts = int(getattr(msg, "target_system", 1))
        # tc = int(getattr(msg, "target_component", 1))
        # to fix the ack thing /////////

        # ts = msg.get_srcSystem()
        # tc = msg.get_srcComponent()    

        ts = int(getattr(msg, "get_srcSystem", lambda: 255)())
        tc = int(getattr(msg, "get_srcComponent", lambda: 190)())


        # ------------------------------------------------------------
        # (A) Direct SET_MESSAGE_INTERVAL message
        # ------------------------------------------------------------
        if msg_type == "SET_MESSAGE_INTERVAL":
            msg_id = int(getattr(msg, "message_id", -1))
            interval_us = int(getattr(msg, "interval_us", 0))

            handled = self._apply_message_interval_request(msg_id, interval_us)

            # Some GCS don't require ACK for SET_MESSAGE_INTERVAL,
            # but sending COMMAND_ACK for MAV_CMD_SET_MESSAGE_INTERVAL is harmless if you want.
            # We'll keep it quiet here to avoid confusing GCS tooling.
            return

        #  ////////// QGC ///////////////////////////////////////
        if msg_type == "PARAM_REQUEST_LIST":
            print("[PARAM] REQUEST_LIST -> sending params", flush=True)
            self._send_all_params()
            return

        if msg_type == "PARAM_REQUEST_READ":
            idx = int(getattr(msg, "param_index", -1))
            pid = getattr(msg, "param_id", b"")

            try:
                pname = pid.decode("ascii", errors="ignore").strip("\x00").strip()
            except Exception:
                pname = ""

            if 0 <= idx < len(self.params):
                name, val, ptype = self.params[idx]
                print(f"[PARAM] REQUEST_READ idx={idx} -> {name}", flush=True)
                self._send_param_value(name, val, ptype, idx, len(self.params))
                return

            if pname and pname in self.param_index:
                i = self.param_index[pname]
                name, val, ptype = self.params[i]
                print(f"[PARAM] REQUEST_READ name={pname} -> {name}", flush=True)
                self._send_param_value(name, val, ptype, i, len(self.params))
                return

            print(f"[PARAM] REQUEST_READ unknown idx={idx} name={pname!r}", flush=True)
            return
        #  ////////// QGC /////////////////////////////

        # ------------------------------------------------------------
        # (B) COMMAND_LONG: may include MAV_CMD_SET_MESSAGE_INTERVAL
        # ------------------------------------------------------------
        if msg_type == "COMMAND_LONG":
            cmd = int(getattr(msg, "command", -1))
            attacker_msg["command"] = cmd

            # ---------------------------
            # B1) Telemetry stream request
            # ---------------------------
            if cmd == mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL:
                # param1 = message_id, param2 = interval_us (often floats)
                msg_id = int(round(float(getattr(msg, "param1", -1.0))))
                interval_us = int(round(float(getattr(msg, "param2", 0.0))))

                handled = self._apply_message_interval_request(msg_id, interval_us)

                # ack = self.mav_out.command_ack_encode( #commented for ack handling
                #     mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, #commented for ack handling
                #     mavutil.mavlink.MAV_RESULT_ACCEPTED if handled else mavutil.mavlink.MAV_RESULT_UNSUPPORTED #commented for ack handling
                # ) #commented for ack handling
                # self.send_mav(ack) #commented for ack handling

                ack = self.mav_out.command_ack_encode(
                    mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                    mavutil.mavlink.MAV_RESULT_ACCEPTED if handled else mavutil.mavlink.MAV_RESULT_UNSUPPORTED                    # ,0,  # progress
                    # 0,  # result_param2
                    # ts, # target_system
                    # tc  # target_component
                    )
                self.send_mav(ack)
                return
            # ---------------------------
            # B2) Request handling #new///
            # ---------------------------
            if cmd == mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE:
                requested_msg_id = int(round(float(getattr(msg, "param1", -1.0))))
                requested_name = self._msg_name_from_id(requested_msg_id)

                print(f"[REQ MESSAGE] msg_id={requested_msg_id} name={requested_name}", flush=True)

                if requested_name == "HEARTBEAT":
                    with self.state_lock:
                        update_time_fields(self.state, self.boot_time)
                        out_msg = make_heartbeat(self.mav_out, self.state)

                    self.send_mav(out_msg)
                    print("[REQ MESSAGE SENT] HEARTBEAT", flush=True)

                    ack = self.mav_out.command_ack_encode(
                        mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                        mavutil.mavlink.MAV_RESULT_ACCEPTED
                    )
                    self.send_mav(ack)
                    return

                builder = TELEM_BUILDERS.get(requested_name)
                if builder:
                    try:
                        with self.state_lock:
                            update_time_fields(self.state, self.boot_time)
                            out_msg = builder(self.mav_out, self.state)

                        self.send_mav(out_msg)
                        print(f"[REQ MESSAGE SENT] {requested_name}", flush=True)

                        ack = self.mav_out.command_ack_encode(
                            mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                            mavutil.mavlink.MAV_RESULT_ACCEPTED
                        )
                        self.send_mav(ack)
                    except Exception as e:
                        print(f"[REQ MESSAGE ERROR] msg_id={requested_msg_id} name={requested_name} err={e}", flush=True)
                        ack = self.mav_out.command_ack_encode(
                            mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                            mavutil.mavlink.MAV_RESULT_UNSUPPORTED
                        )
                        self.send_mav(ack)
                else:
                    print(f"[REQ MESSAGE UNSUPPORTED] msg_id={requested_msg_id} name={requested_name}", flush=True)
                    ack = self.mav_out.command_ack_encode(
                        mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                        mavutil.mavlink.MAV_RESULT_UNSUPPORTED
                    )
                    self.send_mav(ack)
                return
            # ---------------------------
            # B3) Other commands (ARM/DISARM/TAKEOFF/etc.)
            # ---------------------------

            # --- B2) All other commands: route to LLM command pipeline ---
            params = {
                "param1": float(getattr(msg, "param1", 0.0)),
                "param2": float(getattr(msg, "param2", 0.0)),
                "param3": float(getattr(msg, "param3", 0.0)),
                "param4": float(getattr(msg, "param4", 0.0)),
                "param5": float(getattr(msg, "param5", 0.0)),
                "param6": float(getattr(msg, "param6", 0.0)),
                "param7": float(getattr(msg, "param7", 0.0)),
            }

            # ---- NEW: rule-based ACK ----
            result, reason= rule_based_ack(cmd, params, self.state)
            
            result_name = mavutil.mavlink.enums["MAV_RESULT"][int(result)].name #debug
            print(f"[ACK RULE] cmd={cmd} result={result_name} reason={reason}", flush=True) #debug


            # ack_msg = self.mav_out.command_ack_encode(cmd, result) #commented for ack handling
            # self.send_mav(ack_msg) #commented for ack handling
            # ack_msg = self.mav_out.command_ack_encode(
            #     cmd, result,
            #     0,  # progress
            #     0,  # result_param2
            #     ts, # target_system
            #     tc  # target_component
            # )
            # ack_msg = self.mav_out.command_ack_encode(cmd, result)
            # self.send_mav(ack_msg)

            # //// delete below if fails
            # try:
            #     # old pymavlink signature (only command, result)
            #     ack_msg = self.mav_out.command_ack_encode(int(cmd), int(result))
            # except TypeError:
            #     # newer message shape with target fields
            #     ack_msg = mavutil.mavlink.MAVLink_command_ack_message(
            #         int(cmd), int(result), 0, 0, int(ts), int(tc)
            #     )
            # self.send_mav(ack_msg)
            # //// delete above if fails

            ack_msg = mavutil.mavlink.MAVLink_command_ack_message(
                int(cmd), int(result)
            )
            print(f"[ACK TX] cmd={cmd} result={result}", flush=True)
            self.send_mav(ack_msg)          

            #  pending: log the ack as well:


            # log command into history (so LLM sees it next time)
            self.hist.add_cmd(cmd, params)




            # minimal runtime log (easy to see in terminal)
            print(f"[CMD RX] id={cmd} params={params}", flush=True)

            # optional persistent log (recommended)
            try:
                with open("cmd_rx_log.jsonl", "a") as f:
                    f.write(json.dumps({"ts": time.time(), "cmd": cmd, "params": params}) + "\n")
            except Exception as e:
                print(f"[CMD LOG ERROR] {e}", flush=True)

            # # if LLM disabled, immediately UNSUPPORTED (or ACCEPTED if you prefer)
            # if not getattr(self, "llm_enabled", False):
            #     ack_msg = self.mav_out.command_ack_encode(cmd, mavutil.mavlink.MAV_RESULT_UNSUPPORTED)
            #     self.send_mav(ack_msg)
            #     return

            # /////////////////////////////////////////////////////
            # resp_wrap = self.llm_prompt_command(cmd, params)

            # # 4) write one combined log row (rule ack + llm ack)
            # os.makedirs("./logs", exist_ok=True)
            # llm_ack = (resp_wrap.get("_parsed") or {}).get("ack", {}) if resp_wrap else {}
            # with open("./logs/cmd_ack_llm_log.jsonl", "a") as f:
            #     f.write(json.dumps({
            #         "ts": time.time(),
            #         "cmd": int(cmd),
            #         "params": params,
            #         "ack_rule": {"result_int": int(result), "reason": reason},
            #         "ack_llm": llm_ack,
            #         "llm_raw": resp_wrap.get("_raw","") if resp_wrap else "",
            #         "llm_parsed": resp_wrap.get("_parsed") if resp_wrap else None
            #     }) + "\n")

            # # 5) apply telemetry scheduling only
            # if resp_wrap and resp_wrap.get("_parsed"):
            #     self.apply_llm_command_result(resp_wrap["_parsed"])
            # else:
            #     print(f"[LLM CMD FAIL] id={cmd} (no telemetry scheduled)", flush=True)

            # return
            # /////////////////////////////////////////////////////
            skip_llm = lambda c: c in {521}

            if skip_llm(cmd):
                print(f"[LLM SKIP] cmd={cmd}", flush=True)
                return
                #the above skip_llm is supposed to stop bogus functions affecting the llm response.

            # 1) heartbeat-only LLM module
            hb_resp = self.handle_command_heartbeat(cmd, params)

            # 2) telemetry module will be added later
            telem_resp = self.handle_command_telemetry(cmd, params)

            return

        # ------------------------------------------------------------
        # Other message types (optional: track or ignore)
        # ------------------------------------------------------------
        return



        # ////////////// telemetry /////////////////////



        # commenting for static heartbeat testing ////////////////////////

                # if self.llm_enabled:
                #     response = self.llm_prompt(attacker_msg)

                #     if response and self.verify_response(response):
                #         self.llm_logging(attacker_msg, response)
                #         self.llm_output_process(response)
        # commenting for static heartbeat testing ////////////////////////



    # ---------------------------
    # run()  (Main loop)
    # ---------------------------
    def run(self):

        # threading.Thread(target=self.heartbeat_loop, daemon=True).start()


        # ///////////// HEARTBEAT /////////////////////////
        # 1️⃣ Initialize identity
        self.initialize_heartbeat_identity()

        # 2️⃣ Start heartbeat thread
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        # ///////////// HEARTBEAT /////////////////////////


        # 3️⃣ Start request-driven telemetry scheduler
        self.telemetry_thread.start()
        self.enable_default_telem_streams() # new ///

        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
                # print("[RX UDP addr]", addr, "len=", len(data), flush=True) ##debug dlt later
            except socket.timeout:
                continue

            # GCS detection
            if self.gcs_addr is None:
                self.gcs_addr = addr
                print(f"[GCS CONNECTED] {self.gcs_addr}")

            # MAVLink parsing
            for byte in data:
                msg = self.mav_in.parse_char(bytes([byte]))
                if msg:
                    self.handle_inbound_msg(msg)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    hp = LLMHoneypot()
    hp.run()
