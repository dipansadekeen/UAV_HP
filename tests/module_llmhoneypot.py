import socket
import time
import threading
import json
import os
import requests
from collections import deque
from typing import Dict, Any, Optional
from pymavlink import mavutil
from module_historybuffer_commonstate import CommonState, HistoryBuffer
from module_helper_functions import (
    load_command_rag_jsonl,
    rag_retrieve_examples,
    ACK_RESULT_MAP,
    update_time_fields,
    make_heartbeat,
    extract_json,
    TELEM_BUILDERS,
    retrieve_heartbeat_examples_from_sequences,
    retrieve_telemetry_examples_from_cmd_transition,
    retrieve_telemetry_examples_from_sequences,
    canonicalize_internal_history_fields,
    translate_canonical_series_to_internal,
    TELEM_GROUPS,
    TELEM_GROUPS_SET,
    rule_based_ack,
)


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
            # print("[DEBUG] Initial HB State:",
            #     self.state.hb_type,
            #     self.state.hb_autopilot,
            #     self.state.base_mode,
            #     self.state.system_status)
            # self.identity_locked = True


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