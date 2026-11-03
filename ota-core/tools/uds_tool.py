"""PC tester for the C ECU or an explicitly selected python-can adapter."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import uuid
from pc.transport import ROOT,SimTransport,PythonCanTransport,TransportError
from pc.isotp_client import IsoTpClient,ProtocolError
from pc.uds_client import UdsClient,NegativeResponse,UnknownCommit,UnknownReset,inspect_package

def main():
    parser=argparse.ArgumentParser()
    actions=parser.add_subparsers(dest="action",required=True)
    inspect=actions.add_parser("inspect",help="validate an IMG2 without opening any CAN backend")
    inspect.add_argument("image",type=Path)
    inspect.add_argument("--flash-kib",type=int,choices=(512,1024),default=1024)
    for name in ("info","status","flash","install"):
        action=actions.add_parser(name)
        if name in ("flash","install"):
            action.add_argument("image",type=Path)
            action.add_argument("--log",type=Path)
        if name=="flash":
            action.add_argument("--no-reset",action="store_true")
            action.add_argument("--retries",type=int,choices=range(4),default=1)
        action.add_argument("--backend",choices=("sim","can"),default="sim")
        action.add_argument("--cc",help="host C compiler for the simulator")
        action.add_argument("--ecu-bin",type=Path,help="optional prebuilt C simulator")
        action.add_argument("--flash-kib",type=int,choices=(512,1024),default=1024)
        action.add_argument("--interface")
        action.add_argument("--channel")
        action.add_argument("--index",type=int)
        action.add_argument("--bitrate",type=int,default=500000)
        action.add_argument("--request-timeout",type=float,default=120,
                            help="overall deadline per UDS request, including pending/ISO-TP")
    args=parser.parse_args()
    transport=None
    log_file=None
    try:
        if args.action=="inspect":
            print(json.dumps(inspect_package(args.image.read_bytes(),args.flash_kib),indent=2))
            return 0
        # Validate BEFORE opening hardware or sending a session/unlock request.
        package=None
        if args.action in ("flash","install"):
            package=args.image.read_bytes()
            inspect_package(package,args.flash_kib)
            path=args.log or ROOT/"runs"/(datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8]+".jsonl")
            if path.resolve()==args.image.resolve():
                raise ValueError("event log must not overwrite the image")
            path.parent.mkdir(parents=True,exist_ok=True)
            log_file=path.open("w",encoding="utf-8")
        def log(**event):
            if log_file:
                event["at"]=datetime.now().astimezone().isoformat()
                log_file.write(json.dumps(event)+"\n");log_file.flush()
        if args.backend=="can":
            if not args.interface or args.channel is None:
                parser.error("real CAN requires explicit --interface and --channel")
            if args.cc or args.ecu_bin:
                parser.error("--cc/--ecu-bin apply only to the simulator")
            transport=PythonCanTransport(args.interface,args.channel,args.bitrate,args.index)
        else:
            if args.interface or args.channel is not None:
                parser.error("--interface/--channel apply only to real CAN")
            transport=SimTransport(args.cc,args.flash_kib,args.ecu_bin)
        client=UdsClient(IsoTpClient(transport),log,total_timeout=args.request_timeout)
        if args.action=="info": result=client.info()
        elif args.action=="status": result=client.status()
        elif args.action=="install": result=client.install_staged(package,args.flash_kib)
        else: result=client.flash(package,args.flash_kib,reset=not args.no_reset,retries=args.retries)
        result["backend"]="C simulator" if args.backend=="sim" else "python-can"
        if args.backend=="sim":
            result["simulation_state"]=transport.get_state()
        if log_file:
            log(event="completed",**result)
            result["logfile"]=str(path.resolve())
        print(json.dumps(result,indent=2))
        return 0
    except (OSError,ValueError,TransportError,ProtocolError,NegativeResponse,UnknownCommit,UnknownReset,TimeoutError) as error:
        if log_file:
            log_file.write(json.dumps(dict(event="failed",error=str(error)))+"\n")
            log_file.flush()
        print(json.dumps(dict(error=str(error))),file=sys.stderr)
        return 1
    finally:
        try:
            if transport: transport.close()
        except TransportError as error:
            print(json.dumps(dict(close_error=str(error))),file=sys.stderr)
        finally:
            if log_file: log_file.close()
if __name__=="__main__":
    raise SystemExit(main())
