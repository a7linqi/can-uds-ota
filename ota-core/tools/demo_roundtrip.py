"""Reproducible update/rollback/power-cut demonstration using the actual C ECU."""
import argparse
import json
from pathlib import Path
import struct
from make_image import APP,package,DEMO_SEED
from pc.transport import ROOT,SimTransport
from pc.isotp_client import IsoTpClient,TransportTimeout
from pc.uds_client import UdsClient

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--cc",required=True)
    parser.add_argument("--output",type=Path,default=ROOT/"evidence/pc-roundtrip.json")
    args=parser.parse_args()
    events=[]
    stages=[]
    def image(version):
        # Synthetic bytes pass vector/CRC checks; they are NOT executable firmware.
        raw=struct.pack("<II",0x20020000,APP+9)+bytes([version])*1495
        return package(raw,version,seed=DEMO_SEED)
    transport=SimTransport(args.cc)
    client=UdsClient(IsoTpClient(transport),lambda **event:events.append(event))
    def record(name):
        state=transport.get_state()
        status=client.status()
        stages.append(dict(stage=name,**state,uds_status=status))
        print(name,json.dumps(status),flush=True)
        return state
    try:
        client.flash(image(1))
        assert transport.confirm_app_health()==0
        assert record("v1_confirmed")["active"]==1
        client.flash(image(2))
        assert record("v2_trial")["state"]==4
        transport.reboot();transport.reboot();transport.reboot()
        assert record("v2_unconfirmed_restored_v1")["active"]==1
        client.flash(image(2))
        assert transport.confirm_app_health()==0
        assert record("v2_confirmed")["active"]==2
        transport.power_cut_after(100)
        try:
            client.flash(image(3))
        except TransportTimeout:
            events.append(dict(event="expected_simulated_power_loss",storage_byte_budget=100))
        else:
            raise RuntimeError("fault injection did not interrupt the download")
        transport.reboot()
        assert record("download_cut_retained_v2")["active"]==2
        client.flash(image(3))
        assert transport.confirm_app_health()==0
        assert record("fresh_download_v3_confirmed")["active"]==3
        result=dict(mode="C ECU simulation",application_instructions_executed=False,
                    health_confirmation="test-fixture control calls C boot_confirm",
                    hardware_verified=False,stages=stages,events=events)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
        print("Evidence:",args.output.resolve())
    finally:
        transport.close()

if __name__=="__main__":
    main()
