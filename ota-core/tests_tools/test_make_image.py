import struct
import sys
import unittest
import zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from make_image import APP, MAGIC, package

def raw(stack=0x20020000, entry=APP+9, size=19):
    return struct.pack("<II", stack, entry) + b"\xA5" * (size-8)

class PackageTests(unittest.TestCase):
    def test_wire_header(self):
        image=package(raw(),7)
        self.assertEqual(struct.unpack("<7I",image[:28]),(MAGIC,7,19,zlib.crc32(raw()),APP,0,0))
        self.assertEqual(image[32:],raw())
    def test_header_crc(self):
        image=package(raw(),7)
        self.assertEqual(struct.unpack("<I",image[28:32])[0],zlib.crc32(image[:28]))
    def test_zero_version(self):
        with self.assertRaises(ValueError): package(raw(),0)
    def test_large_version(self):
        with self.assertRaises(ValueError): package(raw(),0x100000000)
    def test_short(self):
        with self.assertRaises(ValueError): package(b"\0"*7,1)
    def test_wrong_link(self):
        with self.assertRaises(ValueError): package(raw(entry=0x08000009),1)
    def test_missing_thumb(self):
        with self.assertRaises(ValueError): package(raw(entry=APP+8),1)
    def test_outside_payload(self):
        with self.assertRaises(ValueError): package(raw(entry=APP+101),1)
    def test_invalid_ram(self):
        with self.assertRaises(ValueError): package(raw(stack=0x08010000),1)
    def test_stack_alignment(self):
        with self.assertRaises(ValueError): package(raw(stack=0x2001FFFF),1)
    def test_ccm_stack(self):
        self.assertEqual(package(raw(stack=0x10010000),1)[32:],raw(stack=0x10010000))
    def test_bad_capacity(self):
        with self.assertRaises(ValueError): package(raw(),1,256)
    def test_exact_ze_capacity(self):
        data=raw(size=512*1024-0x10000)
        self.assertEqual(len(package(data,1,512)),len(data)+32)
    def test_over_ze_capacity(self):
        with self.assertRaises(ValueError): package(raw(size=512*1024-0x10000+1),1,512)

if __name__=="__main__":
    unittest.main()
