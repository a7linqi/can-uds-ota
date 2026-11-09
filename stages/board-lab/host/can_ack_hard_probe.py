"""Hard ACK probe: many frames + error frames + echo, read status via DLL.

Uses the *verified* PCAN_BAUD_500K constant (0x1C), not the bogus 0x001C0000
that caused a whole round of false BUSOFF readings on 2026-11-07.
"""
import ctypes
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
from can.interfaces.pcan import basic as B

def cval(obj):
    """python-can pcan constants are plain ints in some versions, IntEnum in others."""
    return int(getattr(obj, "value", obj))


CHANNEL = cval(B.PCAN_USBBUS1)
BAUD = cval(B.PCAN_BAUD_500K)
dll = ctypes.WinDLL("PCANBasic.dll")

TPCANMsg = B.TPCANMsg
TPCANTimestamp = B.TPCANTimestamp


def init():
    r = dll.CAN_Initialize(CHANNEL, BAUD, 0, 0, 0, 0) & 0xFFFFFFFF
    print("CAN_Initialize ->", hex(r), "baud =", hex(BAUD))
    if r:
        raise SystemExit("init failed")
    for name, val in (("ALLOW_ERROR_FRAMES", 1), ("ALLOW_ECHO_FRAMES", 1)):
        key = cval(getattr(B, "PCAN_" + name))
        byte_val = ctypes.c_ubyte(val)
        rr = dll.CAN_SetValue(CHANNEL, key, ctypes.byref(byte_val), 1) & 0xFFFFFFFF
        print("SetValue", name, "->", hex(rr))


def drain(tag):
    rx = 0
    echo = 0
    err = 0
    for _ in range(200):
        msg = TPCANMsg()
        ts = TPCANTimestamp()
        r = dll.CAN_Read(CHANNEL, ctypes.byref(msg), ctypes.byref(ts)) & 0xFFFFFFFF
        if r == cval(B.PCAN_ERROR_QRCVEMPTY):
            break
        if r:
            print("  CAN_Read ->", hex(r))
            break
        mtype = msg.MSGTYPE
        if mtype & cval(B.PCAN_MESSAGE_ERRFRAME):
            err += 1
        elif mtype & cval(B.PCAN_MESSAGE_ECHO):
            echo += 1
        else:
            rx += 1
            print("  RX 0x%03X" % msg.ID, bytes(msg.DATA[:msg.LEN]).hex(" ").upper())
    print("%s: rx=%d echo=%d errframe=%d" % (tag, rx, echo, err))
    st = dll.CAN_GetStatus(CHANNEL) & 0xFFFFFFFF
    print("%s: CAN_GetStatus = %s" % (tag, hex(st)))
    return rx, echo, err, st


def main():
    init()
    # 50 single-frame UDS requests, 20 ms apart, drain after every 10
    payload = bytes([0x03, 0x22, 0xF1, 0x90, 0, 0, 0, 0])
    m = TPCANMsg()
    m.ID = 0x7E0
    m.MSGTYPE = cval(B.PCAN_MESSAGE_STANDARD)
    m.LEN = 8
    for i in range(8):
        m.DATA[i] = payload[i]
    total_rx = 0
    worst = 0
    for i in range(50):
        r = dll.CAN_Write(CHANNEL, ctypes.byref(m)) & 0xFFFFFFFF
        if r:
            print("frame %d CAN_Write -> %s" % (i, hex(r)))
            worst = max(worst, r)
        time.sleep(0.02)
        if (i + 1) % 10 == 0:
            rx, _, _, st = drain("after %d frames" % (i + 1))
            total_rx += rx
            worst = max(worst, st)
    print("=== total UDS responses: %d ; worst status: %s ===" % (total_rx, hex(worst)))
    dll.CAN_Uninitialize(CHANNEL)


if __name__ == "__main__":
    main()
