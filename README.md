
The codebase in tests utilizes four modules.
module_main.py --> The final module to utilize all other submodules
module_llmhoneypot.py --> the main module that connects, generates heartbeats and telemetry and provides UAV next step of responses
module_historybuffer_commonstate.py --> the module which contains the variable history and the variables for LLM to 'get previous context'
module_helper_functions.py --> this module majorly contains all other supporting functions for the honeypot design.

module_llmhoneypot.py
It initiates the connection to the QGC/GCS and transmits responses as requested.

The main development surrounds: it contains the structure to design effective LLM instructions to generate next steps of the movement.

Command Handling Flow

Command → Rule-based ACK → Heartbeat Update → Telemetry Update
1. Rule-based ACK
Decides if a command is ACCEPTED / DENIED / UNSUPPORTED
Runs before LLM inference

If not accepted → LLM do not generate changes

2. handle_command_heartbeat()

Updates the control state of the drone. Modifies:
base_mode
custom_mode
system_status

Uses:
current command
previous heartbeat
past examples

Purpose: reflect mode/state change (e.g., arm/disarm/takeoff/loiter)

3. handle_command_telemetry()

Generates post-command behavior over time.

Outputs 5 steps (0.0 → 0.4 sec)
Uses:
  current command
  recent telemetry
  past examples
  
Produces structured MAVLink telemetry

Purpose: simulate realistic drone behavior (smooth changes)

4. Incoming MAVLink → handle_inbound_msg()
    → classify message type
    → handle locally OR
    → run rule-based ACK
    → trigger heartbeat + telemetry LLM modules
