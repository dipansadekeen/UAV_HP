import json
import time

from typing import List, Dict, Any, Optional
from pymavlink import mavutil

from module_historybuffer_commonstate import CommonState
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