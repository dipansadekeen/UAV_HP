# mission.py
import time
from pymavlink import mavutil
import math
import time, math, os, csv
from datetime import datetime
MISSION_CMD_WAYPOINT = 16
MISSION_CMD_TAKEOFF = 22
MISSION_CMD_LAND = 21
MISSION_CMD_RTL = 20
MISSION_CMD_CHANGE_SPEED = 178

# ///////////// log the attacker mission
def next_mission_id(path="logs/mission_uploads.csv"):
    try:
        with open(path, newline="") as f:
            return max((int(r["mission_id"]) for r in csv.DictReader(f)), default=0) + 1
    except:
        return 1
# ///////////// log the attacker mission




def _current_speed_mps(hp):
    with hp.state_lock:
        return float(getattr(hp.state, "vfr_groundspeed", 0.0))

def send_mission_request_int(hp, seq):
    msg = hp.mav_out.mission_request_int_encode(
        hp.mav_out.srcSystem,
        hp.mav_out.srcComponent,
        int(seq)
    )
    print(f"[MISSION REQ TX] seq={seq}", flush=True)
    hp.send_mav(msg)

def init_mission_state(hp):
    hp.mission_state = {
        "expected_count": 0,
        "items": {},
        "ready": False,
        "active": False,
        "complete": False,
        "current_seq": 0,
        "stable_hits": 0,
        "last_tick": 0.0,
        "reach_threshold_deg": 0.00002,   # rough small threshold
        "alt_threshold_mm": 1500,
        "reprompt_skip_counter": 0, # new
        "reprompt_skip_limit": 3, # new


        "home": None,       # add RTL.
        "rtl_alt_mm": None, # add RTL.

        "speed_threshold_mps": 0.3,
    }

def handle_mission_count(hp, msg):
    hp.mission_id = next_mission_id() #log
    hp.mission_run_id = time.strftime("mission_%Y%m%d_%H%M%S") #log
    hp.mission_state["expected_count"] = int(getattr(msg, "count", 0))
    hp.mission_state["items"] = {}
    hp.mission_state["ready"] = False
    hp.mission_state["active"] = False
    hp.mission_state["complete"] = False
    hp.mission_state["current_seq"] = 0
    hp.mission_state["stable_hits"] = 0
    print(f"[MISSION] count={hp.mission_state['expected_count']}", flush=True)

    if hp.mission_state["expected_count"] > 0:
        send_mission_request_int(hp, 0)

def handle_mission_item(hp, msg):
    seq = int(getattr(msg, "seq", -1))
    hp.mission_state["items"][seq] = {
        "seq": seq,
        "command": int(getattr(msg, "command", -1)),
        "frame": int(getattr(msg, "frame", 0)),
        "x": int(getattr(msg, "x", 0)),
        "y": int(getattr(msg, "y", 0)),
        "z": float(getattr(msg, "z", 0.0)),
        "param1": float(getattr(msg, "param1", 0.0)),
        "param2": float(getattr(msg, "param2", 0.0)),
        "param3": float(getattr(msg, "param3", 0.0)),
        "param4": float(getattr(msg, "param4", 0.0)),
    }
    print(f"[MISSION] stored seq={seq}", flush=True)

    next_seq = seq + 1
    if next_seq < hp.mission_state["expected_count"]:
        send_mission_request_int(hp, next_seq)


def mission_upload_complete(hp):
    exp = hp.mission_state["expected_count"]
    return exp > 0 and len(hp.mission_state["items"]) >= exp


def send_mission_ack(hp, ack_type=0):
    msg = hp.mav_out.mission_ack_encode(
        hp.mav_out.srcSystem,
        hp.mav_out.srcComponent,
        ack_type
    )
    print(f"[MISSION ACK TX] type={ack_type}", flush=True)
    hp.send_mav(msg)


# def finalize_mission_upload(hp):
#     hp.mission_state["ready"] = True
#     # current mission # new
#     with hp.state_lock:
#         hp.state.mission_seq = 0

#     send_mission_ack(hp, 0)
#     print("[MISSION] upload accepted", flush=True)

def finalize_mission_upload(hp): #with log # new
    if hp.mission_state["ready"]:
        return

    hp.mission_state["ready"] = True

    with hp.state_lock:
        hp.state.mission_seq = 0

    path = "logs/mission_uploads.csv"
    os.makedirs("logs", exist_ok=True)
    new = not os.path.exists(path)

    cols = [
        "mission_id", "mission_run_id", "time",
        "seq", "command", "frame", "x", "y", "z",
        "param1", "param2", "param3", "param4"
    ]

    uploaded = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()

        for seq in sorted(hp.mission_state["items"]):
            w.writerow({
                "mission_id": hp.mission_id,
                "mission_run_id": hp.mission_run_id,
                "time": uploaded,
                **hp.mission_state["items"][seq]
            })

    send_mission_ack(hp, 0)
    print(
        f"[MISSION] logged id={hp.mission_id} "
        f"run={hp.mission_run_id}",
        flush=True
    )


# def start_mission(hp):
#     if not hp.mission_state["ready"]:
#         print("[MISSION] start requested but not ready", flush=True)
#         return
#     hp.mission_state["active"] = True
#     hp.mission_state["complete"] = False
#     hp.mission_state["current_seq"] = 0
#     hp.mission_state["stable_hits"] = 0
#     print("[MISSION] started", flush=True)
def start_mission(hp): # full new RTL version
    if not hp.mission_state["ready"]:
        print("[MISSION] start requested but not ready", flush=True)
        return

    with hp.state_lock:
        hp.state.base_mode = 157
        hp.state.custom_mode = 67371008
        hp.state.system_status = 4
        # current mission # new
        hp.state.mission_seq = 0

    hp.mission_state["active"] = True
    hp.mission_state["complete"] = False
    hp.mission_state["current_seq"] = 0
    hp.mission_state["stable_hits"] = 0

    hp.mission_state["home"] = {
        "lat": int(getattr(hp.state, "gpi_lat", 0)),
        "lon": int(getattr(hp.state, "gpi_lon", 0)),
    }
    hp.mission_state["rtl_alt_mm"] = None
    print("[MISSION] started", flush=True)

def get_current_mission_item(hp):
    seq = hp.mission_state["current_seq"]
    return hp.mission_state["items"].get(seq)


# def _target_from_item(item):
#     return {
#         "lat": int(item.get("x", 0)),
#         "lon": int(item.get("y", 0)),
#         "alt_mm": int(float(item.get("z", 0.0)) * 1000.0),
#     }

# ///////////////RTL
def _target_from_item(hp, item):
    cmd = int(item.get("command", -1))

    # old
    # if cmd == MISSION_CMD_RTL and hp.mission_state.get("start_location"):
    #     return hp.mission_state["start_location"]
    #new
    if cmd == MISSION_CMD_RTL and hp.mission_state.get("home"):
        home = hp.mission_state["home"]
        return {
            "lat": int(home["lat"]),
            "lon": int(home["lon"]),
            "alt_mm": int(hp.mission_state.get("rtl_alt_mm") or 0),
        }

    return {
        "lat": int(item.get("x", 0)),
        "lon": int(item.get("y", 0)),
        # "alt_mm": int(float(item.get("z", 0.0)) * 1000.0),
        "alt_mm": int((0.0 if math.isnan(float(item.get("z", 0.0))) else float(item.get("z", 0.0))) * 1000.0),
    }
# ///////////////RTL


# def execute_current_item_with_llm(hp, item):
#     cmd = int(item["command"])

#     params = {
#         "param1": float(item.get("param1", 0.0)),
#         "param2": float(item.get("param2", 0.0)),
#         "param3": float(item.get("param3", 0.0)),
#         "param4": float(item.get("param4", 0.0)),
#         "param5": float(item.get("x", 0)),   # lat_int
#         "param6": float(item.get("y", 0)),   # lon_int
#         "param7": float(item.get("z", 0.0)), # alt
#     }

#     hp.active_cmd = cmd
#     hp.active_params = params

#     # hp.handle_command_heartbeat(cmd, params)
#     hp.handle_command_telemetry(cmd, params)

# # new | fix continuous ticking
def execute_current_item_with_llm(hp, item):
    # if previous generated telemetry is still running, do nothing
    with hp.override_lock:
        if len(hp.override_series) > 0:
            return

    # first eligible call -> prompt immediately
    # after that, skip 3 eligible calls, then prompt again
    skip_counter = hp.mission_state.get("reprompt_skip_counter", 0)
    skip_limit = hp.mission_state.get("reprompt_skip_limit", 3)

    if skip_counter < skip_limit and skip_counter != 0:
        hp.mission_state["reprompt_skip_counter"] += 1
        print(
            f"[MISSION] skip reprompt {hp.mission_state['reprompt_skip_counter']}/{skip_limit}",
            flush=True
        )
        return


    cmd = int(item["command"])

    # ////// commented for RTL ///////
    # params = {
    #     "param1": float(item.get("param1", 0.0)),
    #     "param2": float(item.get("param2", 0.0)),
    #     "param3": float(item.get("param3", 0.0)),
    #     "param4": float(item.get("param4", 0.0)),
    #     "param5": float(item.get("x", 0)),
    #     "param6": float(item.get("y", 0)),
    #     "param7": float(item.get("z", 0.0)),
    # }
    # ////// commented for RTL ///////

    # ////// RTL ///////
    x = float(item.get("x", 0))
    y = float(item.get("y", 0))
    z = float(item.get("z", 0.0))

    if cmd == MISSION_CMD_RTL and hp.mission_state.get("home"):
        if hp.mission_state["rtl_alt_mm"] is None:
            with hp.state_lock:
                hp.mission_state["rtl_alt_mm"] = int(getattr(hp.state, "gpi_relative_alt", 0))

        home = hp.mission_state["home"]
        x = float(home["lat"])
        y = float(home["lon"])
        z = hp.mission_state["rtl_alt_mm"] / 1000.0

    params = {
        "param1": float(item.get("param1", 0.0)),
        "param2": float(item.get("param2", 0.0)),
        "param3": float(item.get("param3", 0.0)),
        "param4": float(item.get("param4", 0.0)),
        "param5": x,
        "param6": y,
        "param7": z,
    }
    # ////// RTL ///////

    hp.active_cmd = cmd
    hp.active_params = params
    # hp.handle_command_heartbeat(cmd, params)

    # keep QGC flying/landing state consistent during uploaded missions # new jul
    hp.apply_qgc_flight_state_from_cmd(cmd, params)

    hp.handle_command_telemetry(cmd, params)

    # after prompting once, begin skip cycle
    hp.mission_state["reprompt_skip_counter"] = 1
    print("[MISSION] LLM prompt sent", flush=True)


def is_step_complete(hp, item):
    # target = _target_from_item(item)
    target = _target_from_item(hp, item) # for RTL

    with hp.state_lock:
        curr_lat = int(getattr(hp.state, "gpi_lat", 0))
        curr_lon = int(getattr(hp.state, "gpi_lon", 0))
        curr_alt = int(getattr(hp.state, "gpi_relative_alt", 0))

    lat_ok = abs(curr_lat - target["lat"]) <= int(hp.mission_state["reach_threshold_deg"] * 1e7)
    lon_ok = abs(curr_lon - target["lon"]) <= int(hp.mission_state["reach_threshold_deg"] * 1e7)
    alt_ok = abs(curr_alt - target["alt_mm"]) <= hp.mission_state["alt_threshold_mm"]

    cmd = int(item["command"])

    if cmd == MISSION_CMD_WAYPOINT:
        ok = lat_ok and lon_ok and alt_ok
    elif cmd == MISSION_CMD_TAKEOFF:
        ok = alt_ok
    elif cmd == MISSION_CMD_LAND:
        ok = curr_alt <= 500

    # ////// for RTL ///////
    elif cmd == MISSION_CMD_RTL: # RTL
        ok = lat_ok and lon_ok
    # ////// for RTL ///////

    # ////// for new speed ///////

    elif cmd == MISSION_CMD_CHANGE_SPEED:
        target_speed = float(item.get("param2", 0.0))  # speed in m/s
        curr_speed = _current_speed_mps(hp)
        ok = abs(curr_speed - target_speed) <= hp.mission_state.get("speed_threshold_mps", 0.3)
    # ////// for new speed ///////

    else:
        ok = lat_ok and lon_ok and alt_ok

    if ok:
        hp.mission_state["stable_hits"] += 1
    else:
        hp.mission_state["stable_hits"] = 0

    return hp.mission_state["stable_hits"] >= 3


def advance_mission_step(hp):
    hp.mission_state["current_seq"] += 1
    hp.mission_state["stable_hits"] = 0
    hp.mission_state["reprompt_skip_counter"] = 0
    print(f"[MISSION] advance -> seq={hp.mission_state['current_seq']}", flush=True)
    # current mission # new
    with hp.state_lock:
        hp.state.mission_seq = hp.mission_state["current_seq"]
    print(f"[MISSION] advance -> seq={hp.mission_state['current_seq']}", flush=True)

# def finish_mission(hp):
#     hp.mission_state["active"] = False
#     hp.mission_state["complete"] = True

#     # disarm
#     with hp.state_lock:
#         hp.state.base_mode = int(hp.state.base_mode) & (~0x80)
#         hp.state.system_status = 3

#     print("[MISSION] complete", flush=True)

def finish_mission(hp):
    hp.mission_state["active"] = False
    hp.mission_state["complete"] = True

    with hp.state_lock:
        hp.state.base_mode = 81
        hp.state.custom_mode = 50593792
        hp.state.system_status = 3

    print("[MISSION] complete", flush=True)


def start_or_tick_mission(hp):
    if not hp.mission_state["active"]:
        return

    now = time.monotonic()
    if now - hp.mission_state["last_tick"] < 0.5:
        return
    hp.mission_state["last_tick"] = now

    item = get_current_mission_item(hp)
    if item is None:
        finish_mission(hp)
        return

    execute_current_item_with_llm(hp, item)

    if is_step_complete(hp, item):
        advance_mission_step(hp)

    if hp.mission_state["current_seq"] >= hp.mission_state["expected_count"]:
        finish_mission(hp)

# new 2
def maybe_start_uploaded_mission(hp):
    if not hp.mission_state["ready"] or hp.mission_state["active"]:
        return

    with hp.state_lock:
        cm = int(getattr(hp.state, "custom_mode", 0))

    # replace this with your actual mission-mode custom_mode
    if cm == 67371008:
        start_mission(hp)