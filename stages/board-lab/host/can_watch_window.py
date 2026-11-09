"""Observe the bus for a while: passive listen + periodic 0x22 probes.

Distinguishes "board never starts" from "board busy in boot_select / recovery
and starts answering later".
"""
import ctypes
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
from can.interfaces.pcan import basic as B


def cval(o):
    return int(getattr(o, "value", o))


CH = cval(B.PCAN_USBBUS1)
BAUD = cval(B.PCAN_BAUD_500K)
dll = ctypes.WinDLL("PCANBasic.dll")

r = dll.CAN_Initialize(CH, BAUD, 0, 0, 0, 0) & 0xFFFFFFFF
print("CAN_Initialize ->", hex(r), "baud", hex(BAUD))
if r:
    raise SystemExit(1)
for name in ("PCAN_ALLOW_ERROR_FRAMES", "PCAN_ALLOW_ECHO_FRAMES"):
    v = ctypes.c_ubyte(1)
    print("SetValue", name, hex(dll.CAN_SetValue(CH, cval(getattr(B, name)), ctypes.byref(v), 1) & 0xFFFFFFFF))

req = B.TPCANMsg()
req.ID = 0x7E0
req.MSGTYPE = cval(B.PCAN_MESSAGE_STANDARD)
req.LEN = 8
for i, b in enumerate([0x03, 0x22, 0xF1, 0x90, 0, 0, 0, 0]):
    req.DATA[i] = b

ECHO = cval(B.PCAN_MESSAGE_ECHO)
ERRF = cval(B.PCAN_MESSAGE_ERRFRAME)
QEMPTY = cval(B.PCAN_ERROR_QRCVEMPTY)

start = time.time()
last_probe = 0.0
responses = 0
errors = 0
while time.time() - start < 45:
    now = time.time()
    if now - last_probe >= 5.0:
        last_probe = now
        wr = dll.CAN_Write(CH, ctypes.byref(req)) & 0xFFFFFFFF
        print("t=%5.1fs TX 0x7E0 22 F190 -> write %s" % (now - start, hex(wr)), flush=True)
    for _ in range(100):
        msg = B.TPCANMsg()
        ts = B.TPCANTimestamp()
        rr = dll.CAN_Read(CH, ctypes.byref(msg), ctypes.byref(ts)) & 0xFFFFFFFF
        if rr == QEMPTY:
            break
        if rr:
            print("  CAN_Read err", hex(rr), flush=True)
            break
        if msg.MSGTYPE & ECHO:
            continue
        if msg.MSGTYPE & ERRF:
            errors += 1
            print("  t=%5.1f ERROR FRAME" % (time.time() - start), flush=True)
            continue
        responses += 1
        print("  t=%5.1f RX 0x%03X %s" % (time.time() - start, msg.ID,
              bytes(msg.DATA[:msg.LEN]).hex(" ").upper()), flush=True)
    st = dll.CAN_GetStatus(CH) & 0xFFFFFFFF
    if st:
        print("  t=%5.1f status=%s" % (time.time() - start, hex(st)), flush=True)
    time.sleep(0.05)

print("=== done: responses=%d error_frames=%d final_status=%s ===" %
      (responses, errors, hex(dll.CAN_GetStatus(CH) & 0xFFFFFFFF)))
dll.CAN_Uninitialize(CH)
