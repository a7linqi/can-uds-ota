"""Lost-response recovery demo with a persistent actual-C simulated ECU."""
import argparse
import json
from pathlib import Path
import struct
from make_image import APP,package,DEMO_SEED
from pc.transport import ROOT,SimTransport
from pc.isotp_client import IsoTpClient
from pc.uds_client import UdsClient,UnknownCommit,UnknownReset

class LoseOneReply:
    def __init__(self,base,sid): self.base=base;self.sid=sid;self.dropped=False
    def __getattr__(self,name): return getattr(self.base,name)
    def recv(self,timeout):
        deadline=self.now()+timeout
        while True:
            frame=self.base.recv(max(0,deadline-self.now()))
            if frame is None: return None
            data=frame.data
            if not self.dropped and len(data)>1 and data[0]>>4==0 and data[1]==self.sid:
                self.dropped=True
                continue
            return frame

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--cc",required=True)
    parser.add_argument("--output",type=Path,default=ROOT/"evidence/pc-recovery.json")
    args=parser.parse_args()
    image=package(struct.pack("<II",0x20020000,APP+9)+b"\xA5"*1495,1,seed=DEMO_SEED)
    scenarios=[]
    for lost_sid in (0x77,0x51):
        transport=SimTransport(args.cc);events=[]
        filtered=LoseOneReply(transport,lost_sid)
        client=UdsClient(IsoTpClient(filtered),lambda **event:events.append(event))
        try:
            expected=UnknownCommit if lost_sid==0x77 else UnknownReset
            try: client.flash(image)
            except expected: pass
            else: raise RuntimeError("expected response loss was not detected")
            before=client.status()
            if lost_sid==0x77:
                assert before["state_name"]=="READY"
                events.clear()
                result=client.install_staged(image)
                assert not any(e.get("sid") in ("34","36","37") for e in events)
                assert result["downloaded_bytes"]==0
            else:
                assert before["state_name"]=="TRIAL" and before["boot_attempts"]==1
                result=dict(reset_not_retried=True,application_health_confirmed=False)
            after=client.status()
            scenarios.append(dict(lost_positive_sid=f"{lost_sid:02X}",before=before,after=after,
                                  result=result,events=events))
            print(f"lost {lost_sid:02X}: {before['state_name']} -> {after['state_name']}, attempts={after['boot_attempts']}")
        finally:
            transport.close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(mode="C ECU with simulated reply loss",
        hardware_verified=False,application_instructions_executed=False,scenarios=scenarios),indent=2)+"\n",encoding="utf-8")
    print("Evidence:",args.output.resolve())
if __name__=="__main__": main()
