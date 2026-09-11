#!/usr/bin/env python3
"""
tlog2csv.py -- convert a MAVLink .tlog into the mission/LLM-step CSV schema.

No command-line arguments. Drop this next to your tlog and run:

    python3 tlog2csv.py

Everything is configured in the SETTINGS block below.

Each output row = one fixed-dt timestep of a mission run, carrying:
  * the mission item active at that instant (MISSION_CURRENT indexed into
    the uploaded MISSION_ITEM_INT table)
  * the vehicle state at that instant           -> input_*     columns
  * the vehicle state one step later            -> generated_* columns
    (ground truth, NOT model output -- see GENERATED)

A tlog holds no LLM data, so llm_call_id / llm_latency_ms / llm_model_name
stay blank unless you set MODEL_NAME.
"""

import csv
import glob
import math
import os
import sys
from datetime import datetime, timezone

from pymavlink import mavutil

# ==========================================================================
# SETTINGS -- edit these, there are no CLI arguments
# ==========================================================================

INPUT_TLOG = "x2.tlog"      # input file; if missing, the first *.tlog nearby is used
OUTPUT_CSV = "x2.csv"       # output file

DT = 0.5                    # resample step in seconds

GENERATED = "next"          # "next"  -> generated_* = the observed next state
                            #            (ground truth pairs for training/eval)
                            # "blank" -> leave generated_* empty

RUNS = "all"                # "all"   -> every uploaded mission in the log
                            # "flown" -> only runs that armed AND reached a
                            #            mission item (drops aborted attempts)

TRIM = False                # True clips each run to its armed window
PAD = 10.0                  # seconds kept either side when TRIM is True

TARGET_LATLON = "deg"       # "deg" -> waypoint lat/lon as decimal degrees
                            # "int" -> raw int * 1e7, matching input_gpi_lat

MISSION_NAME = "qgc_uploaded_mission"
MODEL_NAME = ""             # stamped into llm_model_name on every row
SYSID = 1                   # vehicle system id (GCS is usually 255)

# ==========================================================================
# schema
# ==========================================================================

COLUMNS = [
    "mission_run_id", "mission_name", "timestamp", "mission_seq", "mission_status",
    "command_id", "command_name", "frame", "current", "autocontinue",
    "param1", "param2", "param3", "param4", "param5", "param6", "param7",
    "target_lat", "target_lon", "target_alt",
    "llm_call_id", "llm_step_index", "dt", "llm_latency_ms", "llm_model_name",
    "input_hb_base_mode", "input_hb_custom_mode", "input_hb_system_status",
    "input_gpi_lat", "input_gpi_lon", "input_gpi_alt", "input_gpi_relative_alt",
    "input_gpi_vx", "input_gpi_vy", "input_gpi_vz", "input_gpi_hdg",
    "input_roll", "input_pitch", "input_yaw",
    "input_vfr_groundspeed", "input_vfr_heading", "input_vfr_throttle",
    "input_vfr_alt", "input_vfr_climb",
    "input_battery_remaining", "input_voltage_battery", "input_load",
    "input_gps_fix_type",
    "generated_gpi_lat", "generated_gpi_lon", "generated_gpi_alt",
    "generated_gpi_relative_alt", "generated_gpi_vx", "generated_gpi_vy",
    "generated_gpi_vz", "generated_gpi_hdg",
    "generated_att_roll", "generated_att_pitch", "generated_att_yaw",
    "generated_vfr_groundspeed", "generated_vfr_heading", "generated_vfr_throttle",
    "generated_vfr_alt", "generated_vfr_climb",
    "generated_sys_battery_remaining", "generated_sys_voltage_battery",
    "generated_sys_load", "generated_gps_fix_type",
]

# input_* name -> generated_* name. The schema's two halves are named
# inconsistently (input_roll vs generated_att_roll), so map explicitly.
STATE_PAIRS = [
    ("input_gpi_lat", "generated_gpi_lat"),
    ("input_gpi_lon", "generated_gpi_lon"),
    ("input_gpi_alt", "generated_gpi_alt"),
    ("input_gpi_relative_alt", "generated_gpi_relative_alt"),
    ("input_gpi_vx", "generated_gpi_vx"),
    ("input_gpi_vy", "generated_gpi_vy"),
    ("input_gpi_vz", "generated_gpi_vz"),
    ("input_gpi_hdg", "generated_gpi_hdg"),
    ("input_roll", "generated_att_roll"),
    ("input_pitch", "generated_att_pitch"),
    ("input_yaw", "generated_att_yaw"),
    ("input_vfr_groundspeed", "generated_vfr_groundspeed"),
    ("input_vfr_heading", "generated_vfr_heading"),
    ("input_vfr_throttle", "generated_vfr_throttle"),
    ("input_vfr_alt", "generated_vfr_alt"),
    ("input_vfr_climb", "generated_vfr_climb"),
    ("input_battery_remaining", "generated_sys_battery_remaining"),
    ("input_voltage_battery", "generated_sys_voltage_battery"),
    ("input_load", "generated_sys_load"),
    ("input_gps_fix_type", "generated_gps_fix_type"),
]

MAV_MODE_FLAG_SAFETY_ARMED = 128

# messages we hold the latest copy of, keyed by type
STATE_TYPES = ("HEARTBEAT", "GLOBAL_POSITION_INT", "ATTITUDE", "VFR_HUD",
               "SYS_STATUS", "GPS_RAW_INT", "MISSION_CURRENT")


# ==========================================================================
# helpers
# ==========================================================================

def clean(v):
    """MAVLink NaN means 'unspecified' -- emit empty, not the string 'nan'."""
    if v is None:
        return ""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return ""
    return v


def cmd_name(cmd_id):
    try:
        return mavutil.mavlink.enums["MAV_CMD"][cmd_id].name
    except (KeyError, AttributeError):
        return f"MAV_CMD_UNKNOWN_{cmd_id}"


def px4_is_auto(custom_mode):
    """PX4 packs custom_mode as (sub_mode << 24) | (main_mode << 16).
    main_mode 4 = AUTO. Any AUTO sub-mode counts as executing the plan:
    MISSION (4) flies the waypoints, RTL (5) flies a NAV_RETURN_TO_LAUNCH
    item, LOITER (3) holds at one. Narrowing this to MISSION alone would
    label the whole RTL leg as merely 'armed'."""
    return ((custom_mode >> 16) & 0xFF) == 4


def run_id_from_ts(ts):
    return "mission_" + datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%d_%H%M%S")


def resolve_input():
    """Use INPUT_TLOG if it exists, else fall back to any tlog sitting next
    to the script -- so the file does not have to be renamed to run."""
    if os.path.isfile(INPUT_TLOG):
        return INPUT_TLOG
    here = os.path.dirname(os.path.abspath(__file__))
    found = sorted(glob.glob(os.path.join(here, "*.tlog")))
    if found:
        print(f"note: {INPUT_TLOG!r} not found, using {os.path.basename(found[0])!r}")
        return found[0]
    sys.exit(f"No tlog found. Put one next to this script or set INPUT_TLOG.")


# ==========================================================================
# pass 1 -- read the tlog into mission runs + a state timeline
# ==========================================================================

class Run:
    """One uploaded mission and the window of time it was the active plan."""

    def __init__(self, t_start, items):
        self.t_start = t_start
        self.t_end = None
        self.items = items          # seq -> MISSION_ITEM_INT dict
        self.armed_windows = []     # [(t_arm, t_disarm)]
        self.max_seq_reached = -1
        self.reached_events = []    # [(ts, seq)] -- progress over time

    @property
    def flown(self):
        """Armed alone is not enough -- a run can sit armed for an hour and
        never progress (failed preflight, no GCS link). Require that the
        vehicle actually reached at least one mission item."""
        return bool(self.armed_windows) and self.max_seq_reached >= 0

    def duration(self):
        return (self.t_end or self.t_start) - self.t_start


def parse_tlog(path, sysid=SYSID):
    """Returns (runs, timeline, bad_frame_count). timeline is a list of
    (timestamp, msg_type, msg_dict) for vehicle state messages, in order."""
    conn = mavutil.mavlink_connection(path)

    runs = []
    timeline = []
    pending = {}        # seq -> item, accumulating an in-progress upload
    pending_t = None
    armed = False
    arm_t = None
    bad = 0

    while True:
        msg = conn.recv_match()
        if msg is None:
            break
        mtype = msg.get_type()
        if mtype == "BAD_DATA":
            bad += 1
            continue
        ts = getattr(msg, "_timestamp", None)
        if ts is None:
            continue

        # ---- mission table upload (comes from the GCS, not the vehicle) ----
        # Handled before the sysid filter below: these arrive from the GCS,
        # so filtering first would hide every mission in the log.
        if mtype == "MISSION_ITEM_INT":
            d = msg.to_dict()
            seq = d["seq"]
            if seq == 0 and pending:
                pending = {}            # a restarted upload supersedes
            if seq == 0:
                pending_t = ts
            pending[seq] = d
            continue

        if mtype == "MISSION_ACK" and pending:
            # upload finished -> this becomes the active plan
            if runs and runs[-1].t_end is None:
                runs[-1].t_end = pending_t
            runs.append(Run(pending_t, pending))
            pending, pending_t = {}, None
            continue

        if mtype == "MISSION_CLEAR_ALL":
            if runs and runs[-1].t_end is None:
                runs[-1].t_end = ts
            continue

        # ---- vehicle state ----
        if msg.get_srcSystem() != sysid:
            continue

        if mtype == "HEARTBEAT":
            now_armed = bool(msg.base_mode & MAV_MODE_FLAG_SAFETY_ARMED)
            if now_armed and not armed:
                arm_t = ts
            elif armed and not now_armed and arm_t is not None:
                if runs:
                    runs[-1].armed_windows.append((arm_t, ts))
                arm_t = None
            armed = now_armed

        if mtype == "MISSION_ITEM_REACHED" and runs:
            runs[-1].max_seq_reached = max(runs[-1].max_seq_reached, msg.seq)
            runs[-1].reached_events.append((ts, msg.seq))
            continue

        if mtype in STATE_TYPES:
            timeline.append((ts, mtype, msg.to_dict()))

    # close out
    if armed and arm_t is not None and runs:
        runs[-1].armed_windows.append((arm_t, timeline[-1][0] if timeline else arm_t))
    if runs and runs[-1].t_end is None:
        runs[-1].t_end = timeline[-1][0] if timeline else runs[-1].t_start

    return runs, timeline, bad


# ==========================================================================
# pass 2 -- resample onto a fixed dt grid and emit rows
# ==========================================================================

def state_snapshot(latest):
    """Flatten the held messages into the input_* half of a row."""
    hb = latest.get("HEARTBEAT", {})
    gpi = latest.get("GLOBAL_POSITION_INT", {})
    att = latest.get("ATTITUDE", {})
    vfr = latest.get("VFR_HUD", {})
    sysst = latest.get("SYS_STATUS", {})
    gps = latest.get("GPS_RAW_INT", {})
    return {
        "input_hb_base_mode": hb.get("base_mode"),
        "input_hb_custom_mode": hb.get("custom_mode"),
        "input_hb_system_status": hb.get("system_status"),
        "input_gpi_lat": gpi.get("lat"),
        "input_gpi_lon": gpi.get("lon"),
        "input_gpi_alt": gpi.get("alt"),
        "input_gpi_relative_alt": gpi.get("relative_alt"),
        "input_gpi_vx": gpi.get("vx"),
        "input_gpi_vy": gpi.get("vy"),
        "input_gpi_vz": gpi.get("vz"),
        "input_gpi_hdg": gpi.get("hdg"),
        "input_roll": att.get("roll"),
        "input_pitch": att.get("pitch"),
        "input_yaw": att.get("yaw"),
        "input_vfr_groundspeed": vfr.get("groundspeed"),
        "input_vfr_heading": vfr.get("heading"),
        "input_vfr_throttle": vfr.get("throttle"),
        "input_vfr_alt": vfr.get("alt"),
        "input_vfr_climb": vfr.get("climb"),
        "input_battery_remaining": sysst.get("battery_remaining"),
        "input_voltage_battery": sysst.get("voltage_battery"),
        "input_load": sysst.get("load"),
        "input_gps_fix_type": gps.get("fix_type"),
    }


def mission_status(latest, run, reached_so_far):
    """reached_so_far is the highest mission item reached *as of this row* --
    using the run-level max would label pre-flight rows 'complete'."""
    hb = latest.get("HEARTBEAT", {})
    base = hb.get("base_mode", 0) or 0
    custom = hb.get("custom_mode", 0) or 0
    if base & MAV_MODE_FLAG_SAFETY_ARMED:
        return "active" if px4_is_auto(custom) else "armed"
    if run.items and reached_so_far >= max(run.items):
        return "complete"          # disarmed after running the last item
    return "inactive"


def build_rows(run, timeline):
    """Walk the dt grid across one run, holding the last-seen value of each
    message (that is what the autopilot's own consumers see between updates).

    Consumes `timeline` with a single advancing cursor, so runs must be
    processed in chronological order.
    """
    lo, hi = run.t_start, run.t_end
    if TRIM and run.armed_windows:
        lo = max(lo, run.armed_windows[0][0] - PAD)
        hi = min(hi, run.armed_windows[-1][1] + PAD)

    latest = {}
    idx = 0
    n = len(timeline)

    # prime with everything already known at the start of the window
    while idx < n and timeline[idx][0] <= lo:
        latest[timeline[idx][1]] = timeline[idx][2]
        idx += 1

    run_id = run_id_from_ts(run.t_start)
    reached = sorted(run.reached_events)
    r_idx = 0
    reached_so_far = -1
    # progress already made before the window opens (matters when TRIM is True)
    while r_idx < len(reached) and reached[r_idx][0] <= lo:
        reached_so_far = max(reached_so_far, reached[r_idx][1])
        r_idx += 1

    rows = []
    t = lo
    step = 0
    while t <= hi:
        while idx < n and timeline[idx][0] <= t:
            latest[timeline[idx][1]] = timeline[idx][2]
            idx += 1
        while r_idx < len(reached) and reached[r_idx][0] <= t:
            reached_so_far = max(reached_so_far, reached[r_idx][1])
            r_idx += 1

        seq = (latest.get("MISSION_CURRENT") or {}).get("seq", 0)
        item = run.items.get(seq, {})

        lat_i, lon_i = item.get("x"), item.get("y")
        if TARGET_LATLON == "deg":
            lat = lat_i / 1e7 if lat_i is not None else None
            lon = lon_i / 1e7 if lon_i is not None else None
        else:
            lat, lon = lat_i, lon_i
        alt = item.get("z")

        row = {c: "" for c in COLUMNS}
        row.update({
            "mission_run_id": run_id,
            "mission_name": MISSION_NAME,
            "timestamp": t,
            "mission_seq": seq,
            "mission_status": mission_status(latest, run, reached_so_far),
            "command_id": item.get("command"),
            "command_name": cmd_name(item["command"]) if "command" in item else "",
            "frame": item.get("frame"),
            "current": item.get("current"),
            "autocontinue": item.get("autocontinue"),
            "param1": item.get("param1"),
            "param2": item.get("param2"),
            "param3": item.get("param3"),
            "param4": item.get("param4"),
            "param5": lat,
            "param6": lon,
            "param7": alt,
            "target_lat": lat,
            "target_lon": lon,
            "target_alt": alt,
            "llm_step_index": step,
            "dt": DT,
            "llm_model_name": MODEL_NAME,
        })
        row.update(state_snapshot(latest))
        rows.append(row)

        t += DT
        step += 1

    # generated_* = the next step's observed state (ground truth)
    if GENERATED == "next":
        for i, row in enumerate(rows):
            if i + 1 >= len(rows):
                continue
            nxt = rows[i + 1]
            for src, dst in STATE_PAIRS:
                row[dst] = nxt[src]

    return rows


# ==========================================================================

def main():
    path = resolve_input()
    runs, timeline, bad = parse_tlog(path)

    if not runs:
        sys.exit("No completed mission upload in this tlog -- nothing to key rows to.")
    if not timeline:
        sys.exit(f"No state messages from system {SYSID}.")

    print(f"{os.path.basename(path)}: {len(timeline)} state msgs, "
          f"{bad} bad frames, {len(runs)} run(s)")
    for r in runs:
        flag = "" if r.flown else "   <-- aborted, never reached an item"
        print(f"  {run_id_from_ts(r.t_start)}  {len(r.items)} items  "
              f"{r.duration():7.1f}s  armed={len(r.armed_windows)}x  "
              f"max_seq_reached={r.max_seq_reached}{flag}")

    keep = [r for r in runs if r.flown] if RUNS == "flown" else runs
    if not keep:
        sys.exit("No flown runs (never armed, or never reached a mission item).")
    if RUNS == "flown" and len(keep) < len(runs):
        print(f"  RUNS='flown' -> dropped {len(runs) - len(keep)} aborted run(s)")

    all_rows = []
    for r in keep:
        all_rows.extend(build_rows(r, timeline))

    with open(OUTPUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        for row in all_rows:
            w.writerow({k: clean(v) for k, v in row.items()})

    print(f"wrote {len(all_rows)} rows x {len(COLUMNS)} cols -> {OUTPUT_CSV}")


if __name__ == "__main__":
    main()