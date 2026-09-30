# """
# module_rag.py

from typing import List, Dict, Any, Optional
from module_helper_functions import TELEM_GROUPS, canonicalize_grouped_snapshot, canonicalize_followup_message

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



# ///// Step 3 — retriever from px4_command_sequences.jsonl ## updated retriever can include up to 5 of each telemetry type, maximum 25 telemetry messages total.
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

        # future_telem = []
        # followups = row.get("followups", [])

        # if isinstance(followups, list):
        #     for item in followups:
        #         if not isinstance(item, dict):
        #             continue

        #         grouped = canonicalize_followup_message(item)

        #         if grouped:
        #             future_telem.append(grouped)

        #         if len(future_telem) >= 5:
        #             break
        
        # trying to update with 5x of each message type instead of top 5 whatever it gets || jul 2026

        future_telem = []
        counts = {name: 0 for name in TELEM_GROUPS}
        followups = row.get("followups", [])

        if isinstance(followups, list):
            for item in followups:
                if not isinstance(item, dict):
                    continue

                name = item.get("type")

                if name not in counts or counts[name] >= 5:
                    continue

                grouped = canonicalize_followup_message(item)

                if grouped:
                    future_telem.append(grouped)
                    counts[name] += 1

                if all(count >= 5 for count in counts.values()):
                    break
        # /////////////////

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