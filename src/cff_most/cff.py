#!/usr/bin/env python3
"""
CFF (MOST) compressed text decoder.

MOST V1.26 was an Amiga text viewer by R. Wynn (1988-89).
CFF is its compressed file format using LZSS, read backwards.

Header layout (big-endian):
  0x00  4 bytes   magic "CFF\x00"
  0x04 44 bytes   copyright message (null-padded)
  0x30  2 bytes   compressed data size
  0x32  2 bytes   padding (always 0)
  0x34  2 bytes   uncompressed data size
  0x36  4 bytes   XOR checksum seed
  0x3a  ...       compressed data (comp_size bytes)

Decompression algorithm (reverse-engineered from 68k binary):
  - LZSS variant, reading bit stream backwards from end of data
  - Bit buffer with sentinel: 32-bit word, LSB first via lsr.l
  - Refill: load next longword backwards, XOR with checksum accumulator,
    inject sentinel at bit 31 via roxr with extend=1
  - Control flow:
    bit=1: match reference
      2-bit code:
        0,1: short match (9+code bit offset, code+2 count, +1 for dbf)
        2: long match (8-bit count, 12-bit offset)
        3: counted literals (3-bit code + 8, +1 for dbf)
    bit=0: second bit
      1: short match (8-bit offset, count=2)
      0: counted literals (3-bit code, +1 for dbf)
"""

import struct
import sys


HEADER_SIZE = 0x3A
MAGIC = b"CFF\x00"


class BitReader:
    """Backwards bit reader matching the 68k CFF decompressor."""

    def __init__(self, data):
        self.data = data
        self.pos = len(data)
        # Load initial 32-bit buffer (last 4 bytes)
        self.pos -= 4
        self.bits = struct.unpack(">I", self.data[self.pos : self.pos + 4])[0]

    def read_bit(self):
        """Read one bit from the stream."""
        bit = self.bits & 1
        self.bits >>= 1
        if self.bits == 0:
            self.pos -= 4
            self.bits = struct.unpack(">I", self.data[self.pos : self.pos + 4])[0]
            bit = self.bits & 1
            self.bits = (self.bits >> 1) | 0x80000000
        return bit

    def read_bits(self, count):
        """Read count bits into a value (MSB first via roxl)."""
        val = 0
        for _ in range(count):
            val = (val << 1) | self.read_bit()
        return val


def decode_cff(data):
    """Decode CFF compressed data. Returns decompressed bytes."""
    if data[:3] != MAGIC[:3]:
        raise ValueError(f"invalid CFF magic: {data[:4]}")

    comp_size = struct.unpack(">H", data[0x30:0x32])[0]
    uncomp_size = struct.unpack(">H", data[0x34:0x36])[0]
    checksum_seed = struct.unpack(">I", data[0x36:0x3A])[0]

    compressed = data[HEADER_SIZE:]
    reader = BitReader(compressed)

    output = bytearray(uncomp_size)
    out_pos = uncomp_size

    while out_pos > 0:
        bit = reader.read_bit()

        if bit == 1:
            # match_ref
            code = reader.read_bits(2)

            if code < 2:
                # short match: 9+code bit offset, count = code+2
                offset_bits = 9 + code
                match_count = code + 2  # d3
                offset = reader.read_bits(offset_bits)
                for _ in range(match_count + 1):  # dbf = d3+1
                    out_pos -= 1
                    output[out_pos] = output[out_pos + offset]

            elif code == 3:
                # counted literals with d1=8, d4=8
                lit_code = reader.read_bits(8)
                count = lit_code + 8  # d3 = code + d4
                for _ in range(count + 1):
                    out_pos -= 1
                    output[out_pos] = reader.read_bits(8)

            else:
                # code == 2: long match with 12-bit offset
                match_count = reader.read_bits(8)  # d3
                offset = reader.read_bits(12)  # 12 bits, not 8!
                for _ in range(match_count + 1):
                    out_pos -= 1
                    output[out_pos] = output[out_pos + offset]

        else:
            bit2 = reader.read_bit()

            if bit2 == 1:
                # short match: 8-bit offset, count=2
                offset = reader.read_bits(8)
                for _ in range(2):  # d3=1, dbf = 2
                    out_pos -= 1
                    output[out_pos] = output[out_pos + offset]

            else:
                # counted literals with d4=0
                lit_code = reader.read_bits(3)
                count = lit_code  # d3 = code + 0
                for _ in range(count + 1):
                    out_pos -= 1
                    output[out_pos] = reader.read_bits(8)

    return bytes(output)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input.cff> [output]")
        sys.exit(1)

    with open(sys.argv[1], "rb") as f:
        data = f.read()

    result = decode_cff(data)

    if len(sys.argv) > 2:
        with open(sys.argv[2], "wb") as f:
            f.write(result)
        print(f"Decoded {len(data)} -> {len(result)} bytes to {sys.argv[2]}")
    else:
        sys.stdout.buffer.write(result)
