# generator_transition_sequence_fixed.py
# pip install pymavlink

from pymavlink import mavutil
import json
from collections import defaultdict, Counter
from pathlib import Path

TLOG_PATH = Path("./set_of_tlogs/t1_gazeebo.tlog")
OUT_PX4_SEQUENCE = Path("./set_of_tlogs/px4_command_sequences_t1.jsonl")
OUT_CMD_TRANSITION = Path("./set_of_tlogs/cmd_transition_t1.jsonl")

# Remove noisy command IDs. 512 is REQUEST_MESSAGE in many PX4/QGC logs.
FILTER_COMMANDS = {512}

# Keep behavior/mission commands only. Set to None to keep all non-filtered commands.
KEEP_COMMANDS = {16, 20, 22, 178,400,401}

# Maximum followup messages per MAVLink message type inside each row.
MAX_PER_TYPE = 7

TELEMETRY_TYPES = [
    "SYS_STATUS",
    "GPS_RAW_INT",
    "GLOBAL_POSITION_INT",
    "ATTITUDE",
    "VFR_HUD",
    "POSITION_TARGET_LOCAL_NED",
    "POSITION_TARGET_GLOBAL_INT",
    "MISSION_CURRENT",
    "SERVO_OUTPUT_RAW",
]

COMMAND_TYPES = {
    "COMMAND_LONG",
    "COMMAND_INT",
    "MISSION_ITEM",
    "MISSION_ITEM_INT",
}

ACK_TYPES = {
    "COMMAND_ACK",
    "MISSION_ACK",
}


def clean_msg_dict(msg):
    d = msg.to_dict()
    msg_type = msg.get_type()
    d.pop("mavpackettype", None)

    for k, v in list(d.items()):
        if isinstance(v, float) and v != v:  # NaN
            d[k] = None

    if msg_type != "HEARTBEAT":
        d = {"type": msg_type, **d}

    return d


def heartbeat_is_vehicle_type_2(msg):
    if msg.get_type() != "HEARTBEAT":
        return True
    return msg.to_dict().get("type") == 2


def compact_heartbeat(msg):
    if msg is None:
        return None
    d = clean_msg_dict(msg)
    return {
        "type": d.get("type"),
        "autopilot": d.get("autopilot"),
        "base_mode": d.get("base_mode"),
        "custom_mode": d.get("custom_mode"),
        "system_status": d.get("system_status"),
        "mavlink_version": d.get("mavlink_version"),
    }


def px4_context_heartbeat(msg):
    if msg is None:
        return None
    d = clean_msg_dict(msg)
    return {
        "type": "HEARTBEAT",
        "base_mode": d.get("base_mode"),
        "custom_mode": d.get("custom_mode"),
        "system_status": d.get("system_status"),
    }


def command_name(command_id):
    try:
        enum = mavutil.mavlink.enums["MAV_CMD"]
        if command_id in enum:
            return enum[command_id].name
    except Exception:
        pass
    return f"MAV_CMD_{command_id}"


def get_command_id(msg):
    d = msg.to_dict()
    msg_type = msg.get_type()
    if msg_type in {"COMMAND_LONG", "COMMAND_INT", "MISSION_ITEM", "MISSION_ITEM_INT"}:
        return d.get("command")
    if msg_type == "COMMAND_ACK":
        return d.get("command")
    return None


# def command_payload(msg):
#     # Keep the MAVLink message type. This is important for distinguishing
#     # COMMAND_LONG vs MISSION_ITEM_INT.
#     return clean_msg_dict(msg)

def command_payload(msg):
    # Keep MAVLink message type, but normalize coordinate fields.
    # MISSION_ITEM_INT / COMMAND_INT use x,y,z.
    # For our dataset, store them as param5,param6,param7.

    d = clean_msg_dict(msg)
    msg_type = msg.get_type()

    if msg_type in {"MISSION_ITEM_INT", "COMMAND_INT"}:
        d["param5"] = d.get("x")
        d["param6"] = d.get("y")
        d["param7"] = d.get("z")

        # remove raw x/y/z so the output has one consistent format
        d.pop("x", None)
        d.pop("y", None)
        d.pop("z", None)

    return d


def ack_payload(msg):
    if msg is None:
        return None
    return clean_msg_dict(msg)


def empty_telemetry_snapshot():
    return {k: None for k in TELEMETRY_TYPES}


def update_latest_telemetry(latest_telemetry, msg):
    msg_type = msg.get_type()
    if msg_type in TELEMETRY_TYPES:
        d = clean_msg_dict(msg)
        d.pop("type", None)
        latest_telemetry[msg_type] = d


def snapshot_telemetry(latest_telemetry):
    return {k: latest_telemetry.get(k) for k in TELEMETRY_TYPES}


def add_followup_with_cap(active, msg):
    if active is None:
        return

    msg_type = msg.get_type()
    if msg_type not in TELEMETRY_TYPES and msg_type != "HEARTBEAT":
        return

    if active["type_counter"][msg_type] >= MAX_PER_TYPE:
        return

    if msg_type == "HEARTBEAT":
        d = px4_context_heartbeat(msg)
    else:
        d = clean_msg_dict(msg)

    active["followups"].append(d)
    active["type_counter"][msg_type] += 1


# for additional items in future telemetry.
def find_future_followups(messages, start_index, max_seconds=5.0):
    followups = []
    type_counter = defaultdict(int)
    next_telemetry = empty_telemetry_snapshot()
    hb_next = None

    start_time = messages[start_index][0]

    for t, msg in messages[start_index + 1:]:
        if t - start_time > max_seconds:
            break

        msg_type = msg.get_type()

        # Keep heartbeat
        if msg_type == "HEARTBEAT":
            if hb_next is None:
                hb_next = msg

            if type_counter[msg_type] < MAX_PER_TYPE:
                followups.append(px4_context_heartbeat(msg))
                type_counter[msg_type] += 1

        # Keep telemetry messages
        elif msg_type in TELEMETRY_TYPES:
            d = clean_msg_dict(msg)

            # update final NEXT_Telemetry snapshot
            d_no_type = dict(d)
            d_no_type.pop("type", None)
            next_telemetry[msg_type] = d_no_type

            # keep repeated sequence samples
            if type_counter[msg_type] < MAX_PER_TYPE:
                followups.append(d)
                type_counter[msg_type] += 1

    return hb_next, next_telemetry, followups


def read_filtered_messages(tlog_path):
    if not tlog_path.exists():
        raise FileNotFoundError(f"TLOG file not found: {tlog_path.resolve()}")

    mlog = mavutil.mavlink_connection(str(tlog_path))
    messages = []

    while True:
        msg = mlog.recv_match(blocking=False)
        if msg is None:
            break

        if msg.get_type() == "BAD_DATA":
            continue

        # First delete all HEARTBEAT messages that are not vehicle type 2.
        if not heartbeat_is_vehicle_type_2(msg):
            continue

        t = getattr(msg, "_timestamp", None)
        if t is None:
            continue

        messages.append((float(t), msg))

    messages.sort(key=lambda x: x[0])
    return messages


def make_active(t, msg, latest_hb, latest_telemetry):
    return {
        "t_cmd": t,
        "cmd_msg": msg,
        "prev_hb": latest_hb,
        "prev_telemetry": snapshot_telemetry(latest_telemetry),
        "t_ack": None,
        "ack_msg": None,
        "hb_next": None,
        "next_telemetry": None,
        "followups": [],
        "type_counter": defaultdict(int),
    }


def active_is_meaningful(active):
    if active is None:
        return False
    return (
        active.get("ack_msg") is not None
        or active.get("next_telemetry") is not None
        or len(active.get("followups", [])) > 0
    )


def build_px4_row(active):
    return {
        "t_request": active["t_cmd"],
        "t_ack": active["t_ack"],
        "dt_ack_s": (
            round(active["t_ack"] - active["t_cmd"], 6)
            if active["t_ack"] is not None
            else None
        ),
        "context_prev_heartbeat": px4_context_heartbeat(active["prev_hb"]),
        "request": command_payload(active["cmd_msg"]),
        "ack": ack_payload(active["ack_msg"]),
        "followups": active["followups"],
    }


# def build_transition_row(active):
#     cmd = command_payload(active["cmd_msg"])
#     cmd_id = cmd.get("command")

#     return {
#         "t_cmd": active["t_cmd"],
#         "Prev_HB": compact_heartbeat(active["prev_hb"]),
#         "Prev_Telemetry": active["prev_telemetry"],
#         "Command": cmd,
#         "Command_name": command_name(cmd_id),
#         "t_ack": active["t_ack"],
#         "Command_ACK": ack_payload(active["ack_msg"]),
#         "HB_NEXT": compact_heartbeat(active["hb_next"]),
#         "NEXT_Telemetry": (
#             active["next_telemetry"]
#             if active["next_telemetry"] is not None
#             else empty_telemetry_snapshot()
#         ),
#     }

def build_transition_row(active):
    cmd = command_payload(active["cmd_msg"])
    cmd_id = cmd.get("command")

    # Transition format should not include raw MAVLink "type"
    cmd_for_transition = dict(cmd)
    cmd_for_transition.pop("type", None)

    ack = ack_payload(active["ack_msg"])
    if ack is not None:
        ack_for_transition = dict(ack)
        ack_for_transition.pop("type", None)
    else:
        ack_for_transition = None

    return {
        "t_cmd": active["t_cmd"],
        "Prev_HB": compact_heartbeat(active["prev_hb"]),
        "Prev_Telemetry": active["prev_telemetry"],
        "Command": cmd_for_transition,
        "Command_name": command_name(cmd_id),
        "t_ack": active["t_ack"],
        "Command_ACK": ack_for_transition,
        "HB_NEXT": compact_heartbeat(active["hb_next"]),
        "NEXT_Telemetry": (
            active["next_telemetry"]
            if active["next_telemetry"] is not None
            else empty_telemetry_snapshot()
        ),
    }

def convert_tlog():
    messages = read_filtered_messages(TLOG_PATH)

    latest_hb = None
    latest_telemetry = empty_telemetry_snapshot()
    px4_rows = []
    transition_rows = []
    active = None

    seen_commands = Counter()
    kept_commands = Counter()
    filtered_commands = Counter()

    def close_active_if_meaningful():
        nonlocal active
        if active_is_meaningful(active):
            px4_rows.append(build_px4_row(active))
            transition_rows.append(build_transition_row(active))
        active = None

    # for t, msg in messages: # for getting accurate seq
    for i, (t, msg) in enumerate(messages):
        msg_type = msg.get_type()

        # HEARTBEAT state
        if msg_type == "HEARTBEAT":
            latest_hb = msg
            if active is not None:
                if active.get("hb_next") is None:
                    active["hb_next"] = msg
                add_followup_with_cap(active, msg)
            continue

        # Telemetry state. Keep updating NEXT_Telemetry until next kept command.
        update_latest_telemetry(latest_telemetry, msg)
        if active is not None and msg_type in TELEMETRY_TYPES:
            active["next_telemetry"] = snapshot_telemetry(latest_telemetry)
            add_followup_with_cap(active, msg)

        # Command event start
        if msg_type in COMMAND_TYPES:
            cmd_id = get_command_id(msg)
            seen_commands[cmd_id] += 1

            if cmd_id in FILTER_COMMANDS:
                filtered_commands[cmd_id] += 1
                # Do NOT destroy active. 512 is noise and should not reset the current event.
                continue

            if KEEP_COMMANDS is not None and cmd_id not in KEEP_COMMANDS:
                filtered_commands[cmd_id] += 1
                # Ignore noisy commands, but do not reset active.
                continue


            # kept_commands[cmd_id] += 1
            # close_active_if_meaningful()
            # active = make_active(t, msg, latest_hb, latest_telemetry)
            # continue

            # replacing for better sequences.
            kept_commands[cmd_id] += 1
            close_active_if_meaningful()

            active = make_active(t, msg, latest_hb, latest_telemetry)

            hb_next, future_telem, future_followups = find_future_followups(
                messages,
                i,
                max_seconds=5.0
            )

            active["hb_next"] = hb_next
            active["next_telemetry"] = future_telem
            active["followups"] = future_followups

            continue

        # # ACK/response handling
        # if msg_type in ACK_TYPES and active is not None and active.get("ack_msg") is None:
        #     if msg_type == "COMMAND_ACK":
        #         active_cmd_id = get_command_id(active["cmd_msg"])
        #         ack_cmd_id = get_command_id(msg)
        #         if active_cmd_id == ack_cmd_id:
        #             active["t_ack"] = t
        #             active["ack_msg"] = msg
        #     elif msg_type == "MISSION_ACK":
        #         # MISSION_ACK confirms mission upload, not necessarily one item,
        #         # but attach it as the available response.
        #         active["t_ack"] = t
        #         active["ack_msg"] = msg
        #     continue

        # mission ack and command ack are not same;
        # ACK/response handling
        if msg_type in ACK_TYPES and active is not None and active.get("ack_msg") is None:
            if msg_type == "COMMAND_ACK":
                active_cmd_id = get_command_id(active["cmd_msg"])
                ack_cmd_id = get_command_id(msg)
                if active_cmd_id == ack_cmd_id:
                    active["t_ack"] = t
                    active["ack_msg"] = msg
            elif msg_type == "MISSION_ACK":
                # MISSION_ACK confirms mission upload, not necessarily one item,
                # but attach it as the available response.
                active["t_ack"] = t
                active["ack_msg"] = msg
            continue

    close_active_if_meaningful()

    with OUT_PX4_SEQUENCE.open("w", encoding="utf-8") as f:
        for row in px4_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with OUT_CMD_TRANSITION.open("w", encoding="utf-8") as f:
        for row in transition_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Total filtered messages read: {len(messages)}")
    print(f"Seen commands: {dict(seen_commands)}")
    print(f"Kept commands: {dict(kept_commands)}")
    print(f"Ignored commands: {dict(filtered_commands)}")
    print(f"Saved: {OUT_PX4_SEQUENCE} ({len(px4_rows)} rows)")
    print(f"Saved: {OUT_CMD_TRANSITION} ({len(transition_rows)} rows)")


def find_future_followups(messages, start_index, max_seconds=5.0):
    followups = []
    type_counter = defaultdict(int)
    next_telemetry = empty_telemetry_snapshot()
    hb_next = None

    start_time = messages[start_index][0]

    for t, msg in messages[start_index + 1:]:
        if t - start_time > max_seconds:
            break

        msg_type = msg.get_type()

        # Only collect telemetry/heartbeat as future behavior
        if msg_type == "HEARTBEAT":
            if hb_next is None:
                hb_next = msg

            if type_counter[msg_type] < MAX_PER_TYPE:
                followups.append(px4_context_heartbeat(msg))
                type_counter[msg_type] += 1

        elif msg_type in TELEMETRY_TYPES:
            d = clean_msg_dict(msg)

            # update final snapshot
            d_no_type = dict(d)
            d_no_type.pop("type", None)
            next_telemetry[msg_type] = d_no_type

            # keep repeated sequence
            if type_counter[msg_type] < MAX_PER_TYPE:
                followups.append(d)
                type_counter[msg_type] += 1

    return hb_next, next_telemetry, followups

if __name__ == "__main__":
    convert_tlog()