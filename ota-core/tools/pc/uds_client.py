import hashlib
import math
import struct
import zlib
from .isotp_client import ProtocolError,TransportTimeout

APP=0x08010000
MAGIC=0x32474D49
IMAGE_FLAG_SIGNED=0x00000001
SIGNATURE_SIZE=64
class NegativeResponse(RuntimeError):
    def __init__(self,sid,nrc):
        self.sid,self.nrc=sid,nrc
        super().__init__(f"UDS {sid:02X} rejected with NRC {nrc:02X}")
class UnknownCommit(RuntimeError):
    pass
class UnknownReset(RuntimeError):
    pass

def inspect_package(data,flash_kib=1024):
    if flash_kib not in (512,1024) or len(data)<40:
        raise ValueError("invalid capacity/package length")
    magic,version,size,crc,address,flags,reserved,hcrc=struct.unpack("<8I",data[:32])
    if magic!=MAGIC or version==0 or address!=APP or (flags&~IMAGE_FLAG_SIGNED) or reserved:
        raise ValueError("unsupported IMG2 header")
    signed=bool(flags&IMAGE_FLAG_SIGNED)
    if size<8 or size>flash_kib*1024-0x10000 or len(data)!=32+size+(SIGNATURE_SIZE if signed else 0):
        raise ValueError("package size does not match its header/target capacity")
    if zlib.crc32(data[:28])!=hcrc or zlib.crc32(data[32:32+size])!=crc:
        raise ValueError("package CRC32 mismatch")
    stack,reset=struct.unpack_from("<II",data,32)
    ram=0x20000000<stack<=0x20020000 or 0x10000000<stack<=0x10010000
    if not ram or stack&7 or not reset&1 or not APP+8<=reset&~1<APP+size:
        raise ValueError("invalid app vector table")
    return dict(version=version,payload_bytes=size,package_bytes=len(data),load_address=hex(APP),
                payload_crc32=f"{crc:08X}",package_sha256=hashlib.sha256(data).hexdigest(),
                signature_verified=False,signed=signed)

def demo_key(seed):
    value=seed^0xA5A5A5A5
    value=((value<<7)|(value>>25))&0xFFFFFFFF
    return (value+0x9E3779B9)&0xFFFFFFFF

class UdsClient:
    def __init__(self,link,logger=None,total_timeout=120):
        if not math.isfinite(total_timeout) or total_timeout<=0:
            raise ValueError("positive total timeout required")
        self.link=link
        self.log=logger or (lambda **event:None)
        self.p2=1.0
        self.p2_star=5.0
        self.total_timeout=total_timeout
    def request(self,request):
        request=bytes(request)
        if not request: raise ValueError("empty request")
        sid=request[0]
        deadline=self.link.transport.now()+self.total_timeout
        self.log(event="request_started",sid=f"{sid:02X}",bytes=len(request))
        self.link.send(request,deadline=deadline)
        timeout=self.p2
        pending=0
        while True:
            remaining=deadline-self.link.transport.now()
            if remaining<=0: raise TransportTimeout("UDS total operation timeout")
            response=self.link.receive(min(timeout,remaining),deadline=deadline)
            if response[:1]==b"\x7F":
                if len(response)!=3 or response[1]!=sid:
                    raise ProtocolError("mismatched negative response")
                if response[2]==0x78:
                    pending+=1
                    if pending>1000: raise ProtocolError("too many response-pending messages")
                    self.log(event="pending",sid=f"{sid:02X}",count=pending)
                    timeout=self.p2_star
                    continue
                raise NegativeResponse(sid,response[2])
            if not response or response[0]!=((sid+0x40)&255):
                raise ProtocolError("mismatched positive response")
            return response
    def read_did(self,did):
        response=self.request(b"\x22"+int(did).to_bytes(2,"big"))
        if len(response)<3 or response[1:3]!=int(did).to_bytes(2,"big"):
            raise ProtocolError("DID echo mismatch")
        return response[3:]
    def info(self):
        vin=self.read_did(0xF190)
        version=self.read_did(0xF195)
        session=self.read_did(0xF186)
        if len(vin)!=17 or not 1<=len(version)<=32 or len(session)!=1 or session[0] not in (1,2,3):
            raise ProtocolError("malformed identification/session DID")
        try:
            return dict(vin=vin.decode("ascii"),bootloader_demo_version=version.decode("ascii"),session=session[0])
        except UnicodeDecodeError as error:
            raise ProtocolError("non-ASCII identification DID") from error
    def status(self):
        data=self.read_did(0x0200)
        if len(data)!=40 or data[0]!=1 or data[1]>1 or data[2]>5 or data[3]>3:
            raise ProtocolError("unsupported/malformed update-status record")
        names=("active_version","pending_version","min_version","active_size","pending_size",
               "active_crc32","pending_crc32","metadata_sequence","flash_bytes")
        result=dict(zip(names,struct.unpack(">9I",data[4:])))
        if result["flash_bytes"] not in (512*1024,1024*1024):
            raise ProtocolError("unsupported ECU capacity in status record")
        result.update(metadata_valid=bool(data[1]),state=data[2],boot_attempts=data[3],
                      state_name=("IDLE","DOWNLOADING","READY","INSTALLING","TRIAL","RESTORING")[data[2]],
                      metadata_only=True)
        return result
    def programming(self):
        response=self.request(b"\x10\x02")
        if len(response)!=6 or response[1]!=2:
            raise ProtocolError("invalid programming-session response")
        # ISO 14229: P2 resolution 1 ms; P2* resolution 10 ms.
        p2=int.from_bytes(response[2:4],"big")/1000
        star=int.from_bytes(response[4:6],"big")/100
        if not 0<p2<=65.535 or not 0<star<=655.35:
            raise ProtocolError("invalid session timing values")
        self.p2,self.p2_star=p2,star
        seed_response=self.request(b"\x27\x01")
        if len(seed_response)!=6 or seed_response[1]!=1:
            raise ProtocolError("invalid seed response")
        seed=int.from_bytes(seed_response[2:],"big")
        if seed:
            response=self.request(b"\x27\x02"+demo_key(seed).to_bytes(4,"big"))
            if response!=b"\x67\x02": raise ProtocolError("invalid unlock response")
        self.log(event="programming_unlocked",p2_s=self.p2,p2_star_s=self.p2_star,
                 security="demo_seed_key")
    def _verify_staged(self):
        verified=self.request(b"\x31\x01\x00\x01")
        if verified!=b"\x71\x01\x00\x01\x00":
            raise ProtocolError("staged-image verification failed")
    def _reset(self):
        try:
            response=self.request(b"\x11\x01")
        except TransportTimeout as error:
            self.log(event="reset_uncertain")
            raise UnknownReset("ECU-reset acknowledgement lost: reset state unknown. "
                               "Do not repeat automatically; inspect update status first.") from error
        if response!=b"\x51\x01": raise ProtocolError("invalid ECU-reset response")
        self.log(event="reset_requested")
    def install_staged(self,package,flash_kib=1024):
        metadata=inspect_package(package,flash_kib)
        self.log(event="package_preflight",**metadata)
        state=self.status()
        if state["flash_bytes"]!=flash_kib*1024:
            raise ValueError("ECU capacity differs from --flash-kib")
        expected=(metadata["version"],metadata["payload_bytes"],int(metadata["payload_crc32"],16))
        actual=(state["pending_version"],state["pending_size"],state["pending_crc32"])
        if not state["metadata_valid"] or state["state_name"]!="READY" or actual!=expected:
            raise ValueError("READY package version/length/CRC does not match the local image")
        self.log(event="matching_ready_package",metadata_sequence=state["metadata_sequence"])
        self.programming()
        self._verify_staged()
        self._reset()
        return dict(**metadata,operation="install_staged",downloaded_bytes=0,
                    reset_requested=True,application_health_confirmed=False)
    def flash(self,package,flash_kib=1024,reset=True,retries=1):
        if not isinstance(retries,int) or not 0<=retries<=3:
            raise ValueError("TransferData retries must be 0..3")
        metadata=inspect_package(package,flash_kib)
        self.log(event="package_preflight",**metadata)
        self.programming()
        response=self.request(b"\x34\x00\x44"+APP.to_bytes(4,"big")+len(package).to_bytes(4,"big"))
        if len(response)<3 or response[1]&15 or not 1<=response[1]>>4<=4:
            raise ProtocolError("invalid maxNumberOfBlockLength encoding")
        width=response[1]>>4
        if len(response)!=2+width:
            raise ProtocolError("invalid download response length")
        maximum=int.from_bytes(response[2:],"big")
        if not 3<=maximum<=self.link.max_payload:
            raise ProtocolError("unsafe TransferData block length")
        block_data=maximum-2
        blocks=0
        for offset in range(0,len(package),block_data):
            counter=(blocks+1)&255
            request=b"\x36"+bytes([counter])+package[offset:offset+block_data]
            for attempt in range(retries+1):
                try:
                    answer=self.request(request)
                    break
                except TransportTimeout:
                    if attempt==retries: raise
                    self.log(event="block_retry",counter=counter,offset=offset,attempt=attempt+1)
            if answer!=b"\x76"+bytes([counter]):
                raise ProtocolError("TransferData acknowledgement mismatch")
            blocks+=1
            self.log(event="block_accepted",counter=counter,offset=offset,bytes=len(request)-2)
        try:
            answer=self.request(b"\x37")
        except TransportTimeout as error:
            self.log(event="commit_uncertain")
            # A lost 0x77 may mean READY is already durable. Never erase/restart
            # or pretend the update failed safely just because its ACK was lost.
            raise UnknownCommit("0x37 timed out: commit state unknown; automatic flashing stopped. "
                                "Read update status (DID 0x0200) before choosing recovery.") from error
        if answer!=b"\x77": raise ProtocolError("invalid transfer-exit response")
        self._verify_staged()
        self.log(event="download_committed",blocks=blocks)
        if reset:
            self._reset()
        return dict(**metadata,blocks=blocks,reset_requested=reset,
                    application_health_confirmed=False)
