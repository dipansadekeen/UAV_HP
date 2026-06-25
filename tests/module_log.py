# module_log.py

import os
import csv
import time
import uuid


def log_mission_llm_csv(hp, command_id, command_name, params, user_payload, parsed):
    """
    Logs one row per LLM-generated telemetry step.
    Each row links the generated telemetry to the active mission item.
    """

    os.makedirs("./logs", exist_ok=True)

    csv_path = "./logs/mission_llm_generation_v2.csv" # v2 updated as we are adding new column
    file_exists = os.path.exists(csv_path)

    llm_call_id = str(uuid.uuid4())

    mission_seq = None
    mission_name = getattr(hp, "mission_name", "qgc_uploaded_mission")
    mission_status = "active"

    if hasattr(hp, "mission_state") and isinstance(hp.mission_state, dict):
        mission_seq = hp.mission_state.get("current_seq")

        if hp.mission_state.get("complete"):
            mission_status = "completed"
        elif not hp.mission_state.get("active"):
            mission_status = "inactive"

    with hp.state_lock:
        input_state = {
            "input_gpi_lat": getattr(hp.state, "gpi_lat", None),
            "input_gpi_lon": getattr(hp.state, "gpi_lon", None),
            "input_gpi_alt": getattr(hp.state, "gpi_alt", None),
            "input_gpi_relative_alt": getattr(hp.state, "gpi_relative_alt", None),
            "input_gpi_vx": getattr(hp.state, "gpi_vx", None),
            "input_gpi_vy": getattr(hp.state, "gpi_vy", None),
            "input_gpi_vz": getattr(hp.state, "gpi_vz", None),
            "input_gpi_hdg": getattr(hp.state, "gpi_hdg", None),

            "input_roll": getattr(hp.state, "roll", None),
            "input_pitch": getattr(hp.state, "pitch", None),
            "input_yaw": getattr(hp.state, "yaw", None),

            "input_vfr_groundspeed": getattr(hp.state, "vfr_groundspeed", None),
            "input_vfr_heading": getattr(hp.state, "vfr_heading", None),
            "input_vfr_throttle": getattr(hp.state, "vfr_throttle", None),
            "input_vfr_alt": getattr(hp.state, "vfr_alt", None),
            "input_vfr_climb": getattr(hp.state, "vfr_climb", None),

            "input_battery_remaining": getattr(hp.state, "battery_remaining", None),
            "input_voltage_battery": getattr(hp.state, "voltage_battery", None),
            "input_load": getattr(hp.state, "load", None),
            "input_gps_fix_type": getattr(hp.state, "gps_fix_type", None),
        }

    fieldnames = [
        "mission_run_id",
        "mission_name",
        "timestamp",
        "mission_seq",
        "mission_status",

        "command_id",
        "command_name",
        "frame",
        "current",
        "autocontinue",

        "param1",
        "param2",
        "param3",
        "param4",
        "param5",
        "param6",
        "param7",

        "target_lat",
        "target_lon",
        "target_alt",

        "llm_call_id",
        "llm_step_index",
        "dt",
        "llm_latency_ms", # new
        "llm_model_name", # new

        "input_gpi_lat",
        "input_gpi_lon",
        "input_gpi_alt",
        "input_gpi_relative_alt",
        "input_gpi_vx",
        "input_gpi_vy",
        "input_gpi_vz",
        "input_gpi_hdg",

        "input_roll",
        "input_pitch",
        "input_yaw",

        "input_vfr_groundspeed",
        "input_vfr_heading",
        "input_vfr_throttle",
        "input_vfr_alt",
        "input_vfr_climb",

        "input_battery_remaining",
        "input_voltage_battery",
        "input_load",
        "input_gps_fix_type",

        "generated_gpi_lat",
        "generated_gpi_lon",
        "generated_gpi_alt",
        "generated_gpi_relative_alt",
        "generated_gpi_vx",
        "generated_gpi_vy",
        "generated_gpi_vz",
        "generated_gpi_hdg",

        "generated_att_roll",
        "generated_att_pitch",
        "generated_att_yaw",

        "generated_vfr_groundspeed",
        "generated_vfr_heading",
        "generated_vfr_throttle",
        "generated_vfr_alt",
        "generated_vfr_climb",

        "generated_sys_battery_remaining",
        "generated_sys_voltage_battery",
        "generated_sys_load",
        "generated_gps_fix_type",
    ]

    p = {
        "param1": float(params.get("param1", 0.0)),
        "param2": float(params.get("param2", 0.0)),
        "param3": float(params.get("param3", 0.0)),
        "param4": float(params.get("param4", 0.0)),
        "param5": float(params.get("param5", 0.0)),
        "param6": float(params.get("param6", 0.0)),
        "param7": float(params.get("param7", 0.0)),
    }

    series = parsed.get("telemetry_series", [])

    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        for i, step in enumerate(series):
            fields = step.get("fields", {})

            gpi = fields.get("GLOBAL_POSITION_INT", {})
            att = fields.get("ATTITUDE", {})
            vfr = fields.get("VFR_HUD", {})
            sys_status = fields.get("SYS_STATUS", {})
            gps = fields.get("GPS_RAW_INT", {})

            row = {
                "mission_run_id": getattr(hp, "mission_run_id", "run_001"),
                "mission_name": mission_name,
                "timestamp": time.time(),
                "mission_seq": mission_seq,
                "mission_status": mission_status,

                "command_id": int(command_id),
                "command_name": command_name,
                "frame": params.get("frame"),
                "current": params.get("current"),
                "autocontinue": params.get("autocontinue"),

                "param1": p["param1"],
                "param2": p["param2"],
                "param3": p["param3"],
                "param4": p["param4"],
                "param5": p["param5"],
                "param6": p["param6"],
                "param7": p["param7"],

                "target_lat": p["param5"],
                "target_lon": p["param6"],
                "target_alt": p["param7"],

                "llm_call_id": llm_call_id,
                "llm_step_index": i,
                "dt": step.get("dt"),
                "llm_latency_ms": getattr(hp, "last_llm_latency_ms", None), # new
                "llm_model_name": getattr(hp, "last_llm_model_name", None), # new

                **input_state,

                "generated_gpi_lat": gpi.get("lat"),
                "generated_gpi_lon": gpi.get("lon"),
                "generated_gpi_alt": gpi.get("alt"),
                "generated_gpi_relative_alt": gpi.get("relative_alt"),
                "generated_gpi_vx": gpi.get("vx"),
                "generated_gpi_vy": gpi.get("vy"),
                "generated_gpi_vz": gpi.get("vz"),
                "generated_gpi_hdg": gpi.get("hdg"),

                "generated_att_roll": att.get("roll"),
                "generated_att_pitch": att.get("pitch"),
                "generated_att_yaw": att.get("yaw"),

                "generated_vfr_groundspeed": vfr.get("groundspeed"),
                "generated_vfr_heading": vfr.get("heading"),
                "generated_vfr_throttle": vfr.get("throttle"),
                "generated_vfr_alt": vfr.get("alt"),
                "generated_vfr_climb": vfr.get("climb"),

                "generated_sys_battery_remaining": sys_status.get("battery_remaining"),
                "generated_sys_voltage_battery": sys_status.get("voltage_battery"),
                "generated_sys_load": sys_status.get("load"),
                "generated_gps_fix_type": gps.get("fix_type"),
            }

            writer.writerow(row)

    print(f"[MISSION CSV] logged {len(series)} LLM steps to {csv_path}", flush=True)