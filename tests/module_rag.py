"""
rag.py

Purpose:
    Retrieve the best matching UAV command examples for LLM prompting.

Supports two dataset styles:
    1) cmd_transition JSONL rows:
       Prev_HB, Prev_Telemetry, Command, Command_ACK, NEXT_Telemetry

    2) px4_command_sequences JSONL rows:
       context_prev_heartbeat, request, ack, followups

6 functions:
load_jsonl()
attach_rag_to_hp()
get_current_state_from_hp()
score_example()
retrieve_best_examples()
retrieve_best_examples_from_hp()

Main idea:
    For the current command, compare the CURRENT heartbeat and CURRENT telemetry
    against each example's previous heartbeat and previous telemetry context.
    Return the best k examples, default k=2.
"""

import json
import math
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# Canonical telemetry fields used by your current simulator
# ============================================================

TELEM_GROUPS = {
    "GLOBAL_POSITION_INT": [
        "lat", "lon", "alt", "relative_alt", "vx", "vy", "vz", "hdg"
    ],
    "ATTITUDE": [
        "roll", "pitch", "yaw"
    ],
    "VFR_HUD": [
        "groundspeed", "heading", "throttle", "alt", "climb"
    ],
    "SYS_STATUS": [
        "battery_remaining", "voltage_battery", "load"
    ],
    "GPS_RAW_INT": [
        "fix_type"
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


# Field-specific scale values for numeric similarity.
# Bigger scale = more tolerant difference.
FIELD_SCALE = {
    ("GLOBAL_POSITION_INT", "lat"): 200000.0,          # about 0.02 deg * 1e7
    ("GLOBAL_POSITION_INT", "lon"): 200000.0,
    ("GLOBAL_POSITION_INT", "alt"): 20000.0,           # mm
    ("GLOBAL_POSITION_INT", "relative_alt"): 20000.0,  # mm
    ("GLOBAL_POSITION_INT", "vx"): 500.0,              # cm/s
    ("GLOBAL_POSITION_INT", "vy"): 500.0,
    ("GLOBAL_POSITION_INT", "vz"): 300.0,
    ("GLOBAL_POSITION_INT", "hdg"): 9000.0,            # centideg

    ("ATTITUDE", "roll"): 0.5,                         # rad
    ("ATTITUDE", "pitch"): 0.5,
    ("ATTITUDE", "yaw"): 1.0,

    ("VFR_HUD", "groundspeed"): 5.0,                   # m/s
    ("VFR_HUD", "heading"): 90.0,                      # deg
    ("VFR_HUD", "throttle"): 40.0,                     # percent
    ("VFR_HUD", "alt"): 20.0,                          # m
    ("VFR_HUD", "climb"): 3.0,                         # m/s

    ("SYS_STATUS", "battery_remaining"): 30.0,
    ("SYS_STATUS", "voltage_battery"): 3000.0,
    ("SYS_STATUS", "load"): 800.0,

    ("GPS_RAW_INT", "fix_type"): 2.0,
}


# ============================================================
# Loading
# ============================================================

def load_jsonl(path: str) -> List[Dict[str, Any]]:
    """Load JSONL rows. Bad lines are skipped."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        rows.append(obj)
                except Exception:
                    continue
    except Exception as e:
        print(f"[RAG] failed to load {path}: {e}", flush=True)

    print(f"[RAG] loaded {len(rows)} rows from {path}", flush=True)
    return rows


class RagStore:
    """
    Small container for both example sources.

    Example:
        rag = RagStore(
            transition_path="./set_of_tlogs/cmd_transition_t1.jsonl",
            sequence_path="./set_of_tlogs/px4_command_sequences_t1.jsonl"
        )
    """
    def __init__(self, transition_path: Optional[str] = None, sequence_path: Optional[str] = None):
        self.transition_rows = load_jsonl(transition_path) if transition_path else []
        self.sequence_rows = load_jsonl(sequence_path) if sequence_path else []


# ============================================================
# Current state extraction
# ============================================================

def heartbeat_from_state(state: Any) -> Dict[str, Any]:
    """Convert CommonState-like object into compact heartbeat dict."""
    if state is None:
        return {}
    if isinstance(state, dict):
        return compact_heartbeat(state)

    return {
        "type": getattr(state, "hb_type", None),
        "autopilot": getattr(state, "hb_autopilot", None),
        "base_mode": getattr(state, "base_mode", None),
        "custom_mode": getattr(state, "custom_mode", None),
        "system_status": getattr(state, "system_status", None),
        "mavlink_version": getattr(state, "mavlink_version", None),
    }


def telemetry_from_state(state: Any) -> Dict[str, Dict[str, Any]]:
    """Convert CommonState-like object into grouped canonical telemetry."""
    if state is None:
        return {}

    if isinstance(state, dict):
        return canonicalize_grouped_snapshot(state)

    out: Dict[str, Dict[str, Any]] = {}
    for internal_name, (msg_name, field_name) in INTERNAL_TO_CANONICAL.items():
        if hasattr(state, internal_name):
            out.setdefault(msg_name, {})[field_name] = getattr(state, internal_name)

    return out


def snapshot_from_hp(hp: Any) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """
    Safely extract current heartbeat and telemetry from your hp object.
    Uses hp.state_lock when available.
    """
    if hp is None or not hasattr(hp, "state"):
        return {}, {}

    lock = getattr(hp, "state_lock", None)
    if lock is not None:
        with lock:
            hb = heartbeat_from_state(hp.state)
            telem = telemetry_from_state(hp.state)
        return hb, telem

    return heartbeat_from_state(hp.state), telemetry_from_state(hp.state)


# ============================================================
# Canonicalization helpers
# ============================================================

def compact_heartbeat(hb: Any) -> Dict[str, Any]:
    if not isinstance(hb, dict):
        return {}

    return {
        "type": hb.get("type") or hb.get("hb_type") or hb.get("mavpackettype") or hb.get("_type"),
        "autopilot": hb.get("autopilot") or hb.get("hb_autopilot"),
        "base_mode": hb.get("base_mode"),
        "custom_mode": hb.get("custom_mode"),
        "system_status": hb.get("system_status"),
        "mavlink_version": hb.get("mavlink_version"),
    }


def canonicalize_grouped_snapshot(snapshot: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for raw_msg_name, fields in snapshot.items():
        msg_name = str(raw_msg_name).upper()
        if msg_name not in TELEM_GROUPS_SET or not isinstance(fields, dict):
            continue

        kept = {k: v for k, v in fields.items() if k in TELEM_GROUPS_SET[msg_name]}
        if kept:
            out[msg_name] = kept

    return out


def canonicalize_followup_message(msg: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(msg, dict):
        return {}

    msg_type = str(msg.get("type") or msg.get("mavpackettype") or msg.get("_type") or "").upper()
    if msg_type not in TELEM_GROUPS_SET:
        return {}

    kept = {k: v for k, v in msg.items() if k in TELEM_GROUPS_SET[msg_type]}
    if not kept:
        return {}

    return {msg_type: kept}


# ============================================================
# Row parsing: supports both datasets
# ============================================================

def _to_int_or_none(x: Any) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def row_command_id(row: Dict[str, Any]) -> Optional[int]:
    """Find command id from transition row or sequence row."""
    if not isinstance(row, dict):
        return None

    for key in ("Command", "command", "request"):
        obj = row.get(key)
        if isinstance(obj, dict):
            cmd_id = _to_int_or_none(obj.get("command") or obj.get("id") or obj.get("cmd_id"))
            if cmd_id is not None:
                return cmd_id
        else:
            cmd_id = _to_int_or_none(obj)
            if cmd_id is not None:
                return cmd_id

    return _to_int_or_none(row.get("command_id") or row.get("cmd_id"))


def transition_row_to_example(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize cmd_transition-style row."""
    cmd = row.get("Command", {}) if isinstance(row.get("Command"), dict) else {}

    return {
        "source": "transition",
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
        "prev_heartbeat": compact_heartbeat(row.get("Prev_HB", {})),
        "prev_telemetry": canonicalize_grouped_snapshot(row.get("Prev_Telemetry", {})),
        "ack": row.get("Command_ACK", {}),
        "future_telemetry": canonicalize_grouped_snapshot(row.get("NEXT_Telemetry", {})),
    }


def sequence_row_to_example(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize px4_command_sequences-style row."""
    req = row.get("request", {}) if isinstance(row.get("request"), dict) else {}

    future_telem: List[Dict[str, Dict[str, Any]]] = []
    for item in row.get("followups", []) if isinstance(row.get("followups"), list) else []:
        grouped = canonicalize_followup_message(item)
        if grouped:
            future_telem.append(grouped)
        if len(future_telem) >= 10:
            break

    return {
        "source": "sequence",
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
        "prev_heartbeat": compact_heartbeat(row.get("context_prev_heartbeat", {})),
        "prev_telemetry": {},
        "ack": row.get("ack", {}),
        "future_telemetry": future_telem,
    }


def normalize_row(row: Dict[str, Any], source: str) -> Dict[str, Any]:
    if source == "transition":
        return transition_row_to_example(row)
    return sequence_row_to_example(row)


# ============================================================
# Similarity scoring
# ============================================================

def _as_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        y = float(x)
        if math.isnan(y) or math.isinf(y):
            return None
        return y
    except Exception:
        return None


def numeric_similarity(a: Any, b: Any, scale: float) -> Optional[float]:
    fa = _as_float(a)
    fb = _as_float(b)
    if fa is None or fb is None:
        return None
    return 1.0 / (1.0 + abs(fa - fb) / max(scale, 1e-9))


def heartbeat_score(current_hb: Dict[str, Any], example_hb: Dict[str, Any]) -> float:
    if not current_hb or not example_hb:
        return 0.0

    weights = {
        "type": 1.0,
        "autopilot": 1.0,
        "base_mode": 2.0,
        "custom_mode": 2.0,
        "system_status": 2.0,
        "mavlink_version": 0.5,
    }

    got = 0.0
    total = 0.0
    for key, weight in weights.items():
        cv = current_hb.get(key)
        ev = example_hb.get(key)
        if cv is None or ev is None:
            continue
        total += weight
        got += weight if str(cv) == str(ev) else 0.0

    return got / total if total > 0 else 0.0


def telemetry_score(current_telem: Dict[str, Dict[str, Any]], example_telem: Dict[str, Dict[str, Any]]) -> float:
    if not current_telem or not example_telem:
        return 0.0

    scores: List[float] = []
    for msg_name, fields in current_telem.items():
        if msg_name not in example_telem:
            continue
        if not isinstance(fields, dict) or not isinstance(example_telem[msg_name], dict):
            continue

        for field_name, current_value in fields.items():
            if field_name not in example_telem[msg_name]:
                continue
            scale = FIELD_SCALE.get((msg_name, field_name), 1.0)
            s = numeric_similarity(current_value, example_telem[msg_name].get(field_name), scale)
            if s is not None:
                scores.append(s)

    return sum(scores) / len(scores) if scores else 0.0


def command_param_score(current_params: Dict[str, Any], example_cmd: Dict[str, Any]) -> float:
    """
    Optional tie-breaker.
    For mission commands, target lat/lon/alt similarity helps pick closer examples.
    """
    if not isinstance(current_params, dict) or not isinstance(example_cmd, dict):
        return 0.0

    pairs = [
        ("param5", 200000.0),
        ("param6", 200000.0),
        ("param7", 50.0),
    ]

    scores = []
    for key, scale in pairs:
        s = numeric_similarity(current_params.get(key), example_cmd.get(key), scale)
        if s is not None:
            scores.append(s)

    return sum(scores) / len(scores) if scores else 0.0


def example_score(
    current_hb: Dict[str, Any],
    current_telem: Dict[str, Dict[str, Any]],
    current_params: Dict[str, Any],
    example: Dict[str, Any],
) -> float:
    hb_s = heartbeat_score(current_hb, example.get("prev_heartbeat", {}))
    telem_s = telemetry_score(current_telem, example.get("prev_telemetry", {}))
    param_s = command_param_score(current_params, example.get("command", {}))

    # Transition rows usually have previous telemetry, sequence rows may not.
    # So heartbeat is always important; telemetry dominates when available.
    if example.get("prev_telemetry"):
        return (0.25 * hb_s) + (0.60 * telem_s) + (0.15 * param_s)

    return (0.70 * hb_s) + (0.30 * param_s)


# ============================================================
# Public retriever
# ============================================================

# def retrieve_best_examples(
#     transition_rows: List[Dict[str, Any]],
#     sequence_rows: List[Dict[str, Any]],
#     command_id: int,
#     current_heartbeat: Dict[str, Any],
#     current_telemetry: Dict[str, Dict[str, Any]],
#     current_params: Optional[Dict[str, Any]] = None,
#     k: int = 2,
# ) -> List[Dict[str, Any]]:
#     """
#     Return best k examples across both transition and sequence datasets.
#     """
#     current_params = current_params or {}
#     current_heartbeat = compact_heartbeat(current_heartbeat)
#     current_telemetry = canonicalize_grouped_snapshot(current_telemetry)

#     candidates: List[Dict[str, Any]] = []

#     for row in transition_rows or []:
#         if row_command_id(row) == int(command_id):
#             ex = normalize_row(row, "transition")
#             ex["score"] = example_score(current_heartbeat, current_telemetry, current_params, ex)
#             candidates.append(ex)

#     for row in sequence_rows or []:
#         if row_command_id(row) == int(command_id):
#             ex = normalize_row(row, "sequence")
#             ex["score"] = example_score(current_heartbeat, current_telemetry, current_params, ex)
#             candidates.append(ex)

#     candidates.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
#     return candidates[:max(1, int(k))]
def retrieve_best_examples(
    transition_rows,
    sequence_rows,
    command_id,
    current_heartbeat,
    current_telemetry,
    current_params=None,
    k=2,
):
    current_params = current_params or {}
    current_heartbeat = compact_heartbeat(current_heartbeat)
    current_telemetry = canonicalize_grouped_snapshot(current_telemetry)

    candidates = []

    for row in transition_rows or []:
        if row_command_id(row) == int(command_id):
            ex = normalize_row(row, "transition")
            ex["_score"] = example_score(current_heartbeat, current_telemetry, current_params, ex)
            candidates.append(ex)

    for row in sequence_rows or []:
        if row_command_id(row) == int(command_id):
            ex = normalize_row(row, "sequence")
            ex["_score"] = example_score(current_heartbeat, current_telemetry, current_params, ex)
            candidates.append(ex)

    candidates.sort(key=lambda x: float(x.get("_score", 0.0)), reverse=True)

    selected = candidates[:max(1, int(k))]

    for ex in selected:
        ex.pop("_score", None)

    return selected


def retrieve_best_examples_from_hp(
    hp: Any,
    command_id: int,
    params: Optional[Dict[str, Any]] = None,
    k: int = 2,
) -> List[Dict[str, Any]]:
    """
    Convenience function for your simulator class.

    Required hp attributes:
        hp.state
        hp.rag_transition_rows or hp.rag_transitions
        hp.rag_sequence_rows or hp.rag_sequences
    """
    current_hb, current_telem = snapshot_from_hp(hp)

    transition_rows = (
        getattr(hp, "rag_transition_rows", None)
        or getattr(hp, "rag_transitions", None)
        or []
    )
    sequence_rows = (
        getattr(hp, "rag_sequence_rows", None)
        or getattr(hp, "rag_sequences", None)
        or []
    )

    return retrieve_best_examples(
        transition_rows=transition_rows,
        sequence_rows=sequence_rows,
        command_id=command_id,
        current_heartbeat=current_hb,
        current_telemetry=current_telem,
        current_params=params or {},
        k=k,
    )

def retrieve_best_examples_split_from_hp(
    hp,
    command_id: int,
    params=None,
    k_each: int = 2,
):
    """
    Return:
      - best k_each transition examples
      - best k_each sequence examples

    This preserves your old prompt structure:
      transition_examples: [...]
      sequence_examples: [...]
    """
    current_hb, current_telem = snapshot_from_hp(hp)

    transition_rows = (
        getattr(hp, "rag_transition_rows", None)
        or getattr(hp, "rag_transitions", None)
        or []
    )

    sequence_rows = (
        getattr(hp, "rag_sequence_rows", None)
        or getattr(hp, "rag_sequences", None)
        or []
    )

    transition_examples = retrieve_best_examples(
        transition_rows=transition_rows,
        sequence_rows=[],
        command_id=command_id,
        current_heartbeat=current_hb,
        current_telemetry=current_telem,
        current_params=params or {},
        k=k_each,
    )

    sequence_examples = retrieve_best_examples(
        transition_rows=[],
        sequence_rows=sequence_rows,
        command_id=command_id,
        current_heartbeat=current_hb,
        current_telemetry=current_telem,
        current_params=params or {},
        k=k_each,
    )

    return {
        "transition_examples": transition_examples,
        "sequence_examples": sequence_examples,
    }


def attach_rag_to_hp(
    hp: Any,
    transition_path: Optional[str] = None,
    sequence_path: Optional[str] = None,
) -> None:
    """
    Load examples into your existing hp object.

    Example:
        from rag import attach_rag_to_hp
        attach_rag_to_hp(
            hp,
            "./set_of_tlogs/cmd_transition_t1.jsonl",
            "./set_of_tlogs/px4_command_sequences_t1.jsonl"
        )
    """
    hp.rag_transition_rows = load_jsonl(transition_path) if transition_path else []
    hp.rag_sequence_rows = load_jsonl(sequence_path) if sequence_path else []