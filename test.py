import requests
import time
import json


# ============================
# CONFIG
# ============================

OLLAMA_URL = "http://192.168.1.100:11434"
OLLAMA_MODEL = "gpt-oss:20b"
TIMEOUT = 1200


# ============================
# TEST PROMPT
# ============================

system_text = """
[TELEM LLM USER PROMPT]
{"command": {"id": 22, "name": "MAV_CMD_NAV_TAKEOFF", "params": {"param1": 0.0, "param2": 0.0, "param3": 0.0, "param4": NaN, "param5": 473975577.0, "param6": 85457262.0, "param7": 50.0}}, "current_heartbeat": {"base_mode": 157, "custom_mode": 67371008, "system_status": 4}, "last_5_telemetry": [{"name": "SYS_STATUS", "ts": 1787348018.6942787, "fields": {"SYS_STATUS": {"battery_remaining": 100, "voltage_battery": 12000, "load": 400}}}, {"name": "GPS_RAW_INT", "ts": 1787348018.6943133, "fields": {"GPS_RAW_INT": {"fix_type": 3}}}, {"name": "GLOBAL_POSITION_INT", "ts": 1787348018.6944754, "fields": {"GLOBAL_POSITION_INT": {"lat": 473979709, "lon": 85461637, "alt": 0, "relative_alt": 0, "vx": 0, "vy": 0, "vz": 0, "hdg": 0}}}, {"name": "ATTITUDE", "ts": 1787348018.6945012, "fields": {"ATTITUDE": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}}}, {"name": "VFR_HUD", "ts": 1787348018.6945188, "fields": {"VFR_HUD": {"groundspeed": 0.0, "heading": 0, "throttle": 0, "alt": 0.0, "climb": 0.0}}}], "transition_examples": [{"command": {"command": 22, "param1": 0.0, "param2": 0.0, "param3": 0.0, "param4": null, "param5": 474016762, "param6": 85443849, "param7": 50.0}, "prev_heartbeat": {"type": 2, "autopilot": 12, "base_mode": 157, "custom_mode": 67371008, "system_status": 4, "mavlink_version": 3}, "prev_telemetry": {}, "command_ack": {"command": 22, "result": 0, "progress": 0, "result_param2": 0, "target_system": 1, "target_component": 1}, "future_telemetry": {"SYS_STATUS": {"load": 974, "voltage_battery": 16200, "battery_remaining": 100}, "GPS_RAW_INT": {"fix_type": 3}, "GLOBAL_POSITION_INT": {"lat": 474016628, "lon": 85443981, "alt": 50218, "relative_alt": 49989, "vx": 291, "vy": -191, "vz": -1, "hdg": 32636}, "ATTITUDE": {"roll": -0.00442530820146203, "pitch": -0.07209382951259613, "yaw": -0.5870041251182556}, "VFR_HUD": {"groundspeed": 3.55532169342041, "heading": 326, "throttle": 72, "alt": 50.221519470214844, "climb": 0.014158576726913452}}}, {"command": {"command": 22, "param1": 0.0, "param2": 0.0, "param3": 0.0, "param4": null, "param5": 473979712, "param6": 85461642, "param7": 50.0}, "prev_heartbeat": {"type": 2, "autopilot": 12, "base_mode": 29, "custom_mode": 50593792, "system_status": 3, "mavlink_version": 3}, "prev_telemetry": {"SYS_STATUS": {"load": 1121, "voltage_battery": 16200, "battery_remaining": 100}, "GPS_RAW_INT": {"fix_type": 3}, "GLOBAL_POSITION_INT": {"lat": 473979710, "lon": 85461638, "alt": 179, "relative_alt": -45, "vx": 0, "vy": 0, "vz": 2, "hdg": 9559}, "ATTITUDE": {"roll": 0.0031386471819132566, "pitch": -0.0015583649510517716, "yaw": 1.6684123277664185}, "VFR_HUD": {"groundspeed": 0.0049963705241680145, "heading": 95, "throttle": 0, "alt": 0.17954909801483154, "climb": -0.02252829261124134}}, "command_ack": null, "future_telemetry": {"SYS_STATUS": {"load": 1121, "voltage_battery": 16200, "battery_remaining": 100}, "GPS_RAW_INT": {"fix_type": 3}, "GLOBAL_POSITION_INT": {"lat": 473979710, "lon": 85461638, "alt": 179, "relative_alt": -45, "vx": 0, "vy": 0, "vz": 2, "hdg": 9559}, "ATTITUDE": {"roll": 0.0031209972221404314, "pitch": -0.0015570038231089711, "yaw": 1.6684192419052124}, "VFR_HUD": {"groundspeed": 0.0049963705241680145, "heading": 95, "throttle": 0, "alt": 0.17954909801483154, "climb": -0.02252829261124134}}}], "sequence_examples": [{"command": {"command": 22, "param1": -1.0, "param2": 0.0, "param3": 0.0, "param4": null, "param5": null, "param6": null, "param7": 3.296698808670044}, "ack": {"type": "COMMAND_ACK", "command": 22, "result": 0, "progress": 0, "result_param2": 0}, "future_telemetry": [{"GLOBAL_POSITION_INT": {"lat": 473979711, "lon": 85461637, "alt": 246, "vx": 0, "vy": 0, "vz": 0}}, {"GLOBAL_POSITION_INT": {"lat": 473979711, "lon": 85461637, "alt": 246, "vx": 0, "vy": 0, "vz": 0}}, {"GLOBAL_POSITION_INT": {"lat": 473979711, "lon": 85461637, "alt": 246, "vx": 0, "vy": 0, "vz": 0}}, {"GLOBAL_POSITION_INT": {"lat": 473979711, "lon": 85461637, "alt": 246, "vx": 0, "vy": 0, "vz": 0}}, {"GLOBAL_POSITION_INT": {"lat": 473979711, "lon": 85461637, "alt": 246, "vx": 0, "vy": 0, "vz": 0}}, {"VFR_HUD": {"alt": 0.2463895082473755, "groundspeed": 0.007804920896887779, "throttle": 3, "climb": -0.0015170399565249681}}]}, {"command": {"command": 22, "param1": -1.0, "param2": 0.0, "param3": 0.0, "param4": null, "param5": null, "param6": null, "param7": 3.276357412338257}, "ack": {"type": "COMMAND_ACK", "command": 22, "result": 0, "progress": 0, "result_param2": 0}, "future_telemetry": [{"GLOBAL_POSITION_INT": {"lat": 473995309, "lon": 85464989, "alt": 226, "vx": 0, "vy": 0, "vz": 0}}, {"GLOBAL_POSITION_INT": {"lat": 473995309, "lon": 85464989, "alt": 226, "vx": 0, "vy": 0, "vz": 0}}, {"GLOBAL_POSITION_INT": {"lat": 473995308, "lon": 85464989, "alt": 226, "vx": 0, "vy": 0, "vz": 0}}, {"GLOBAL_POSITION_INT": {"lat": 473995308, "lon": 85464989, "alt": 226, "vx": 0, "vy": 0, "vz": 0}}, {"GLOBAL_POSITION_INT": {"lat": 473995308, "lon": 85464989, "alt": 226, "vx": 0, "vy": 0, "vz": 0}}, {"VFR_HUD": {"alt": 0.2262149155139923, "groundspeed": 0.007380280178040266, "throttle": 3, "climb": -0.007120846770703793}}]}], "allowed_telemetry_groups": {"GLOBAL_POSITION_INT": ["lat", "lon", "alt", "relative_alt", "vx", "vy", "vz", "hdg"], "ATTITUDE": ["roll", "pitch", "yaw"], "VFR_HUD": ["groundspeed", "heading", "throttle", "alt", "climb"], "SYS_STATUS": ["battery_remaining", "voltage_battery", "load"], "GPS_RAW_INT": ["fix_type"]}, "instruction": "Generate the next 5 telemetry states using canonical MAVLink field names grouped by message."}
"""

user_text = """
        You are a MAVLink telemetry predictor for a drone honeypot.

        You are given:
        - the current command
        - the current heartbeat
        - the most recent live telemetry context
        - transition examples from past traces
        - short future telemetry examples from past traces
        - the allowed telemetry schema

        Your task:
        - generate the next 5 telemetry states after this command
        - use only canonical MAVLink telemetry names
        - group telemetry by MAVLink message name
        - follow the allowed telemetry schema exactly
        - keep the sequence physically consistent and smooth
        - preserve continuity from the latest telemetry state

        Return ONLY valid JSON in exactly this format:
        {
        "telemetry_series": [
            {"dt": 0.5, "fields": {}},
            {"dt": 1.0, "fields": {}},
            {"dt": 1.5, "fields": {}},
            {"dt": 2.0, "fields": {}},
            {"dt": 2.5, "fields": {}}
        ],
        "reason": "<short>"
        }

        Rules:
        - Only use message groups and fields defined in allowed_telemetry_groups.
        - Do not use internal or private variable names.
        - Keep all changes smooth, realistic, and temporally consistent.
        - The first telemetry state at dt=0.5 must begin from the latest available telemetry state.
        - Use examples only to learn which fields change and the style of change. Never copy absolute values.
        - Always include SYS_STATUS.battery_remaining.
        - If a field does not need to change, keep it unchanged or omit it.
        - Keep telemetry internally consistent across message groups.

        Command-specific behavior:
        - ARM/DISARM (400): may change armed-related behavior, but must not simulate takeoff unless a takeoff command is given.
        - TAKEOFF (22): perform a smooth vertical climb. Horizontal movement should remain minimal. Relative altitude must increase toward the target altitude.
        - WAYPOINT (16): move smoothly toward target latitude and longitude while maintaining target altitude.
        - LAND (21): descend smoothly toward ground with minimal horizontal movement.
        - Return to Launch (20): like WAYPOINT you have to reach to coordinates.
        
        For WAYPOINT (16):
        - Move in a straight line from current position to the target position.
        - At every step, reduce the distance to the target.
        - Do not move sideways or away from the direct path.
        - If any drift occurs, correct the direction back toward the straight path.
        - Maintain smooth and consistent velocity toward the target.

        Completion requirement:
        - The generated 5-step sequence must move the drone toward the command target.
        - If the target is reachable within 5 steps, fully complete it.
        - If the target is not realistically reachable within 5 steps, make strong, consistent progress toward it without unrealistic jumps.
        - Do not overshoot and reverse direction within the same 5 steps.

        Consistency requirements:
        - GLOBAL_POSITION_INT.relative_alt and VFR_HUD.alt must follow the same trend.
        - If altitude increases, climb should be positive or zero.
        - If altitude decreases, climb should be negative or zero.
        - Do not abruptly change direction unless already indicated by current telemetry.
        - Keep velocity, altitude, and heading changes smooth and consistent.

        Kinematic realism:
        - First update position: GLOBAL_POSITION_INT.lat/lon/relative_alt and VFR_HUD.alt.
        - VFR_HUD.alt is meters; GLOBAL_POSITION_INT.relative_alt is millimeters, so relative_alt = VFR_HUD.alt × 1000.
        - For normal waypoint flight, choose groundspeed around 8–10.
        - For takeoff/landing, keep horizontal groundspeed low, around 0 m/s.
        - If groundspeed is 0, lat/lon must not change.
        - If the waypoint is closer than the allowed movement, slow down and stop at the target.
        - vx/vy/vz must describe the same movement shown by lat/lon/altitude. Use cm/s integers.
        - VFR_HUD.heading must point in the same direction as lat/lon movement.
        - GLOBAL_POSITION_INT.hdg = VFR_HUD.heading × 100.
        - ATTITUDE.yaw must match heading in radians.
        - Pitch should be near 0, slightly positive during climb, and slightly negative during descent.
        - Roll should be near 0 unless the drone is turning.
        
        Return JSON only.
"""


# ============================
# OLLAMA CALL
# ============================

payload = {
    "model": OLLAMA_MODEL,
    "messages": [
        {
            "role": "system",
            "content": system_text
        },
        {
            "role": "user",
            "content": user_text
        }
    ],
    "stream": False,
    "format": "json",
    "options": {
        "temperature": 0,
        "top_p": 0.9
    }
}


print("==============================")
print("Calling Ollama")
print("URL:", f"{OLLAMA_URL}/api/chat")
print("MODEL:", OLLAMA_MODEL)
print("==============================")


t0 = time.monotonic()

try:

    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json=payload,
        timeout=TIMEOUT
    )

    latency = (time.monotonic() - t0) * 1000


    print("\nHTTP STATUS:", r.status_code)

    print(
        f"Latency: {latency:.2f} ms "
        f"({latency/1000:.2f} sec)"
    )


    r.raise_for_status()


    response_json = r.json()

    raw = response_json["message"]["content"]


    print("\n========== RAW RESPONSE ==========")
    print(raw)


    print("\n========== FULL JSON ==========")
    print(
        json.dumps(
            response_json,
            indent=2
        )
    )


except requests.exceptions.Timeout:

    latency = (time.monotonic() - t0) * 1000

    print(
        f"\nTIMEOUT after {latency/1000:.2f} seconds"
    )


except Exception as e:

    latency = (time.monotonic() - t0) * 1000

    print(
        f"\nERROR after {latency/1000:.2f} seconds"
    )

    print(e)