import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from make_image import package,APP,DEMO_SEED
from pc.transport import Frame,SimTransport,PythonCanTransport,TransportError
from pc.isotp_client import IsoTpClient,ProtocolError,TransportTimeout
from pc.uds_client import UdsClient,NegativeResponse,UnknownCommit,UnknownReset,inspect_package

def image(version=1,size=1503):
    raw=struct.pack("<II",0x20020000,APP+9)+bytes((i*37+version)&255 for i in range(size-8))
    return package(raw,version,seed=DEMO_SEED)

class Delegated:
    def __init__(self,base): self.base=base
    def __getattr__(self,name): return getattr(self.base,name)

class DropResponse(Delegated):
    def __init__(self,base,sid):
        super().__init__(base); self.sid=sid; self.dropped=0
    def recv(self,timeout):
        end=self.now()+timeout
        while True:
            frame=self.base.recv(max(0,end-self.now()))
            if frame is None: return None
            data=frame.data
            if not self.dropped and len(data)>1 and data[0]>>4==0 and data[1]==self.sid:
                self.dropped+=1
                continue
            return frame

class PendingResponse(Delegated):
    def __init__(self,base):
        super().__init__(base);self.injected=False;self.requests=0
    def send(self,frame,timeout=1):
        if frame.data[:2]==b"\x02\x10": self.requests+=1
        self.base.send(frame,timeout=timeout)
    def recv(self,timeout):
        if not self.injected and self.base.frames and self.base.frames[0].data[1]==0x50:
            self.injected=True
            return Frame(0x7E8,b"\x03\x7F\x10\x78")
        return self.base.recv(timeout)

class CIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=os.environ.get("OTA_TEST_CC")
        if not cls.compiler: raise unittest.SkipTest("set OTA_TEST_CC to a host C compiler")
    def setUp(self):
        self.transport=SimTransport(compiler=self.compiler)
        self.client=UdsClient(IsoTpClient(self.transport))
    def tearDown(self):
        self.transport.close()
    def install(self,version=1):
        self.client.flash(image(version))
        self.assertEqual(self.transport.confirm_app_health(),0)
    def test_info_through_real_c_isotp(self):
        result=self.client.info()
        self.assertEqual(len(result["vin"]),17)
        self.assertEqual(result["bootloader_demo_version"],"0.2.0")
        self.assertEqual(result["session"],1)
    def test_session_timing_resolution(self):
        self.client.programming()
        self.assertEqual(self.client.p2,0.05)
        self.assertEqual(self.client.p2_star,5.0)
    def test_empty_metadata_status_over_can(self):
        status=self.client.status()
        self.assertFalse(status["metadata_valid"])
        self.assertEqual(status["flash_bytes"],1024*1024)
        self.assertEqual(status["state_name"],"IDLE")
    def test_ready_and_confirmed_status_over_can(self):
        package=image(3)
        self.client.flash(package,reset=False)
        status=self.client.status()
        self.assertEqual((status["state_name"],status["pending_version"],status["pending_size"]),("READY",3,1503))
        self.assertEqual(status["pending_crc32"],int(inspect_package(package)["payload_crc32"],16))
        self.transport.reboot();self.transport.confirm_app_health()
        status=self.client.status()
        self.assertEqual((status["state_name"],status["active_version"],status["min_version"]),("IDLE",3,3))
        self.assertEqual(status["pending_version"],0)
    def test_first_install_then_app_health(self):
        result=self.client.flash(image(1))
        self.assertFalse(result["application_health_confirmed"])
        self.assertEqual(self.transport.get_state()["state"],4)
        self.assertEqual(self.transport.get_state()["attempts"],1)
        self.assertEqual(self.transport.confirm_app_health(),0)
        self.assertEqual(self.transport.get_state()["active"],1)
    def test_unconfirmed_second_version_rolls_back(self):
        self.install(1)
        self.client.flash(image(2))
        self.transport.reboot();self.transport.reboot()
        state=self.transport.reboot()
        self.assertEqual((state["state"],state["active"],state["pending"]),(0,1,0))
    def test_healthy_second_version_commits(self):
        self.install(1);self.install(2)
        self.assertEqual(self.transport.get_state()["active"],2)
    def test_no_reset_cannot_confirm_ready_package(self):
        self.client.flash(image(1),reset=False)
        self.assertEqual(self.transport.get_state()["state"],2)
        self.assertNotEqual(self.transport.confirm_app_health(),0)
        self.assertEqual(self.transport.reboot()["state"],4)
        self.assertEqual(self.transport.confirm_app_health(),0)
    def test_downgrade_rejected_by_c(self):
        self.install(2)
        with self.assertRaises(NegativeResponse) as caught:
            self.client.flash(image(1))
        self.assertEqual(caught.exception.nrc,0x31)
        self.assertEqual(self.transport.reboot()["active"],2)
    def test_bad_crc_stopped_before_sending(self):
        bad=bytearray(image());bad[40]^=1
        with self.assertRaises(ValueError): self.client.flash(bad)
        self.assertEqual(self.transport.get_state()["metadata"],0)
        self.assertEqual(self.transport.get_stats()["nor_writes"],0)
    def test_lost_transfer_ack_retried_without_rewrite(self):
        events=[]
        wrapped=DropResponse(self.transport,0x76)
        client=UdsClient(IsoTpClient(wrapped),lambda **event:events.append(event))
        result=client.flash(image(),reset=False)
        self.assertEqual(wrapped.dropped,1)
        self.assertEqual(len([e for e in events if e["event"]=="block_retry"]),1)
        self.assertEqual(self.transport.get_stats()["nor_writes"],result["blocks"])
    def test_lost_commit_ack_stops_with_unknown_state(self):
        wrapped=DropResponse(self.transport,0x77)
        client=UdsClient(IsoTpClient(wrapped))
        with self.assertRaises(UnknownCommit): client.flash(image())
        state=self.transport.get_state()
        self.assertEqual((state["state"],state["pending"],state["attempts"]),(2,1,0))
        status=client.status() # investigate without another erase/session/reset
        self.assertEqual((status["state_name"],status["pending_version"]),("READY",1))
        self.assertEqual(self.transport.reboot()["state"],4) # ACK loss was not commit failure
    def test_lost_commit_ack_recovers_by_staged_install(self):
        package=image()
        events=[]
        client=UdsClient(IsoTpClient(DropResponse(self.transport,0x77)),lambda **event:events.append(event))
        with self.assertRaises(UnknownCommit): client.flash(package)
        events.clear()
        result=client.install_staged(package)
        self.assertEqual(result["downloaded_bytes"],0)
        self.assertFalse(any(e.get("sid") in ("34","36","37") for e in events))
        # Reboot resets operation counters. First install needs no backup,
        # so zero NOR writes after reboot also confirms no extra NOR work.
        self.assertEqual(self.transport.get_stats()["nor_writes"],0)
        self.assertEqual(client.status()["state_name"],"TRIAL")
    def test_lost_reset_ack_is_not_retried(self):
        events=[];wrapped=DropResponse(self.transport,0x51)
        client=UdsClient(IsoTpClient(wrapped),lambda **event:events.append(event))
        with self.assertRaises(UnknownReset): client.flash(image())
        state=client.status()
        self.assertEqual((state["state_name"],state["boot_attempts"]),("TRIAL",1))
        self.assertEqual(len([e for e in events if e["event"]=="reset_uncertain"]),1)
    def test_install_ready_without_downloading_again(self):
        package=image(7)
        self.client.flash(package,reset=False)
        result=self.client.install_staged(package)
        self.assertEqual(result["operation"],"install_staged")
        self.assertFalse(result["application_health_confirmed"])
        self.assertEqual(self.transport.get_state()["pending"],7)
    def test_install_rejects_mismatched_local_package(self):
        self.client.flash(image(1),reset=False)
        writes=self.transport.get_stats()["nor_writes"]
        with self.assertRaises(ValueError): self.client.install_staged(image(2))
        self.assertEqual(self.transport.get_stats()["nor_writes"],writes)
        self.assertEqual(self.client.status()["state_name"],"READY")
    def test_install_rejects_empty_state(self):
        with self.assertRaises(ValueError): self.client.install_staged(image())
        self.assertEqual(self.transport.get_stats()["nor_writes"],0)
    def test_install_checks_target_capacity(self):
        self.client.flash(image(),reset=False)
        with self.assertRaisesRegex(ValueError,"capacity"):
            self.client.install_staged(image(),flash_kib=512)
    def test_block_counter_wrap(self):
        events=[]
        client=UdsClient(IsoTpClient(self.transport),lambda **event:events.append(event))
        result=client.flash(image(size=131000),reset=False)
        counters=[e["counter"] for e in events if e["event"]=="block_accepted"]
        self.assertEqual(result["blocks"],258)
        self.assertEqual(counters[254:],[255,0,1,2])
    def test_wrong_and_changed_repeat_block_rejected(self):
        data=image()
        self.client.programming()
        self.client.request(b"\x34\x00\x44"+APP.to_bytes(4,"big")+len(data).to_bytes(4,"big"))
        with self.assertRaises(NegativeResponse) as caught:
            self.client.request(b"\x36\x02"+data[:510])
        self.assertEqual(caught.exception.nrc,0x73)
        self.assertEqual(self.client.request(b"\x36\x01"+data[:510]),b"\x76\x01")
        altered=bytearray(data[:510]);altered[50]^=1
        with self.assertRaises(NegativeResponse) as caught:
            self.client.request(b"\x36\x01"+altered)
        self.assertEqual(caught.exception.nrc,0x73)
        self.assertEqual(self.transport.get_stats()["nor_writes"],1)
    def test_download_power_cut_keeps_previous_app(self):
        self.install(1)
        self.transport.power_cut_after(100)
        with self.assertRaises(TransportTimeout): self.client.flash(image(2))
        self.assertEqual(self.transport.reboot()["active"],1)
        self.client.flash(image(2))
        self.assertEqual(self.transport.get_state()["pending"],2)
    def test_pending_response_does_not_resend_request(self):
        wrapped=PendingResponse(self.transport);events=[]
        client=UdsClient(IsoTpClient(wrapped),lambda **event:events.append(event))
        client.programming()
        self.assertEqual(wrapped.requests,1)
        self.assertTrue(any(e["event"]=="pending" for e in events))
    def test_extended_request_not_accepted_as_standard(self):
        self.transport.send(Frame(0x7E0,b"\x03\x22\xF1\x86",True))
        self.assertIsNone(self.transport.recv(0.05))
    def test_bad_sim_command_does_not_desynchronize(self):
        with self.assertRaises(TransportError): self.transport._exchange("UNKNOWN")
        self.assertEqual(self.transport.get_state()["active"],0)
    def test_ze_configuration(self):
        self.transport.close()
        self.transport=SimTransport(compiler=self.compiler,flash_kib=512)
        self.client=UdsClient(IsoTpClient(self.transport))
        self.client.flash(image(),flash_kib=512)
        self.assertEqual(self.transport.get_state()["pending"],1)
        self.assertEqual(self.client.status()["flash_bytes"],512*1024)

class Scripted:
    def __init__(self,frames=()):
        self.frames=list(frames);self.sent=[];self.t=0
    def send(self,frame,timeout=1): self.sent.append(frame)
    def recv(self,timeout):
        if self.frames: return self.frames.pop(0)
        self.t+=timeout
        return None
    def wait(self,seconds): self.t+=seconds
    def now(self): return self.t

class ClientGuards(unittest.TestCase):
    def test_malformed_single_frame(self):
        transport=Scripted([Frame(0x7E8,b"\x07\x62")])
        with self.assertRaises(ProtocolError): IsoTpClient(transport).receive()
    def test_oversize_response_sends_overflow(self):
        transport=Scripted([Frame(0x7E8,b"\x1F\xFF\x00")])
        with self.assertRaises(ProtocolError): IsoTpClient(transport).receive()
        self.assertEqual(transport.sent[0].data,b"\x32\x00\x00")
    def test_cf_sequence_error(self):
        transport=Scripted([Frame(0x7E8,b"\x10\x08\x62\xF1\x95\x30\x2E\x32"),
                            Frame(0x7E8,b"\x22\x2E\x30")])
        with self.assertRaises(ProtocolError): IsoTpClient(transport).receive()
    def test_fc_wait_limit(self):
        transport=Scripted([Frame(0x7E8,b"\x31\x00\x00")]*4)
        with self.assertRaises(TransportTimeout): IsoTpClient(transport).send(b"x"*8)
    def test_wrong_positive_sid(self):
        transport=Scripted([Frame(0x7E8,b"\x02\x50\x01")])
        with self.assertRaises(ProtocolError): UdsClient(IsoTpClient(transport)).request(b"\x11\x01")
    def test_wrong_negative_sid(self):
        transport=Scripted([Frame(0x7E8,b"\x03\x7F\x22\x31")])
        with self.assertRaises(ProtocolError): UdsClient(IsoTpClient(transport)).request(b"\x11\x01")
    def test_p2star_bounds_total_timeout(self):
        transport=Scripted([Frame(0x7E8,b"\x03\x7F\x10\x78")])
        client=UdsClient(IsoTpClient(transport),total_timeout=0.1)
        with self.assertRaises(TransportTimeout): client.request(b"\x10\x02")
        self.assertLessEqual(transport.t,0.1)
    def test_multiframe_response_obeys_total_deadline(self):
        class Slow(Scripted):
            def recv(self,timeout):
                if timeout<0.02:
                    self.t+=timeout
                    return None
                self.t+=0.02
                return super().recv(0)
        transport=Slow([Frame(0x7E8,b"\x10\x1E\x62abcde")]+
                       [Frame(0x7E8,bytes([0x21+i])+b"abcdefg") for i in range(4)])
        client=UdsClient(IsoTpClient(transport),total_timeout=0.05)
        with self.assertRaises(TransportTimeout): client.request(b"\x22\xF1\x90")
        self.assertLessEqual(transport.t,0.05)
    def test_flow_control_wait_obeys_total_deadline(self):
        transport=Scripted()
        with self.assertRaises(TransportTimeout):
            IsoTpClient(transport).send(b"x"*20,deadline=0.1)
        self.assertLessEqual(transport.t,0.1)
    def test_stmin_cannot_extend_deadline(self):
        transport=Scripted([Frame(0x7E8,b"\x30\x00\x7F")])
        with self.assertRaises(TransportTimeout):
            IsoTpClient(transport).send(b"x"*8,deadline=0.1)
        self.assertLessEqual(transport.t,0.1)
        self.assertEqual(len(transport.sent),1) # only FF; no late CF
    def test_classic_frame_bounds(self):
        for args in [(0x800,b"x"),(1,b""),(1,b"x"*9)]:
            with self.subTest(args=args),self.assertRaises(ValueError): Frame(*args)
    def test_frame_owns_immutable_data(self):
        data=bytearray(b"abc");frame=Frame(1,data);data[0]=0
        self.assertEqual(frame.data,b"abc")
    def test_preflight_rejects_extra_trailer(self):
        with self.assertRaises(ValueError): inspect_package(image()+b"extra")
    def test_real_backend_requires_explicit_selection(self):
        with self.assertRaises(ValueError): PythonCanTransport("",None)
    def test_request_deadline_must_be_finite(self):
        for timeout in (0,-1,float("nan"),float("inf")):
            with self.subTest(timeout=timeout),self.assertRaises(ValueError):
                UdsClient(IsoTpClient(Scripted()),total_timeout=timeout)
    def test_malformed_status_is_rejected(self):
        valid=bytearray(struct.pack(">4B9I",1,1,0,0,0,0,0,0,0,0,0,0,1024*1024))
        cases=[valid[:-1],valid+b"x"]
        for index,value in ((0,2),(1,2),(2,6),(3,4),(36,0xFF)):
            bad=bytearray(valid);bad[index]=value;cases.append(bad)
        client=UdsClient(IsoTpClient(Scripted()))
        for record in cases:
            client.read_did=lambda did:record
            with self.subTest(record=record),self.assertRaises(ProtocolError): client.status()
    def test_malformed_identification_is_protocol_error(self):
        for vin,version,session in ((b"x",b"0.2.0",b"\x01"),
                                    (b"x"*17,b"0.2.0",b""),
                                    (b"\xFF"*17,b"0.2.0",b"\x01")):
            client=UdsClient(IsoTpClient(Scripted()))
            values={0xF190:vin,0xF195:version,0xF186:session}
            client.read_did=lambda did:values[did]
            with self.subTest(vin=vin,session=session),self.assertRaises(ProtocolError): client.info()

class CanBackend(unittest.TestCase):
    """Driver contract only: no USB discovery, drivers or physical bus."""
    def setUp(self):
        class CanError(Exception): pass
        self.error=CanError
        self.options=None;self.messages=[];self.received=[];self.failure=None
        owner=self
        class Bus:
            def __init__(self,**options):
                owner.options=options
                if owner.failure=="open": raise CanError("open")
            def send(self,message,timeout):
                if owner.failure=="send": raise CanError("send")
                owner.messages.append((message,timeout))
            def recv(self,timeout):
                if owner.failure=="recv": raise CanError("recv")
                return owner.received.pop(0) if owner.received else None
            def shutdown(self):
                if owner.failure=="close": raise CanError("close")
        self.module=SimpleNamespace(CanError=CanError,Bus=Bus,Message=lambda **kw:SimpleNamespace(**kw))
    def open(self):
        return PythonCanTransport("gs_usb","test-channel",index=2,can_module=self.module)
    def test_explicit_options_and_send_budget(self):
        transport=self.open()
        self.assertTrue(self.options["ignore_config"])
        self.assertEqual(self.options["index"],2)
        self.assertEqual(self.options["can_filters"],[dict(can_id=0x7E8,can_mask=0x7FF,extended=False)])
        transport.send(Frame(0x7E0,b"123"),timeout=0.025)
        message,timeout=self.messages[0]
        self.assertFalse(message.is_extended_id)
        self.assertEqual(timeout,0.025)
        transport.close()
    def test_non_data_frames_are_filtered(self):
        def msg(**kw):
            values=dict(arbitration_id=0x7E8,data=b"abc",is_error_frame=False,
                        is_remote_frame=False,is_extended_id=False,is_fd=False)
            values.update(kw);return SimpleNamespace(**values)
        self.received=[msg(is_error_frame=True),msg(is_remote_frame=True),
                       msg(is_extended_id=True),msg(is_fd=True),msg(arbitration_id=0x700),msg()]
        transport=self.open()
        self.assertEqual(transport.recv(0.1),Frame(0x7E8,b"abc"))
        transport.close()
    def test_driver_failures_have_actionable_error(self):
        self.failure="open"
        with self.assertRaisesRegex(TransportError,"open CAN"): self.open()
        self.failure=None;transport=self.open()
        for action in ("send","recv","close"):
            self.failure=action
            with self.subTest(action=action),self.assertRaisesRegex(TransportError,action):
                if action=="send": transport.send(Frame(1,b"a"))
                elif action=="recv": transport.recv(0.1)
                else: transport.close()

if __name__=="__main__":
    unittest.main()
