from collections import deque
from dataclasses import dataclass
import math
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[2]

class TransportError(RuntimeError):
    pass

@dataclass(frozen=True)
class Frame:
    arbitration_id:int
    data:bytes
    extended:bool=False
    def __post_init__(self):
        object.__setattr__(self,"data",bytes(self.data))
        limit=0x1FFFFFFF if self.extended else 0x7FF
        if not 0<=self.arbitration_id<=limit or not 1<=len(self.data)<=8:
            raise ValueError("classic CAN frame out of range")

class SimTransport:
    """Line protocol drives a child process containing the actual C ECU."""
    def __init__(self,compiler=None,flash_kib=1024,ecu_binary=None):
        if flash_kib not in (512,1024):
            raise ValueError("invalid flash capacity")
        self.frames=deque()
        self.lines=queue.Queue()
        self.errors=deque(maxlen=80)
        self.state=None
        self.now_ms=1
        self.process=None
        self.last_result=None
        self.stats=None
        self.reader_threads=[]
        if ecu_binary:
            command=[str(Path(ecu_binary).resolve())]
        else:
            compiler=compiler or next((x for n in ("tcc","cc","gcc","clang") if (x:=shutil.which(n))),None)
            if not compiler:
                raise TransportError("specify --cc with a working C99 compiler")
            sources=[str(p.relative_to(ROOT)) for pattern in ("src/core/*.c","src/port/host/*.c","third_party/ed25519/*.c")
                     for p in sorted(ROOT.glob(pattern))]
            command=[str(compiler),"-std=c99","-Wall","-Wextra","-Iinclude","-Isrc/port/host",
                     "-Ithird_party/ed25519",
                     f"-DFLASH_TOTAL_SIZE={flash_kib*1024}u",*sources]
            if Path(compiler).stem.lower()=="tcc":
                command+=["-run","sim/ecu_main.c"]
            else:
                out=ROOT/"build"/f"sim-{flash_kib}{'.exe' if sys.platform=='win32' else ''}"
                out.parent.mkdir(parents=True,exist_ok=True)
                build=subprocess.run(command+["sim/ecu_main.c","-o",str(out)],
                                     cwd=ROOT,capture_output=True,text=True,timeout=60)
                if build.returncode:
                    raise TransportError(build.stderr)
                command=[str(out)]
        try:
            options={"creationflags":subprocess.CREATE_NO_WINDOW} if sys.platform=="win32" else {}
            self.process=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE,text=True,bufsize=1,**options)
            for target in (self._read_stdout,self._read_stderr):
                worker=threading.Thread(target=target,daemon=True)
                self.reader_threads.append(worker)
                worker.start()
            self._read_until("READY")
        except Exception:
            self.close()
            raise

    def _read_stdout(self):
        try:
            for line in self.process.stdout:
                self.lines.put(line.strip())
        finally:
            self.lines.put(None)
    def _read_stderr(self):
        for line in self.process.stderr:
            self.errors.append(line.rstrip())
    def _read_until(self,terminator):
        deadline=time.monotonic()+10
        command_error=None
        while True:
            try:
                line=self.lines.get(timeout=max(0.001,deadline-time.monotonic()))
            except queue.Empty:
                raise TransportError("C simulator did not complete a command")
            if line is None:
                raise TransportError("C simulator exited: "+"\n".join(self.errors))
            parts=line.split()
            if not parts:
                continue
            if parts[0]=="CAN":
                self.now_ms=int(parts[1])
                self.frames.append(Frame(int(parts[2],16),bytes.fromhex(parts[4]),bool(int(parts[3]))))
            elif parts[0]=="STATE":
                values=list(map(int,parts[1:]))
                self.state=dict(zip(("target","state","active","pending","attempts","metadata"),values))
            elif parts[0]=="RESULT":
                self.last_result=int(parts[1])
            elif parts[0]=="STATS":
                self.stats=dict(zip(("nor_writes","internal_writes","eeprom_writes"),map(int,parts[1:])))
            elif parts[0]=="ERROR":
                command_error=line
            elif parts[0] in ("DONE","READY","BYE"):
                if parts[0]=="DONE": self.now_ms=int(parts[1])
                if parts[0]==terminator:
                    if command_error: raise TransportError(command_error)
                    return
            else:
                raise TransportError("unexpected simulator output: "+line)
            if time.monotonic()>=deadline:
                raise TransportError("C simulator output exceeded deadline")
    def _exchange(self,command):
        if not self.process or self.process.poll() is not None:
            raise TransportError("simulator is not running")
        self.process.stdin.write(command+"\n")
        self.process.stdin.flush()
        self._read_until("DONE")
    def send(self,frame,timeout=1):
        self._exchange(f"F {self.now_ms} {frame.arbitration_id:X} {int(frame.extended)} {frame.data.hex()}")
    def recv(self,timeout):
        if self.frames:
            return self.frames.popleft()
        if timeout>0:
            self.wait(timeout)
        return self.frames.popleft() if self.frames else None
    def now(self):
        return self.now_ms/1000
    def wait(self,seconds):
        if seconds>0:
            self._exchange(f"T {self.now_ms+max(1,math.ceil(seconds*1000))}")
    def reboot(self):
        self.frames.clear()
        self._exchange("REBOOT")
        return dict(self.state)
    def confirm_app_health(self):
        self._exchange("HEALTH")
        return self.last_result
    def power_cut_after(self,byte_operations):
        if not isinstance(byte_operations,int) or byte_operations < -1:
            raise ValueError("invalid simulator fault budget")
        self._exchange(f"FAULT {byte_operations}")
    def get_state(self):
        self._exchange("STATE")
        return dict(self.state)
    def get_stats(self):
        self._exchange("STATS")
        return dict(self.stats)
    def close(self):
        process=self.process
        if not process:
            return
        if process.poll() is None:
            try:
                process.stdin.write("EXIT\n");process.stdin.flush()
                process.wait(timeout=2)
            except (OSError,subprocess.TimeoutExpired):
                process.terminate()
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired: process.kill();process.wait(timeout=2)
        for worker in self.reader_threads:
            worker.join(timeout=1)
        for stream in (process.stdin,process.stdout,process.stderr):
            if stream: stream.close()
        self.process=None

class PythonCanTransport:
    """Explicit real-CAN backend. No driver installation or autodiscovery."""
    def __init__(self,interface,channel,bitrate=500000,index=None,can_module=None):
        if not interface or channel is None or bitrate<=0:
            raise ValueError("explicit interface/channel/bitrate required")
        if can_module is None:
            try:
                import can as can_module
            except ImportError as error:
                raise TransportError("real CAN requires the optional python-can package") from error
        self.can=can_module
        options=dict(interface=interface,channel=channel,bitrate=bitrate,ignore_config=True,
                     receive_own_messages=False,
                     can_filters=[dict(can_id=0x7E8,can_mask=0x7FF,extended=False)])
        if index is not None: options["index"]=index
        try:
            self.bus=self.can.Bus(**options)
        except self.can.CanError as error:
            raise TransportError(f"cannot open CAN interface: {error}") from error
    def send(self,frame,timeout=1):
        message=self.can.Message(arbitration_id=frame.arbitration_id,data=frame.data,
                                 is_extended_id=frame.extended,check=True)
        try:
            self.bus.send(message,timeout=timeout)
        except self.can.CanError as error:
            raise TransportError(f"CAN send failed: {error}") from error
    def recv(self,timeout):
        deadline=time.monotonic()+timeout
        while True:
            try:
                message=self.bus.recv(max(0,deadline-time.monotonic()))
            except self.can.CanError as error:
                raise TransportError(f"CAN receive failed: {error}") from error
            if message is None:
                return None
            if not (message.is_error_frame or message.is_remote_frame or message.is_extended_id
                    or getattr(message,"is_fd",False)) and message.arbitration_id==0x7E8:
                if 1<=len(message.data)<=8:
                    return Frame(message.arbitration_id,bytes(message.data))
            if time.monotonic()>=deadline: return None
    def now(self): return time.monotonic()
    def wait(self,seconds): time.sleep(max(0,seconds))
    def close(self):
        try:
            self.bus.shutdown()
        except self.can.CanError as error:
            raise TransportError(f"CAN shutdown failed: {error}") from error
