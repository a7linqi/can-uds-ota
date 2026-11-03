from .transport import Frame

class ProtocolError(RuntimeError):
    pass

class TransportTimeout(TimeoutError):
    pass

class IsoTpClient:
    """Classic CAN, normal 11-bit addressing, one outstanding request."""
    def __init__(self,transport,tx_id=0x7E0,rx_id=0x7E8,max_payload=2048):
        self.transport=transport
        self.tx_id=tx_id
        self.rx_id=rx_id
        self.max_payload=max_payload
    def _budget(self,timeout,deadline):
        remaining=timeout if deadline is None else min(timeout,deadline-self.transport.now())
        if remaining<=0:
            raise TransportTimeout("ISO-TP operation deadline exceeded")
        return remaining
    def _send(self,data,deadline=None):
        timeout=self._budget(1,deadline)
        self.transport.send(Frame(self.tx_id,bytes(data)),timeout=timeout)
        if deadline is not None and self.transport.now()>=deadline:
            raise TransportTimeout("ISO-TP send exceeded operation deadline")
    def _recv(self,timeout,deadline=None):
        deadline=self.transport.now()+self._budget(timeout,deadline)
        while True:
            frame=self.transport.recv(max(0,deadline-self.transport.now()))
            if frame is None:
                raise TransportTimeout("CAN/ISO-TP response timeout")
            if self.transport.now()>=deadline:
                raise TransportTimeout("CAN receive exceeded operation deadline")
            if frame.arbitration_id==self.rx_id and not frame.extended:
                return frame.data
            if self.transport.now()>=deadline:
                raise TransportTimeout("no matching ECU frame")
    def _flow(self,timeout,deadline=None):
        deadline=self.transport.now()+self._budget(timeout,deadline)
        waits=0
        while True:
            data=self._recv(max(0,deadline-self.transport.now()))
            if len(data)<3 or data[0]>>4!=3:
                raise ProtocolError("expected ISO-TP flow control")
            status=data[0]&15
            if status==1:
                waits+=1
                if waits>3 or self.transport.now()>=deadline:
                    raise TransportTimeout("too many flow-control WAIT frames")
                continue
            if status!=0:
                raise ProtocolError("flow control rejected the transfer")
            st=data[2]
            if st<=0x7F: interval=st/1000
            elif 0xF1<=st<=0xF9: interval=(st-0xF0)/10000
            else: raise ProtocolError("reserved STmin value")
            return data[1],interval
    def send(self,payload,timeout=1,deadline=None):
        payload=bytes(payload)
        if not 1<=len(payload)<=min(self.max_payload,4095):
            raise ValueError("ISO-TP payload length out of range")
        if len(payload)<=7:
            self._send(bytes([len(payload)])+payload,deadline)
            return
        self._send(bytes([0x10|(len(payload)>>8),len(payload)&255])+payload[:6],deadline)
        bs,interval=self._flow(timeout,deadline)
        offset,sn,count=6,1,0
        while offset<len(payload):
            if interval:
                budget=self._budget(interval,deadline)
                self.transport.wait(budget)
                if budget<interval or (deadline is not None and self.transport.now()>=deadline):
                    raise TransportTimeout("STmin exceeded operation deadline")
            part=payload[offset:offset+7]
            self._send(bytes([0x20|sn])+part,deadline)
            offset+=len(part);sn=(sn+1)&15;count+=1
            if bs and count>=bs and offset<len(payload):
                bs,interval=self._flow(timeout,deadline);count=0
    def receive(self,timeout=1,deadline=None):
        data=self._recv(timeout,deadline)
        pci=data[0]>>4
        if pci==0:
            length=data[0]&15
            if not 1<=length<=7 or length>len(data)-1:
                raise ProtocolError("malformed single frame")
            return data[1:1+length]
        if pci!=1 or len(data)<3:
            raise ProtocolError("expected single/first response frame")
        length=((data[0]&15)<<8)|data[1]
        if not 8<=length<=self.max_payload:
            self._send(b"\x32\x00\x00",deadline)
            raise ProtocolError("response exceeds client buffer")
        result=bytearray(data[2:])
        if len(result)>=length:
            raise ProtocolError("invalid first-frame length")
        self._send(b"\x30\x00\x00",deadline)
        sn=1
        while len(result)<length:
            data=self._recv(1,deadline)
            if len(data)<2 or data[0]>>4!=2 or data[0]&15!=sn:
                raise ProtocolError("wrong/malformed consecutive response frame")
            result.extend(data[1:1+min(7,length-len(result))])
            sn=(sn+1)&15
        return bytes(result)
