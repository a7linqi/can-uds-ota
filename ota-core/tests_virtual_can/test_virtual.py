import os
from pathlib import Path
import struct
import sys
import unittest
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import can
from bench import VirtualBench
from make_image import APP,package,DEMO_SEED
from pc.transport import Frame,PythonCanTransport
from pc.isotp_client import IsoTpClient,TransportTimeout
from pc.uds_client import UdsClient,NegativeResponse

class RealLibraryIntegration(unittest.TestCase):
    def setUp(self):
        self.cc=os.environ["OTA_TEST_CC"]
        self.bench=VirtualBench(self.cc)
        self.client=UdsClient(IsoTpClient(self.bench.transport))
    def tearDown(self): self.bench.close()
    def image(self,version=1,seed=DEMO_SEED):
        return package(struct.pack("<II",0x20020000,APP+9)+b"\xA5"*1495,version,seed=seed)
    def test_info_and_status(self):
        self.assertEqual(len(self.client.info()["vin"]),17)
        self.assertFalse(self.client.status()["metadata_valid"])
    def test_flash_staged_install_and_health(self):
        image=self.image()
        self.client.flash(image,reset=False)
        self.assertEqual(self.client.status()["state_name"],"READY")
        self.client.install_staged(image)
        self.assertEqual(self.client.status()["state_name"],"TRIAL")
        self.assertEqual(self.bench.control("confirm_app_health"),0)
        self.assertEqual(self.client.status()["active_version"],1)
    def test_remote_error_extended_fd_and_other_ids_filtered(self):
        for options in (dict(is_remote_frame=True),dict(is_error_frame=True),
                        dict(is_extended_id=True),dict(is_fd=True),dict(arbitration_id=0x7E9)):
            values=dict(arbitration_id=0x7E8,data=b"",is_extended_id=False)
            values.update(options)
            self.bench.ecu_bus.send(can.Message(**values),timeout=1)
        self.bench.ecu_bus.send(can.Message(arbitration_id=0x7E8,data=b"\x01\x7E",is_extended_id=False),timeout=1)
        self.assertEqual(self.bench.transport.recv(0.1),Frame(0x7E8,b"\x01\x7E"))
    def test_unsigned_cannot_replace_confirmed_app(self):
        self.client.flash(self.image())
        self.assertEqual(self.bench.control("confirm_app_health"),0)
        with self.assertRaises(NegativeResponse) as error:
            self.client.flash(self.image(version=2,seed=None),reset=False)
        self.assertEqual((error.exception.sid,error.exception.nrc),(0x37,0x72))
        status=self.client.status()
        self.assertEqual(status["state_name"],"DOWNLOADING")
        self.assertEqual(status["active_version"],1)
    def test_tampered_signature_rejected(self):
        image=bytearray(self.image());image[-1]^=1
        with self.assertRaises(NegativeResponse) as error:
            self.client.flash(image,reset=False)
        self.assertEqual((error.exception.sid,error.exception.nrc),(0x37,0x72))
        self.assertEqual(self.client.status()["state_name"],"DOWNLOADING")
    def test_512_capacity(self):
        self.bench.close();self.bench=VirtualBench(self.cc,512)
        self.client=UdsClient(IsoTpClient(self.bench.transport))
        self.assertEqual(self.client.status()["flash_bytes"],512*1024)
        self.client.flash(self.image(),flash_kib=512)
        self.assertEqual(self.client.status()["state_name"],"TRIAL")
    def test_no_peer_honors_receive_timeout(self):
        transport=PythonCanTransport("virtual","no-peer-"+uuid.uuid4().hex,can_module=can)
        try:
            client=UdsClient(IsoTpClient(transport),total_timeout=0.025)
            with self.assertRaises(TransportTimeout): client.request(b"\x22\xF1\x86")
        finally: transport.close()

if __name__=="__main__": unittest.main()
