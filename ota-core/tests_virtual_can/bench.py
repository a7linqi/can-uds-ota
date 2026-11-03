"""Test-only bridge: real python-can VirtualBus <-> actual C ECU process."""
from concurrent.futures import Future
import queue
import threading
import time
import uuid
import can
from pc.transport import Frame,PythonCanTransport,SimTransport,TransportError

class VirtualBench:
    def __init__(self,compiler,flash_kib=1024):
        self.stop=threading.Event();self.commands=queue.Queue();self.errors=[]
        self.channel="ota-test-"+uuid.uuid4().hex
        self.sim=SimTransport(compiler,flash_kib)
        self.ecu_bus=can.Bus(interface="virtual",channel=self.channel,ignore_config=True,
            receive_own_messages=False,can_filters=[dict(can_id=0x7E0,can_mask=0x7FF,extended=False)])
        self.transport=PythonCanTransport("virtual",self.channel,can_module=can)
        self.worker=threading.Thread(target=self._run,daemon=True)
        self.worker.start()
    def _run(self):
        started=time.monotonic()
        active=None
        try:
            while not self.stop.is_set():
                try:
                    name,args,active=self.commands.get_nowait()
                except queue.Empty:
                    pass
                else:
                    active.set_result(getattr(self.sim,name)(*args));active=None
                advance=time.monotonic()-started-self.sim.now()
                if advance>0: self.sim.wait(advance)
                message=self.ecu_bus.recv(0.002)
                if message is not None and not (message.is_error_frame or message.is_remote_frame or
                        message.is_extended_id or message.is_fd):
                    self.sim.send(Frame(message.arbitration_id,bytes(message.data)))
                while self.sim.frames:
                    frame=self.sim.recv(0)
                    self.ecu_bus.send(can.Message(arbitration_id=frame.arbitration_id,data=frame.data,
                        is_extended_id=frame.extended,check=True),timeout=1)
        except Exception as error:
            self.errors.append(str(error));self.stop.set()
            if active and not active.done(): active.set_exception(error)
        finally:
            while not self.commands.empty():
                _,_,future=self.commands.get_nowait()
                future.set_exception(TransportError("C bridge stopped"))
    def control(self,name,*args):
        if self.stop.is_set(): raise TransportError("C bridge stopped: "+"; ".join(self.errors))
        future=Future();self.commands.put((name,args,future))
        return future.result(timeout=10)
    def close(self):
        self.stop.set();self.worker.join(timeout=12)
        if self.worker.is_alive(): raise TransportError("C bridge did not stop")
        try:
            self.transport.close()
        finally:
            self.ecu_bus.shutdown();self.sim.close()
        if self.errors: raise TransportError("; ".join(self.errors))
