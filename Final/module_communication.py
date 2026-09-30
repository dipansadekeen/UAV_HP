# module_communication.py
import socket
from pymavlink import mavutil


class UDPConnection:
    def __init__(
        self,
        listen_ip="127.0.0.1",
        listen_port=14551,
        gcs_ip="127.0.0.1",
        gcs_port=14550,
        timeout=0.2,
        system_id=1,
        component_id=1,
    ):
        self.listen_ip = listen_ip
        self.listen_port = listen_port

        # ---------------------------
        # UDP
        # ---------------------------
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((listen_ip, listen_port))
        self.sock.settimeout(timeout)

        self.gcs_addr = (
            (gcs_ip, gcs_port)
            if gcs_ip is not None
            else None
        )

        # ---------------------------
        # MAVLink encoder / decoder
        # ---------------------------
        self.mav_out = mavutil.mavlink.MAVLink(None)
        self.mav_out.srcSystem = system_id
        self.mav_out.srcComponent = component_id

        self.mav_in = mavutil.mavlink.MAVLink(None)

        print(
            f"[CONNECTION] Listening on "
            f"{self.listen_ip}:{self.listen_port}",
            flush=True,
        )

    # ---------------------------
    # Raw UDP
    # ---------------------------
    def send(self, data):
        if self.gcs_addr is None:
            return False

        self.sock.sendto(data, self.gcs_addr)
        return True

    def receive(self, buffer_size=4096):
        try:
            return self.sock.recvfrom(buffer_size)
        except socket.timeout:
            return None, None

    def set_gcs_addr(self, addr):
        if self.gcs_addr is None:
            self.gcs_addr = addr
            print(
                f"[GCS CONNECTED] {self.gcs_addr}",
                flush=True,
            )

    # ---------------------------
    # MAVLink
    # ---------------------------
    def send_mav(self, msg):
        if self.gcs_addr is None:
            return False

        pkt = msg.pack(self.mav_out)
        self.sock.sendto(pkt, self.gcs_addr)
        return True

    def parse_mav(self, data):
        messages = []

        if not data:
            return messages

        for byte in data:
            msg = self.mav_in.parse_char(bytes([byte]))

            if msg:
                messages.append(msg)

        return messages

    def receive_mav(self, buffer_size=4096):
        data, addr = self.receive(buffer_size)

        if data is None:
            return [], None

        self.set_gcs_addr(addr)

        messages = self.parse_mav(data)

        return messages, addr

    def close(self):
        self.sock.close()