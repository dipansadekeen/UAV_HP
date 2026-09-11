#!/usr/bin/env python3
from pymavlink import mavutil
import time, csv, os, subprocess

PX4="udpin:127.0.0.1:14550"; ACK_OUT="ack_capture.csv"; HB_OUT="heartbeat_capture.csv"; FORCE=21196
PX4_COMMANDER=os.environ.get(
    "PX4_COMMANDER",
    os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "PX4-Autopilot",
        "build", "px4_sitl_default", "bin", "px4-commander"
    ))
)

# ========================= CONNECT =========================
m=mavutil.mavlink_connection(PX4)
print("[GCS] Waiting for PX4...")
first_hb=m.wait_heartbeat()
SYS,COMP=m.target_system,m.target_component
print(f"[GCS] Connected system={SYS} component={COMP}")

# ========================= LIVE STATE =========================
S={"armed":False,"hb_type":None,"hb_autopilot":None,"base_mode":None,"custom_mode":None,
   "system_status":None,"mavlink_version":None,"hb_count":0,"landed":None,
   "alt":0.0,"amsl_alt":None,"lat":0,"lon":0,"heading":0}

# ========================= HEARTBEAT LOG =========================
hb_fields=["timestamp","src_system","src_component","type","autopilot","base_mode","custom_mode",
           "system_status","mavlink_version","armed","landed_state","relative_alt_m"]
hb_new=not os.path.exists(HB_OUT); hb_f=open(HB_OUT,"a",newline=""); hb_w=csv.DictWriter(hb_f,fieldnames=hb_fields)
if hb_new: hb_w.writeheader()

def update(msg):
    t=msg.get_type()
    if t=="HEARTBEAT":
        S.update({"hb_type":int(msg.type),"hb_autopilot":int(msg.autopilot),"base_mode":int(msg.base_mode),
                  "custom_mode":int(msg.custom_mode),"system_status":int(msg.system_status),
                  "mavlink_version":int(msg.mavlink_version),"armed":bool(msg.base_mode&0x80)})
        S["hb_count"]+=1
        hb_w.writerow({"timestamp":time.time(),"src_system":msg.get_srcSystem(),"src_component":msg.get_srcComponent(),
                       "type":S["hb_type"],"autopilot":S["hb_autopilot"],"base_mode":S["base_mode"],
                       "custom_mode":S["custom_mode"],"system_status":S["system_status"],
                       "mavlink_version":S["mavlink_version"],"armed":int(S["armed"]),
                       "landed_state":S["landed"],"relative_alt_m":round(S["alt"],3)})
        hb_f.flush()
    elif t=="EXTENDED_SYS_STATE": S["landed"]=int(msg.landed_state)
    elif t=="GLOBAL_POSITION_INT":
        S["lat"],S["lon"]=int(msg.lat),int(msg.lon)
        S["amsl_alt"],S["alt"]=float(msg.alt)/1000.0,float(msg.relative_alt)/1000.0
        S["heading"]=int(msg.hdg)

update(first_hb)

# def pump(sec=.3):
#     end=time.time()+sec
#     while time.time()<end:
#         x=m.recv_match(blocking=True,timeout=.1)
#         if x: update(x)

last_gcs_hb=0

def pump(sec=.3):
    global last_gcs_hb
    end=time.time()+sec

    while time.time()<end:

        if time.time()-last_gcs_hb>=1:
            m.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_GCS,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0,0,
                mavutil.mavlink.MAV_STATE_ACTIVE
            )
            last_gcs_hb=time.time()

        x=m.recv_match(blocking=True,timeout=.1)
        if x: update(x)
def wait_for(check,timeout=20):
    end=time.time()+timeout
    while time.time()<end:
        pump(.2)
        if check(): return True
    return False

def wait_hb(timeout=2):
    n=S["hb_count"]
    return wait_for(lambda:S["hb_count"]>n,timeout)

# ========================= STATE =========================
GROUND=mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND
AIR=mavutil.mavlink.MAV_LANDED_STATE_IN_AIR
TAKING_OFF=mavutil.mavlink.MAV_LANDED_STATE_TAKEOFF
LANDING=mavutil.mavlink.MAV_LANDED_STATE_LANDING

def state_name():
    if S["landed"]==TAKING_OFF: return "TAKEOFF"
    if S["landed"]==LANDING: return "LANDING"
    if not S["armed"] and S["alt"]<.3: return "DG"
    if S["armed"] and (S["landed"]==GROUND or S["alt"]<.3): return "AG"
    if S["armed"] and (S["landed"]==AIR or S["alt"]>=.5): return "AIR"
    return "OTHER"

def snapshot():
    pump(.3)
    return {"state":state_name(),"armed":int(S["armed"]),"landed":S["landed"],"alt":round(S["alt"],3),
            "base_mode":S["base_mode"],"custom_mode":S["custom_mode"],"system_status":S["system_status"],
            "lat":S["lat"],"lon":S["lon"]}

# ========================= REQUEST TELEMETRY =========================
def request_msg(mid,hz=5):
    m.mav.command_long_send(SYS,COMP,mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,0,mid,1_000_000/hz,0,0,0,0,0)

request_msg(mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT)
request_msg(mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE)
pump(2)

# ========================= ACK LOG =========================
FIELDS=["timestamp","purpose","pre_state","pre_armed","pre_landed","pre_alt","pre_base_mode","pre_custom_mode",
        "pre_system_status","operation","command","param1","param2","param3","param4","param5","param6","param7",
        "ack_result","ack_name","ack_progress","ack_result_param2","post_state","post_armed","post_landed",
        "post_alt","post_base_mode","post_custom_mode","post_system_status"]

ack_new=not os.path.exists(ACK_OUT); ack_f=open(ACK_OUT,"a",newline=""); ack_w=csv.DictWriter(ack_f,fieldnames=FIELDS)
if ack_new: ack_w.writeheader()

def ack_name(r):
    try: return mavutil.mavlink.enums["MAV_RESULT"][int(r)].name
    except: return str(r)

def send_long(cmd,p=None,operation="",purpose="test",timeout=5):
    p=list(p or [0]*7)+[0]*7; p=p[:7]
    wait_hb(1.5); pre=snapshot()
    print(f"\n[{purpose.upper()}] {pre['state']} -> {operation} (cmd={cmd})")

    m.mav.command_long_send(SYS,COMP,int(cmd),0,*p)
    ack=None; end=time.time()+timeout

    while time.time()<end:
        x=m.recv_match(blocking=True,timeout=.2)
        if not x: continue
        update(x)
        if x.get_type()=="COMMAND_ACK" and int(x.command)==int(cmd):
            ack=x; break

    wait_hb(1.5); post=snapshot()
    result=int(ack.result) if ack else -1
    name=ack_name(result) if ack else "NO_ACK"
    progress=getattr(ack,"progress",None) if ack else None
    result_param2=getattr(ack,"result_param2",None) if ack else None

    print(f"    ACK={name} | {pre['state']} -> {post['state']} | "
          f"HB {pre['base_mode']}/{pre['custom_mode']} -> {post['base_mode']}/{post['custom_mode']}")

    ack_w.writerow({
        "timestamp":time.time(),"purpose":purpose,
        "pre_state":pre["state"],"pre_armed":pre["armed"],"pre_landed":pre["landed"],"pre_alt":pre["alt"],
        "pre_base_mode":pre["base_mode"],"pre_custom_mode":pre["custom_mode"],"pre_system_status":pre["system_status"],
        "operation":operation,"command":cmd,**{f"param{i+1}":p[i] for i in range(7)},
        "ack_result":result,"ack_name":name,"ack_progress":progress,"ack_result_param2":result_param2,
        "post_state":post["state"],"post_armed":post["armed"],"post_landed":post["landed"],"post_alt":post["alt"],
        "post_base_mode":post["base_mode"],"post_custom_mode":post["custom_mode"],"post_system_status":post["system_status"]
    })
    ack_f.flush()
    return result

# ========================= COMMANDS =========================
ARM=mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM
TAKEOFF=mavutil.mavlink.MAV_CMD_NAV_TAKEOFF
LAND=mavutil.mavlink.MAV_CMD_NAV_LAND
WAYPOINT=mavutil.mavlink.MAV_CMD_NAV_WAYPOINT
RTL=mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH
REPOSITION=getattr(mavutil.mavlink,"MAV_CMD_DO_REPOSITION",192)

# ========================= SETUP STATES =========================
# IMPORTANT:
# setup uses FORCE=21196.
# purpose="setup" rows are NOT used to learn automaton rules.


def ensure_DG():
    pump(.5)

    if S["alt"]>.3 or S["landed"] in {AIR,TAKING_OFF,LANDING}:
        print("[RESET] Landing...")
        send_long(LAND,[0]*7,"SETUP_LAND","setup")
        if not wait_for(lambda:S["landed"]==GROUND or S["alt"]<.15,30):
            raise RuntimeError(f"Could not land: armed={S['armed']} alt={S['alt']:.2f}")

    if S["armed"]:
        print("[RESET] Disarming...")
        send_long(ARM,[0,FORCE,0,0,0,0,0],"SETUP_DISARM","setup")
        wait_for(lambda:not S["armed"],10)

    if not wait_for(lambda:not S["armed"] and S["alt"]<.3,10):
        raise RuntimeError(f"Could not reach DG: armed={S['armed']} alt={S['alt']:.2f}")

    # Reset PX4 to AUTO LOITER / HOLD
    LOITER=(4<<16)|(3<<24)

    m.mav.set_mode_send(
        SYS,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        LOITER
    )

    if not wait_for(lambda:S["custom_mode"]==LOITER,5):
        print(f"[WARN] LOITER reset not confirmed: custom={S['custom_mode']}")

    print(f"[STATE] DG ready | LOITER | custom={S['custom_mode']}")
# def ensure_AG():
#     ensure_DG()
#     print("[SETUP] Force arming...")

#     send_long(ARM,[1,FORCE,0,0,0,0,0],"SETUP_ARM","setup")

#     if not wait_for(lambda:S["armed"] and S["alt"]<.3,10):
#         raise RuntimeError(f"Could not reach AG: armed={S['armed']} alt={S['alt']:.2f}")

#     print("[STATE] AG ready")

def ensure_AG():
    ensure_DG()

    # Reset test-created AUTO modes before arming
    m.mav.set_mode_send(
        SYS,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        3 << 16      # PX4 POSCTL
    )
    pump(2)

    print(f"[SETUP] Mode reset | base={S['base_mode']} custom={S['custom_mode']}")

    if not PX4.startswith("udpin:127.0.0.1:"):
        raise RuntimeError("Internal force-arm is restricted to local PX4 SITL")

    if not os.path.isfile(PX4_COMMANDER):
        raise RuntimeError(
            f"PX4 SITL commander not found: {PX4_COMMANDER}. "
            "Set PX4_COMMANDER to the px4-commander executable."
        )

    # PX4 deliberately still runs preflight checks for externally sourced
    # MAVLink arm commands, even when param2 is FORCE. Setup transitions are
    # excluded from the automaton, so use the local SITL command interface.
    for attempt in range(1,4):
        print(f"[SETUP] Internal force-arm attempt {attempt}/3")
        try:
            result=subprocess.run(
                [PX4_COMMANDER,"arm","-f"],
                capture_output=True,text=True,timeout=5,check=False
            )
        except (OSError,subprocess.TimeoutExpired) as exc:
            print(f"[WARN] Internal force-arm failed to run: {exc}")
            pump(1)
            continue

        if result.returncode!=0:
            detail=(result.stderr or result.stdout).strip()
            print(f"[WARN] Internal force-arm exit={result.returncode}: {detail}")

        if wait_for(lambda:S["armed"],4):
            print("[STATE] AG ready")
            return

        pump(2)

    raise RuntimeError(
        f"Could not reach AG | armed={S['armed']} alt={S['alt']:.2f} "
        f"base={S['base_mode']} custom={S['custom_mode']} status={S['system_status']}"
    )

def ensure_AIR():
    ensure_AG()

    print("[SETUP] Taking off...")
    ack=send_long(TAKEOFF,takeoff_params(3),"SETUP_TAKEOFF","setup")

    if ack!=mavutil.mavlink.MAV_RESULT_ACCEPTED:
        raise RuntimeError(f"Setup TAKEOFF rejected: {ack_name(ack)}")

    print("[WAIT] Waiting until altitude > 1 m...")

    if not wait_for(lambda:S["armed"] and S["alt"]>1.0,30):
        raise RuntimeError(f"Could not reach AIR: armed={S['armed']} alt={S['alt']:.2f}")

    print(f"[STATE] AIR ready | alt={S['alt']:.2f} m")

# ========================= POSITION =========================
def target_altitude_amsl(relative_m):
    pump(.5)
    if S["amsl_alt"] is None:
        raise RuntimeError("No GLOBAL_POSITION_INT altitude available")
    home_alt_amsl=S["amsl_alt"]-S["alt"]
    return home_alt_amsl+relative_m

def takeoff_params(relative_m):
    # MAV_CMD_NAV_TAKEOFF param7 is AMSL, not height above takeoff. NaN
    # latitude/longitude tells PX4 to use the current horizontal position.
    nan=float("nan")
    return [0,0,0,nan,nan,nan,target_altitude_amsl(relative_m)]

def position_params():
    pump(.5)
    return [0,0,0,float("nan"),(S["lat"]+900)/1e7,S["lon"]/1e7,
            target_altitude_amsl(max(S["alt"],3.0))]

# ========================= TESTS =========================
print("\n"+"="*60)
print(" PX4 ACK / HEARTBEAT AUTOMATON CAPTURE")
print("="*60)

# ---------- DISARMED GROUND ----------
DG_TESTS=[
    ("DISARM",ARM,[0,0,0,0,0,0,0]),
    ("TAKEOFF",TAKEOFF,[0,0,0,0,0,0,10]),
    ("LAND",LAND,[0]*7),
    ("WAYPOINT",WAYPOINT,None),
    ("REPOSITION",REPOSITION,None),
    ("RTL",RTL,[0]*7),
    ("UNSUPPORTED",31000,[0]*7)
]

for name,cmd,p in DG_TESTS:
    ensure_DG()
    if name=="TAKEOFF": p=takeoff_params(10)
    elif name in {"WAYPOINT","REPOSITION"}: p=position_params()
    send_long(cmd,p,name,"test")

# ---------- ARMED GROUND ----------
ensure_AG()
send_long(ARM,[1,0,0,0,0,0,0],"ARM_AGAIN","test")

ensure_AG()
send_long(ARM,[0,0,0,0,0,0,0],"DISARM","test")

# ---------- TAKEOFF PARAMETER TEST ----------
for target_alt in [.2,.5,1.0,10.0]:
    ensure_AG()
    ack=send_long(TAKEOFF,takeoff_params(target_alt),f"TAKEOFF_{target_alt}M","test")

    if ack==mavutil.mavlink.MAV_RESULT_ACCEPTED:
        print("[WAIT] TAKEOFF accepted; observing execution...")
        wait_for(lambda:S["alt"]>.3 or S["landed"]==AIR,15)

    ensure_DG()

# ---------- AIRBORNE ----------
AIR_TESTS=[
    ("DISARM",ARM,[0,0,0,0,0,0,0]),
    ("TAKEOFF_AGAIN",TAKEOFF,[0,0,0,0,0,0,10]),
    ("WAYPOINT",WAYPOINT,None),
    ("REPOSITION",REPOSITION,None),
    ("RTL",RTL,[0]*7),
    ("LAND",LAND,[0]*7)
]

for name,cmd,p in AIR_TESTS:
    ensure_AIR()
    if name in {"WAYPOINT","REPOSITION"}: p=position_params()
    send_long(cmd,p,name,"test")
    pump(2)

# ========================= CLEANUP =========================
ensure_DG()
ack_f.close(); hb_f.close()

print("\n"+"="*60)
print(" CAPTURE COMPLETE")
print("="*60)
print(f"ACK data       : {ACK_OUT}")
print(f"Heartbeat data : {HB_OUT}")
print("\nUse only purpose=='test' rows to generate the ACK automaton.")
