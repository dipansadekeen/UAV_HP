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

import json, os, re, math
from collections import defaultdict, deque

# ----------- RAG Integration -----
RAG_FILES = [
    "../RAGS/mavlink_rag_corpus.jsonl",
    "../out3/px4_command_sequences.jsonl",
    "../out3/px4_command_trace.jsonl",
]
# ----------- RAG Integration end module-----------



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
    battery_remaining: int = 90           # %
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
    # LOCAL_POSITION_NED
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
    # ATTITUDE_QUATERNION
    # ------------------------
    aq_time_boot_ms: int = 0
    q1: float = 1.0
    q2: float = 0.0
    q3: float = 0.0
    q4: float = 0.0
    repr_offset_q: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    # ------------------------
    # ATTITUDE_TARGET
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

@dataclass
class StateSample:
    t: float
    fields: Dict[str, Any]

class StateBuffer:
    """
    Ring buffer of StateSample (time-ordered).
    - LLM worker appends future samples
    - Scheduler consumes samples up to 'now'
    """
    def __init__(self, maxlen: int = 4000):
        self._buf: Deque[StateSample] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append_samples(self, samples: List[StateSample]) -> None:
        if not samples:
            return
        with self._lock:
            for s in samples:
                self._buf.append(s)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def remaining_sec(self, now: float) -> float:
        with self._lock:
            if not self._buf:
                return 0.0
            return max(0.0, self._buf[-1].t - now)

    def get_sample(self, now: float) -> Optional[StateSample]:
        """
        Returns the latest sample at/before now.
        If only future samples exist, return earliest future sample.
        """
        with self._lock:
            if not self._buf:
                return None

            last = None
            while self._buf and self._buf[0].t <= now:
                last = self._buf.popleft()

            if last is not None:
                return last

            return self._buf[0] if self._buf else None


    def tail_time(self) -> float: #-- LLM-integration | Add buffer “tail time” so new batches append FIFO correctly
        with self._lock:
            return self._buf[-1].t if self._buf else 0.0



# ------------------------ LLM Working ------------------------
    # def llm_worker_loop(self) -> None:
    #     while not self.stop_event.is_set():
    #         self.refill_event.wait(timeout=0.5)
    #         if self.stop_event.is_set():
    #             return
    #         if not self.refill_event.is_set():
    #             continue
    #         self.refill_event.clear()

    #         # no GCS → nothing to do
    #         if not self.gcs_addr:
    #             continue

    #         now = time.monotonic()

    #         with self.state_lock:
    #             state_snapshot = dict(self.common_state.__dict__)

    #         inbound_snapshot = list(self.inbound_log)[-10:]

    #         # ---- Retrieval query ----
    #         last = inbound_snapshot[-1] if inbound_snapshot else {}
    #         query = (
    #             f"type={last.get('type','')} "
    #             f"fields={json.dumps(last.get('fields', {}), sort_keys=True)[:250]} "
    #             f"armed={state_snapshot.get('armed',0)} mode={state_snapshot.get('flight_mode','')}"
    #         )

    #         hits = self.retriever.search(query, k=6)
    #         hit_text = "\n".join([f"- {entry_to_text(h)[:450]}" for h in hits])

    #         # ---- LLM prompt ----
    #         prompt = f"""
    # You simulate PX4-like behavior for a MAVLink honeypot.

    # {LLM_OUTPUT_RULES}

    # CURRENT_STATE:
    # {json.dumps(state_snapshot, indent=2, sort_keys=True)}

    # RECENT_INBOUND:
    # {json.dumps(inbound_snapshot, indent=2, sort_keys=True)}

    # RETRIEVED_EXAMPLES:
    # {hit_text}

    # Now output JSON only.
    # """.strip()

    #         try:
    #             llm_text = call_ollama(prompt)
    #             obj = extract_json(llm_text)
    #         except Exception:
    #             obj = None

    #         if not isinstance(obj, dict):
    #             # fallback: keep constant chunk so stream never stops
    #             self._append_constant_samples(now, state_snapshot)
    #             # debugging +
    #             self.llm_fallback += 1
    #             print(f"[LLM] FALLBACK #{self.llm_fallback}", flush=True)
    #             # debugging +
    #             continue

    #         # ---- apply immediate patch ----
    #         patch = keep_only_commonstate_fields(obj.get("state_patch", {}), self.allowed_fields)
    #         with self.state_lock:
    #             for k, v in patch.items():
    #                 setattr(self.common_state, k, v)

    #         # ---- apply time-series into buffer ----
    #         series = normalize_series(obj.get("telemetry_series", []))
    #         if not series:
    #             self._append_constant_samples(now, state_snapshot)
    #             continue

    #         samples = []
    #         base = dict(state_snapshot)
    #         base.update(patch)  # ensure series starts from updated state
    #         for it in series:
    #             dt = float(it["dt"])
    #             fields = keep_only_commonstate_fields(it.get("fields", {}), self.allowed_fields)
    #             merged = dict(base)
    #             merged.update(fields)
    #             samples.append(StateSample(t=now + dt, fields=merged))

    #         self.buffer.append_samples(samples)


# ------------------------ LLM Working end module ------------------------

class StreamTable:
    """
    Thread-safe: stream_name -> StreamConfig(rate_hz, next_send)
    Updated by inbound handler / session init, read by scheduler.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._streams: Dict[str, StreamConfig] = {}

    def set_rate(self, name: str, rate_hz: float, now: float) -> None:
        if rate_hz <= 0:
            return
        with self._lock:
            self._streams[name] = StreamConfig(rate_hz=float(rate_hz), next_send=float(now))

    def remove(self, name: str) -> None:
        with self._lock:
            self._streams.pop(name, None)

    def snapshot(self) -> Dict[str, StreamConfig]:
        # Return a copy so scheduler can iterate without holding lock
        with self._lock:
            return dict(self._streams)
        

import time
import math

def update_time_fields(s: CommonState, boot_time: float, now: float) -> None:
    dt = max(0.0, now - boot_time)
    s.time_boot_ms = int(dt * 1000.0)
    s.time_usec = int(dt * 1_000_000.0)

    # mirror convenience timestamps
    s.gps_time_usec = s.time_usec
    s.gpi_time_boot_ms = s.time_boot_ms
    s.lpn_time_boot_ms = s.time_boot_ms
    s.att_time_boot_ms = s.time_boot_ms
    s.aq_time_boot_ms = s.time_boot_ms
    s.at_time_boot_ms = s.time_boot_ms
    s.ptl_time_boot_ms = s.time_boot_ms
    s.ptg_time_boot_ms = s.time_boot_ms
    s.servo_time_usec = s.time_usec


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
    # Base GPS_RAW_INT fields
    # time_usec, fix_type, lat, lon, alt, eph, epv, vel, cog, satellites_visible
    msg = mav.gps_raw_int_encode(
        int(s.gps_time_usec),
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

    # Optional MAVLink 2 extension fields (only exist in some dialects)
    # Safely attach if attributes exist on message
    if hasattr(msg, "alt_ellipsoid"):
        msg.alt_ellipsoid = int(s.gps_alt_ellipsoid)
    if hasattr(msg, "h_acc"):
        msg.h_acc = int(s.gps_h_acc)
    if hasattr(msg, "v_acc"):
        msg.v_acc = int(s.gps_v_acc)
    if hasattr(msg, "vel_acc"):
        msg.vel_acc = int(s.gps_vel_acc)
    if hasattr(msg, "hdg_acc"):
        msg.hdg_acc = int(s.gps_hdg_acc)
    if hasattr(msg, "yaw"):
        msg.yaw = int(s.gps_yaw)

    return msg

def make_local_position_ned(mav, s: CommonState):
    return mav.local_position_ned_encode(
        int(s.lpn_time_boot_ms),
        float(s.lpn_x),
        float(s.lpn_y),
        float(s.lpn_z),
        float(s.lpn_vx),
        float(s.lpn_vy),
        float(s.lpn_vz),
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

def make_heartbeat(mav, s: CommonState):
    return mav.heartbeat_encode(
        int(s.hb_type),
        int(s.hb_autopilot),
        int(s.base_mode),
        int(s.custom_mode),
        int(s.system_status),
    )

def make_global_position_int(mav, s: CommonState):
    return mav.global_position_int_encode(
        int(s.gpi_time_boot_ms),     # time_boot_ms
        int(s.gpi_lat),              # lat (1E7)
        int(s.gpi_lon),              # lon (1E7)
        int(s.gpi_alt),              # alt (mm, MSL)
        int(s.gpi_relative_alt),     # relative_alt (mm)
        int(s.gpi_vx),               # vx (cm/s)
        int(s.gpi_vy),               # vy (cm/s)
        int(s.gpi_vz),               # vz (cm/s)
        int(s.gpi_hdg),              # heading (cdeg, 0..35999)
    )

def make_attitude(mav, s: CommonState):
    return mav.attitude_encode(
        int(s.att_time_boot_ms),
        float(s.roll),
        float(s.pitch),
        float(s.yaw),
        float(s.rollspeed),
        float(s.pitchspeed),
        float(s.yawspeed),
    )


TELEM_BUILDERS = {
    "HEARTBEAT": make_heartbeat,
    "SYS_STATUS": make_sys_status,
    "GPS_RAW_INT": make_gps_raw_int,
    "GLOBAL_POSITION_INT": make_global_position_int,
    "ATTITUDE": make_attitude,
    "LOCAL_POSITION_NED": make_local_position_ned,
    "VFR_HUD": make_vfr_hud,
}

# ------------------------ RAG Integration module ------------------
RAG_FILES = [
    "/RAG/mavlink_rag_corpus.jsonl",
    "/out3/px4_command_sequences.jsonl",
    "/out3/px4_command_trace.jsonl",
]

def load_jsonl(path: str, limit: int = 20000):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out

def entry_to_text(e: dict) -> str:
    """
    Make every JSONL row searchable even if schemas differ.
    """
    parts = []
    # common keys across different dumps
    for k in ["msg_name", "mavpackettype", "command", "label", "attack", "benign", "event", "name"]:
        if k in e:
            parts.append(f"{k}={e.get(k)}")

    for k in ["fields", "msg_fields", "payload", "request", "response", "inbound", "outbound"]:
        v = e.get(k)
        if isinstance(v, dict):
            small = {kk: v[kk] for kk in list(v.keys())[:12]}
            parts.append(f"{k}:{json.dumps(small, sort_keys=True)}")
        elif isinstance(v, str):
            parts.append(f"{k}:{v[:250]}")

    if not parts:
        try:
            parts.append(json.dumps(e, sort_keys=True)[:400])
        except Exception:
            parts.append(str(e)[:400])

    return " ".join(parts)

class SimpleRetriever:
    """
    Token-overlap retriever (no extra libs).
    """
    def __init__(self, docs):
        self.docs = docs
        self.texts = [entry_to_text(d) for d in docs]
        self.tokens = [set(re.findall(r"[A-Za-z0-9_]+", t.lower())) for t in self.texts]

    def search(self, query: str, k: int = 6):
        qtok = set(re.findall(r"[A-Za-z0-9_]+", query.lower()))
        if not qtok:
            return []
        scored = []
        for i, dtok in enumerate(self.tokens):
            inter = len(qtok & dtok)
            if inter <= 0:
                continue
            score = inter / math.sqrt(max(1, len(dtok)))
            scored.append((score, i))
        scored.sort(reverse=True)
        return [self.docs[i] for _, i in scored[:k]]

# ------------------------ RAG Integration end module------------------



# ------------------------ LLM inferece --------------------------
import requests

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
- gps_lat, gps_lon, gpi_lat, gpi_lon: int = degrees * 1e7
- gps_alt, gpi_alt, gpi_relative_alt: int millimeters
- gps_vel: int cm/s
- gps_cog, gpi_hdg: int centi-degrees (0..35999)
- roll, pitch, yaw: radians
"""

# def call_ollama(system_text: str, user_text: str) -> str:
def call_ollama(system_text: str, user_text: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text}
        ],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "top_p": 1
        }
    }

    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json=payload,
        timeout=OLLAMA_TIMEOUT_SEC,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]

def extract_json(text: str):
    # Pull first {...} block
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

def keep_only_commonstate_fields(patch: dict, allowed: set):
    if not isinstance(patch, dict):
        return {}
    out = {}
    for k, v in patch.items():
        if k in allowed:
            out[k] = v
    return out

def normalize_series(series):
    if not isinstance(series, list):
        return []
    out = []
    last_dt = -1.0
    for it in series:
        if not isinstance(it, dict):
            continue
        try:
            dt = float(it.get("dt", -1))
        except Exception:
            continue
        if dt < 0:
            continue
        if dt < last_dt:
            dt = last_dt
        last_dt = dt
        fields = it.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}
        out.append({"dt": dt, "fields": fields})
    return out



#--------- debug ------
def est_hz(self, name: str) -> float:
    ts = list(self.tx_times.get(name, []))
    if len(ts) < 2:
        return 0.0
    dt = ts[-1] - ts[0]
    return (len(ts) - 1) / dt if dt > 0 else 0.0
#---------- debug ends -----




#  ------------------------ LLM inference end module --------------------------


class LLMHoneypot:

    def __init__(self,
                 listen_ip: str = "0.0.0.0",
                 listen_port: int = 14550):

        # ================================
        # Network / MAVLink Setup
        # ================================
        self.listen_ip = listen_ip
        self.listen_port = listen_port

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # self.sock.bind((self.listen_ip, self.listen_port)) #//commented to connect to QGC
        self.sock.bind(("127.0.0.1", 14551))
        self.gcs_addr = ("127.0.0.1", 14550)
        
        self.sock.settimeout(0.2)

        # MAVLink encoder/decoder (MAVLink 2)
        self.mav_out = mavutil.mavlink.MAVLink(None)
        self.mav_out.srcSystem = 1
        self.mav_out.srcComponent = 1
        self.mav_out.robust_parsing = True

        self.mav_in = mavutil.mavlink.MAVLink(None)

        # GCS address (learned dynamically)
        #self.gcs_addr = None # for qgc

        # ================================
        # Boot Time Reference
        # ================================
        self.boot_time = time.monotonic()

        # ================================
        # Shared State
        # ================================
        self.state_lock = threading.Lock()
        self.common_state = CommonState()

        # ================================
        # Telemetry Stream Table
        # ================================
        self.streams = StreamTable()

        # Only HEARTBEAT enabled by default
        now = time.monotonic()
        self.streams.set_rate("HEARTBEAT", 1.0, now)

        # ================================
        # LLM State Buffer
        # ================================
        self.buffer = StateBuffer()

        # ================================
        # Thread Coordination
        # ================================
        self.stop_event = threading.Event()
        self.refill_event = threading.Event()

        # ================================
        # Scheduler Parameters
        # ================================
        self.scheduler_tick_hz = 10.0  # internal state tick
        self.scheduler_dt = 1.0 / self.scheduler_tick_hz

        self.buffer_target_sec = 3.0
        self.buffer_low_watermark_sec = 1.0

        # ================================
        # Threads
        # ================================
        self.scheduler_thread = threading.Thread(
            target=self.scheduler_loop,
            daemon=True
        )

        self.llm_thread = threading.Thread(
            target=self.llm_worker_loop,
            daemon=True
        )

        # ================================
        # Optional: Inbound Stats Window
        # ================================
        self.recent_inbound = []
        self.recent_window_sec = 3.0


        self.last_sent_state = dict(self.common_state.__dict__) # merged state last streamed  # LLM-integration

        # ================================
        # Logging / Debug
        # ================================
        print(f"[INIT] LLM-Core MAVLink Honeypot")
        print(f"[INIT] Listening on {self.listen_ip}:{self.listen_port}")
        print(f"[INIT] HEARTBEAT stream initialized at 1 Hz")


# ----------------- LLM inference ------------
        # --- inside __init__ after your existing prints / config ---
        self.rag_docs = []
        for p in RAG_FILES:
            docs = load_jsonl(p, limit=20000)
            if docs:
                print(f"[RAG] loaded {len(docs)} from {p}")
            self.rag_docs.extend(docs)

        self.retriever = SimpleRetriever(self.rag_docs)
        self.allowed_fields = set(CommonState().__dict__.keys())

        # keep last inbound MAVLink messages for LLM context
        self.inbound_log = deque(maxlen=30)

        self.tx_counts = defaultdict(int) #debugging 
        self.tx_times = defaultdict(lambda: deque(maxlen=200))  #debugging
#debugging llm call
        self.llm_calls = 0
        self.llm_ok = 0
        self.llm_fallback = 0

# ---------------- LLM inference ends ------------

    def streams_for_prompt(self) -> dict: #LLM-integration | Add Active Streams into prompt (so LLM knows what attacker asked)
        snap = self.streams.snapshot()
        return {k: {"rate_hz": v.rate_hz, "next_send": v.next_send} for k, v in snap.items()}



    def run(self):
        print(f"[HONEYPOT] Listening on {self.listen_ip}:{self.listen_port}")

        # Start background threads
        self.scheduler_thread.start()
        self.llm_thread.start()

        try:
            while not self.stop_event.is_set():
                try:
                    data, addr = self.sock.recvfrom(4096)
                except socket.timeout:
                    continue

                # -----------------------------------
                # 1) Detect GCS (first contact)
                # -----------------------------------
                if self.gcs_addr is None:
                    self.gcs_addr = addr
                    print(f"[HONEYPOT] GCS detected: {self.gcs_addr}")

                    now = time.monotonic()

                    # Enable default telemetry streams
                    self.streams.set_rate("HEARTBEAT", 1.0, now)
                    self.streams.set_rate("SYS_STATUS", 2.0, now)
                    self.streams.set_rate("GPS_RAW_INT", 2.0, now)
                    self.streams.set_rate("GLOBAL_POSITION_INT", 5.0, now)
                    self.streams.set_rate("ATTITUDE", 10.0, now)
                    self.streams.set_rate("LOCAL_POSITION_NED", 10.0, now)
                    self.streams.set_rate("VFR_HUD", 3.0, now)

                    # Trigger initial LLM chunk
                    self.refill_event.set()

                # -----------------------------------
                # 2) Parse MAVLink bytes
                # -----------------------------------
                for byte in data:
                    # msg = self.mav_in.parse_char(bytes([byte]))
                    # if msg:
                    #     self.handle_inbound_msg(msg)
                    try:
                        msg = self.mav_in.parse_char(bytes([byte]))
                    except Exception as e:
                        print("[RX] parse_char exception:", repr(e), flush=True)
                        continue

                    if not msg:
                        continue

                    try:
                        self.handle_inbound_msg(msg)
                    except Exception as e:
                        # THIS is the #1 fix: prevents honeypot from dying silently
                        print("[RX] handle_inbound_msg crashed:", repr(e), "type=", msg.get_type(), flush=True)

        except KeyboardInterrupt:
            print("\n[HONEYPOT] Shutting down...")

        finally:
            self.stop_event.set()
            self.refill_event.set()
            self.scheduler_thread.join()
            self.llm_thread.join()


    def send_mav(self, msg) -> None:
        """Pack + send a MAVLink message to current GCS."""
        name = msg.get_type() # debugging
        self.tx_counts[name] += 1
        self.tx_times[name].append(time.monotonic()) #debugging

        if not self.gcs_addr:
            return
        try:
            pkt = msg.pack(self.mav_out)
            self.sock.sendto(pkt, self.gcs_addr)
        except Exception:
            pass

    def _state_for_send(self, now: float) -> CommonState:
        """
        Build the state object used for telemetry at time 'now'.
        - Prefer buffer sample (future/past) if available
        - Otherwise use common_state
        """
        sample = self.buffer.get_sample(now)

        with self.state_lock:
            # Always keep time fields consistent
            update_time_fields(self.common_state, self.boot_time, now)

            if sample is None:
                return self.common_state

            # Apply sample fields onto a copy of common_state for this send
            s = CommonState(**self.common_state.__dict__)
            for k, v in sample.fields.items():
                if hasattr(s, k):
                    setattr(s, k, v)

            # Ensure time mirrors also correct on the send-state
            update_time_fields(s, self.boot_time, now)

            # at end of _state_for_send(), before "return s" # LLM-integration
            self.last_sent_state = dict(s.__dict__)
            return s

    def scheduler_loop(self) -> None:
        """
        Single timing engine:
          - Sends streams according to StreamTable next_send times
          - Never blocks on LLM
        """
        tick_dt = 1.0 / max(1.0, float(self.scheduler_tick_hz))

        while not self.stop_event.is_set():
            now = time.monotonic()

            # Trigger refill if buffer low (LLM thread may ignore for now)
            if self.buffer.remaining_sec(now) < float(self.buffer_low_watermark_sec):
                self.refill_event.set()

            # Snapshot streams
            streams = self.streams.snapshot()

            # Build state snapshot for this tick
            s = self._state_for_send(now)

            for name, cfg in streams.items():
                if now < cfg.next_send:
                    continue

                builder = TELEM_BUILDERS.get(name)
                if builder and self.gcs_addr:
                    try:
                        msg = builder(self.mav_out, s)
                        self.send_mav(msg)
                    except Exception:
                        pass

                # schedule next time
                period = 1.0 / max(0.0001, float(cfg.rate_hz))
                # update next_send (simple approach: overwrite)
                self.streams.set_rate(name, cfg.rate_hz, now + period)

            time.sleep(tick_dt)

    def llm_worker_loop(self) -> None:
        '''
        This version:
        -builds a RAG query using last inbound command + current state
        -retrieves examples from out3
        -calls Ollama
        -validates output schema
        -appends samples after existing buffer tail (FIFO)
        '''

        SYSTEM_TEXT = (
            "You are a PX4 autopilot telemetry generator for a MAVLink honeypot. "
            "Return JSON only. No prose."
        )

        while not self.stop_event.is_set():
            self.refill_event.wait(timeout=0.5)
            if self.stop_event.is_set():
                return
            if not self.refill_event.is_set():
                continue
            self.refill_event.clear()

            # no GCS yet
            if not self.gcs_addr:
                continue

            now = time.monotonic()

            # anchor: append after whatever is already queued (FIFO)
            tail = self.buffer.tail_time()
            anchor = max(now, tail)

            # "current" state = what was last streamed (merged)
            with self.state_lock:
                state_snapshot = dict(self.last_sent_state) if isinstance(self.last_sent_state, dict) else dict(self.common_state.__dict__)

            inbound_snapshot = list(self.inbound_log)[-10:]
            last = inbound_snapshot[-1] if inbound_snapshot else {}

            active_streams = self.streams_for_prompt()

            # retrieval query: command type + key params + mode/basics
            query = (
                f"type={last.get('type','')} "
                f"fields={json.dumps(last.get('fields', {}), sort_keys=True)[:250]} "
                f"base_mode={state_snapshot.get('base_mode',0)} "
                f"system_status={state_snapshot.get('system_status',0)} "
                f"armed={(1 if (state_snapshot.get('base_mode',0) & 0x80) else 0)}"
            )

            hits = self.retriever.search(query, k=6)
            hit_text = "\n".join([f"- {entry_to_text(h)[:450]}" for h in hits])

            # user instruction: generate only telemetry, no protocol replies
            USER_TEXT = f"""
    {LLM_OUTPUT_RULES}

    IMPORTANT:
    - Generate ONLY telemetry behavior (state_patch + telemetry_series).
    - Do NOT invent MAVLink message packets.
    - Keep values plausible and smooth.

    CURRENT_STATE:
    {json.dumps(state_snapshot, indent=2, sort_keys=True)}

    ACTIVE_STREAMS:
    {json.dumps(active_streams, indent=2, sort_keys=True)}

    RECENT_INBOUND:
    {json.dumps(inbound_snapshot, indent=2, sort_keys=True)}

    RETRIEVED_EXAMPLES (from out3):
    {hit_text}

    Now output JSON only.
    """.strip()

            try:
                # debugging +
                self.llm_calls += 1
                print(f"[LLM] call #{self.llm_calls}", flush=True)
                #debugging +

                llm_text = call_ollama(SYSTEM_TEXT, USER_TEXT)
                obj = extract_json(llm_text)

                # debugging +
                if isinstance(obj, dict):
                    self.llm_ok += 1
                    print(f"[LLM] ok #{self.llm_ok}", flush=True)
                else:
                    print("[LLM] bad_json", flush=True)
                # debugging +
            except Exception:
                obj = None

            if not isinstance(obj, dict):
                # fallback: keep buffer alive
                self._append_constant_samples(anchor, state_snapshot)

                # debugging +
                self.llm_fallback += 1
                print(f"[LLM] FALLBACK #{self.llm_fallback}", flush=True)
                # debugging +
                continue

            # apply immediate patch (optional)
            patch = keep_only_commonstate_fields(obj.get("state_patch", {}), self.allowed_fields)

            # sanitize patch lightly (basic bounds)
            patch = self._sanitize_fields(patch, state_snapshot, dt=0.1)
            print("\n[DEBUG PATCH GPS]", patch.get("gps_lat"), patch.get("gps_lon"), flush=True) #delete / comment later
            if patch:
                delta = {k: {"old": state_snapshot.get(k), "new": v}
                        for k, v in patch.items()
                        if state_snapshot.get(k) != v}
                if delta:
                    print("[LLM_PATCH_DELTA]", json.dumps(delta, sort_keys=True), flush=True)

            with self.state_lock:
                for k, v in patch.items():
                    setattr(self.common_state, k, v)

            series = normalize_series(obj.get("telemetry_series", []))
            if not series:
                self._append_constant_samples(anchor, state_snapshot)
                # debugging +
                self.llm_fallback += 1
                print(f"[LLM] FALLBACK #{self.llm_fallback}", flush=True)
                # debugging +
                continue

            # build samples appended AFTER anchor
            base = dict(state_snapshot)
            base.update(patch)

            samples = []
            prev = dict(base)
            last_dt = 0.0

            for it in series:
                dt_rel = float(it["dt"])  # relative inside the generated chunk
                if dt_rel < last_dt:
                    dt_rel = last_dt
                last_dt = dt_rel

                fields = keep_only_commonstate_fields(it.get("fields", {}), self.allowed_fields)
                fields = self._sanitize_fields(fields, prev, dt=max(0.05, dt_rel - (samples[-1].t - anchor) if samples else dt_rel))

                merged = dict(base)
                merged.update(fields)

                samples.append(StateSample(t=anchor + dt_rel, fields=merged))
                prev = dict(merged)

            self.buffer.append_samples(samples)


    # def handle_inbound_msg(self, msg) -> None:
    #     try:
    #         d = msg.to_dict()
    #     except Exception:
    #         d = {"mavpackettype": getattr(msg, "get_type", lambda: "UNKNOWN")()}

    #     self.inbound_log.append({
    #         "t": time.time(),
    #         "type": d.get("mavpackettype", "UNKNOWN"),
    #         "fields": {k: v for k, v in d.items() if k != "mavpackettype"},
    #     })

    #     # Any inbound event can trigger more realistic reaction
    #     # (commands especially should trigger it)
    #     if d.get("mavpackettype") in ("COMMAND_LONG", "SET_MODE", "MISSION_ITEM", "MISSION_COUNT"):
    #         self.refill_event.set()


    def _append_constant_samples(self, now: float, base: dict):
        dt = 0.1
        horizon = float(self.buffer_target_sec)
        n = max(1, int(horizon / dt))
        samples = [StateSample(t=now + i * dt, fields=dict(base)) for i in range(n)]
        self.buffer.append_samples(samples)

    # LLM-integration | sanitization to prevent absurd altitude/lon/lat
    def _clamp(self, x, lo, hi):
        try:
            x = float(x)
        except Exception:
            return lo
        if x < lo: return lo
        if x > hi: return hi
        return x

    def _wrap_cdeg(self, x):
        try:
            x = int(x)
        except Exception:
            return 0
        x %= 36000
        if x < 0: x += 36000
        return x

    def _sanitize_fields(self, fields: dict, prev: dict, dt: float = 0.1) -> dict:
        if not isinstance(fields, dict):
            return {}

        out = dict(fields)

        # battery
        if "battery_remaining" in out:
            out["battery_remaining"] = int(self._clamp(out["battery_remaining"], 0, 100))

        # ---------- PASTE THIS BLOCK HERE ----------
        # def _latlon_to_1e7(v, is_lat: bool):
        #     try:
        #         fv = float(v)
        #     except Exception:
        #         return 0
        #     # if looks like degrees, convert to 1e7
        #     if abs(fv) <= 180.0:
        #         fv = fv * 1e7
        #     lo = -90e7 if is_lat else -180e7
        #     hi =  90e7 if is_lat else  180e7
        #     if fv < lo: fv = lo
        #     if fv > hi: fv = hi
        #     return int(fv)
    

        def _latlon_to_1e7(v, is_lat: bool):
            """
            Accepts common LLM mistakes and normalizes to MAVLink 1e7 integer degrees.
            Handles:
            - degrees (e.g., 37.77)
            - deg*1e4 (e.g., 377700 -> 37.7700°)
            - deg*1e5
            - deg*1e6
            - deg*1e7 (already correct)
            """
            try:
                fv = float(v)
            except Exception:
                return 0

            av = abs(fv)

            # 1) plain degrees
            if av <= 180.0:
                fv = fv * 1e7

            # 2) scaled degrees but not 1e7 yet
            #    detect based on magnitude
            elif av <= 180.0 * 1e4:     # deg*1e4  -> multiply by 1e3
                fv = fv * 1e3
            elif av <= 180.0 * 1e5:     # deg*1e5  -> multiply by 1e2
                fv = fv * 1e2
            elif av <= 180.0 * 1e6:     # deg*1e6  -> multiply by 10
                fv = fv * 10.0
            else:
                # assume already deg*1e7 or something huge; just clamp below
                pass

            lo = -90e7 if is_lat else -180e7
            hi =  90e7 if is_lat else  180e7
            if fv < lo: fv = lo
            if fv > hi: fv = hi
            return int(fv)



        if "gps_lat" in out:
            out["gps_lat"] = _latlon_to_1e7(out["gps_lat"], True)
        if "gps_lon" in out:
            out["gps_lon"] = _latlon_to_1e7(out["gps_lon"], False)

        if "gpi_lat" in out:
            out["gpi_lat"] = _latlon_to_1e7(out["gpi_lat"], True)
        if "gpi_lon" in out:
            out["gpi_lon"] = _latlon_to_1e7(out["gpi_lon"], False)
        # ---------- END PASTE BLOCK ----------



        # # lat/lon (1e7 degrees)
        # if "gpi_lat" in out:
        #     out["gpi_lat"] = int(self._clamp(out["gpi_lat"], -90e7, 90e7))
        # if "gpi_lon" in out:
        #     out["gpi_lon"] = int(self._clamp(out["gpi_lon"], -180e7, 180e7))

        # relative alt must not go below 0
        if "gpi_relative_alt" in out:
            out["gpi_relative_alt"] = int(max(0, int(out["gpi_relative_alt"])))

        # heading cdeg
        if "gpi_hdg" in out:
            out["gpi_hdg"] = self._wrap_cdeg(out["gpi_hdg"])

        # roll/pitch/yaw rad (gentle)
        for k in ("roll", "pitch"):
            if k in out:
                out[k] = float(self._clamp(out[k], -1.3, 1.3))
        if "yaw" in out:
            # wrap yaw to [-pi, pi]
            try:
                y = float(out["yaw"])
                while y > math.pi: y -= 2*math.pi
                while y < -math.pi: y += 2*math.pi
                out["yaw"] = y
            except Exception:
                out["yaw"] = 0.0

        # simple speed clamps
        for k in ("vfr_groundspeed", "vfr_airspeed"):
            if k in out:
                out[k] = float(self._clamp(out[k], 0, 40))

        return out

    # ---------------- ends


    def handle_inbound_msg(self, msg):
        # LLM-integration | Make sure inbound logging is enabled (so LLM has context)
        try:
            d = msg.to_dict()
        except Exception:
            d = {"mavpackettype": msg.get_type()}

        self.inbound_log.append({
            "t": time.time(),
            "type": d.get("mavpackettype", msg.get_type()),
            "fields": {k: v for k, v in d.items() if k != "mavpackettype"},
        })

        # ---- LLM-integration ends -------

        msg_type = msg.get_type()

        if msg_type == "COMMAND_LONG":
            cmd = msg.command

            # ARM / DISARM
            if cmd == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
                arm = int(msg.param1)

                with self.state_lock:
                    if arm == 1:
                        self.common_state.base_mode |= 0x80  # MAV_MODE_FLAG_SAFETY_ARMED
                    else:
                        self.common_state.base_mode &= ~0x80

                ack = self.mav_out.command_ack_encode(
                    cmd,
                    mavutil.mavlink.MAV_RESULT_ACCEPTED
                )
                self.send_mav(ack)

        elif msg_type == "SET_MESSAGE_INTERVAL":
            msg_id = msg.message_id
            interval_us = msg.interval_us

            if interval_us > 0:
                rate = 1e6 / interval_us
            else:
                rate = 0

            name = mavutil.mavlink.enums['MAVLINK_MSG_ID'][msg_id].name
            self.streams.set_rate(name, rate, time.monotonic())



if __name__ == "__main__":
    hp = LLMHoneypot()
    hp.run()
