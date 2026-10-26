import importlib
import plistlib
import struct
import unittest


DYLIB = "@executable_path/../Frameworks/KakaoAdBlock.dylib"


def module():
    try:
        return importlib.import_module("kakao_patch.macho")
    except ModuleNotFoundError:
        raise AssertionError("Mach-O editor has not been implemented") from None


def thin(cpu=0x0100000C, padding=512, with_entitlements=False):
    segment = bytearray(152)
    struct.pack_into("<II16sQQQQIIII", segment, 0, 0x19, 152, b"__TEXT", 0x100000000,
                     4096, 0, 4096, 7, 5, 1, 0)
    first_section = 32 + 152 + (16 if with_entitlements else 0) + padding
    struct.pack_into("<16s16sQQIIIIIIII", segment, 72, b"__text", b"__TEXT",
                     0x100000000 + first_section, 32, first_section, 2, 0, 0, 0, 0, 0, 0)
    commands = bytes(segment)
    signature = b""
    if with_entitlements:
        xml = plistlib.dumps({"com.apple.security.app-sandbox": True, "example": ["kept"]})
        blob = struct.pack(">II", 0xFADE7171, 8 + len(xml)) + xml
        signature = struct.pack(">IIIII", 0xFADE0CC0, 20 + len(blob), 1, 5, 20) + blob
        commands += struct.pack("<IIII", 0x1D, 16, 4096, len(signature))
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, cpu, 0, 2,
                         2 if with_entitlements else 1, len(commands), 0x200000, 0)
    body = bytearray(4096)
    body[:32] = header
    body[32:32 + len(commands)] = commands
    body[first_section:first_section + 32] = bytes(range(1, 33))
    return bytes(body) + signature


def fat(a, b):
    header = struct.pack(">II", 0xCAFEBABE, 2)
    header += struct.pack(">IIIII", 0x01000007, 3, 4096, len(a), 12)
    header += struct.pack(">IIIII", 0x0100000C, 0, 12288, len(b), 12)
    result = bytearray(12288 + len(b))
    result[:len(header)] = header
    result[4096:4096 + len(a)] = a
    result[12288:] = b
    return bytes(result)


class MachOTests(unittest.TestCase):
    def test_thin_load_command_preserves_section_bytes(self):
        data = thin()
        result = module().inject_dylib(data, DYLIB)
        self.assertEqual(len(data), len(result))
        self.assertEqual(struct.unpack_from("<I", result, 16)[0], 2)
        self.assertIn(DYLIB.encode() + b"\0", result[:696])
        self.assertEqual(result[696:], data[696:])

    def test_both_universal_architectures_receive_the_load_command(self):
        data = fat(thin(0x01000007), thin())
        result = module().inject_dylib(data, DYLIB)
        self.assertEqual(result[:4096], data[:4096])
        self.assertEqual(struct.unpack_from("<I", result, 4096 + 16)[0], 2)
        self.assertEqual(struct.unpack_from("<I", result, 12288 + 16)[0], 2)
        self.assertEqual(result.count(DYLIB.encode()), 2)

    def test_repeated_injection_does_not_duplicate_commands(self):
        first = module().inject_dylib(thin(), DYLIB)
        self.assertEqual(module().inject_dylib(first, DYLIB), first)

    def test_no_room_refuses_instead_of_moving_code(self):
        m = module()
        with self.assertRaises(m.MachOError):
            m.inject_dylib(thin(padding=8), DYLIB)

    def test_nonzero_header_padding_is_not_overwritten(self):
        data = bytearray(thin())
        data[200] = 0xA5
        m = module()
        with self.assertRaises(m.MachOError):
            m.inject_dylib(bytes(data), DYLIB)

    def test_malformed_command_size_is_rejected(self):
        data = bytearray(thin())
        struct.pack_into("<I", data, 36, 1)
        m = module()
        with self.assertRaises(m.MachOError):
            m.inject_dylib(bytes(data), DYLIB)

    def test_file_backed_section_at_zero_is_rejected(self):
        data = bytearray(thin())
        struct.pack_into("<QI", data, 32 + 72 + 40, 4096, 0)
        m = module()
        with self.assertRaises(m.MachOError):
            m.inject_dylib(bytes(data), DYLIB)

    def test_overlapping_fat_slices_are_rejected(self):
        data = bytearray(fat(thin(), thin()))
        struct.pack_into(">I", data, 36, 4096)
        m = module()
        with self.assertRaises(m.MachOError):
            m.inject_dylib(bytes(data), DYLIB)

    def test_entitlements_are_recovered_without_dropping_sandbox_flags(self):
        self.assertEqual(module().entitlements(thin(with_entitlements=True)),
                         {"com.apple.security.app-sandbox": True, "example": ["kept"]})

    def test_unknown_file_is_rejected(self):
        m = module()
        with self.assertRaises(m.MachOError):
            m.inject_dylib(b"not an executable", DYLIB)


if __name__ == "__main__":
    unittest.main()
