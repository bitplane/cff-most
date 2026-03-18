"""
CFF (MOST/LEAST) codec.

MOST V1.26 was an Amiga text viewer by Richard Wynn (1988-89).
LEAST V1.52 was the companion compressor.
CFF is the "Crunched File Format" using LZSS, read backwards.

Header layout (big-endian):
  0x00  4 bytes   magic "CFF\\x00"
  0x04 44 bytes   copyright message (null-padded)
  0x30  2 bytes   compressed data size
  0x32  2 bytes   padding (always 0)
  0x34  2 bytes   uncompressed data size
  0x36  4 bytes   XOR checksum
  0x3a  ...       compressed data (comp_size bytes)

Compression uses an LZSS variant with a backward bit stream.
Reverse-engineered from the 68k binary via vamos instruction tracing.

Encoding table (decoder reads right to left):
  0 0 + 3-bit count:             1 to 8 literal bytes
  0 1 + 8-bit offset:            2-byte match
  1 00 + 9-bit offset:           3-byte match
  1 01 + 10-bit offset:          4-byte match
  1 10 + 8-bit count + 12-bit offset:  long match (1 to 256 bytes)
  1 11 + 8-bit count:            9 to 264 literal bytes
"""

import struct


HEADER_SIZE = 0x3A
MAGIC = b"CFF\x00"
COPYRIGHT = b"To view text use MOST V1.0+ \xa9 R.Wynn 88,89"


# --- Bit I/O ---


class BitReader:
    """Backwards bit reader matching the 68k CFF decompressor."""

    def __init__(self, data):
        self.data = data
        self.pos = len(data)
        self.pos -= 4
        self.bits = struct.unpack(">I", self.data[self.pos : self.pos + 4])[0]

    def read_bit(self):
        bit = self.bits & 1
        self.bits >>= 1
        if self.bits == 0:
            self.pos -= 4
            self.bits = struct.unpack(">I", self.data[self.pos : self.pos + 4])[0]
            bit = self.bits & 1
            self.bits = (self.bits >> 1) | 0x80000000
        return bit

    def read_bits(self, count):
        val = 0
        for _ in range(count):
            val = (val << 1) | self.read_bit()
        return val


class BitWriter:
    """Backwards bit writer producing data readable by BitReader.

    The decoder reads bits LSB-first from 32-bit words (via lsr.l), and
    accumulates multi-bit values MSB-first (via roxl.l). Words are read
    backwards from end to start.

    The encoder collects bits into 31-bit words. Each bit is placed at
    increasing bit positions (LSB first). When full, a sentinel is set
    at bit 31 and the word is flushed. Words are emitted in encoding
    order and reversed at the end so the decoder reads them correctly.
    """

    def __init__(self):
        self.all_bits = []  # flat list of all data bits

    def write_bit(self, bit):
        self.all_bits.append(bit & 1)

    def write_bits(self, value, count):
        for i in range(count - 1, -1, -1):
            self.write_bit((value >> i) & 1)

    def finish(self):
        """Pack bits into longwords and return the compressed byte stream.

        Each refilled word delivers 32 bits to the decoder: 31 data bits
        (positions 0-30) plus a forced zero (from bit 31, which becomes
        bit 30 after roxr). We insert that zero every 31 data bits.
        The initial buffer (last word) has no padding overhead.
        """
        bits = self.all_bits

        # Each refilled word carries 32 data bits. The decoder reads them
        # via roxr (carry = bit 0, then lsr reads bits 0-30 of shifted
        # value = original bits 1-31, sentinel at bit 31 triggers refill).
        # Pack from the END so first bits go in the last word (initial buffer).
        words = []
        i = len(bits)

        while i - 32 >= 0:
            i -= 32
            w = 0
            for j in range(32):
                w |= bits[i + j] << j
            words.append(w)

        # Remaining bits at the start = initial buffer (no roxr, just lsr)
        if i > 0:
            w = 0
            for j in range(i):
                w |= bits[j] << j
            w |= 1 << i  # sentinel
            words.append(w)
        else:
            words.append(1)

        # words[0] = last refill word (first in output)
        # words[-1] = initial buffer (last in output)
        out = bytearray()
        for w in words:
            out.extend(struct.pack(">I", w & 0xFFFFFFFF))
        return bytes(out)


# --- Decoder ---


def decode(data):
    """Decode CFF compressed data. Returns decompressed bytes."""
    if data[:3] != MAGIC[:3]:
        raise ValueError(f"invalid CFF magic: {data[:4]}")

    uncomp_size = struct.unpack(">H", data[0x34:0x36])[0]
    compressed = data[HEADER_SIZE:]
    reader = BitReader(compressed)

    output = bytearray(uncomp_size)
    out_pos = uncomp_size

    while out_pos > 0:
        bit = reader.read_bit()

        if bit == 1:
            code = reader.read_bits(2)

            if code < 2:
                offset = reader.read_bits(9 + code)
                for _ in range(code + 3):
                    out_pos -= 1
                    output[out_pos] = output[out_pos + offset]

            elif code == 3:
                count = reader.read_bits(8) + 9
                for _ in range(count):
                    out_pos -= 1
                    output[out_pos] = reader.read_bits(8)

            else:
                match_count = reader.read_bits(8) + 1
                offset = reader.read_bits(12)
                for _ in range(match_count):
                    out_pos -= 1
                    output[out_pos] = output[out_pos + offset]

        else:
            bit2 = reader.read_bit()

            if bit2 == 1:
                offset = reader.read_bits(8)
                for _ in range(2):
                    out_pos -= 1
                    output[out_pos] = output[out_pos + offset]

            else:
                count = reader.read_bits(3) + 1
                for _ in range(count):
                    out_pos -= 1
                    output[out_pos] = reader.read_bits(8)

    return bytes(output)


# --- Encoder ---


def _find_match(data, pos):
    """Find the best match looking ahead from pos.

    The encoder processes input backwards (pos decrements). Already-encoded
    bytes are at data[pos:]. A match at offset `off` means the bytes at
    data[pos-1], data[pos-2], ... match data[pos+off-1], data[pos+off-2], ...

    Returns (offset, length) or (0, 0).
    """
    best_off = 0
    best_len = 0
    end = len(data)
    max_offset = 4095
    max_len = 256

    for off in range(1, min(max_offset + 1, end - pos + 1)):
        length = 0
        while length < max_len and pos - 1 - length >= 0:
            if data[pos - 1 - length] != data[pos - 1 + off - length]:
                break
            length += 1
        if length > best_len:
            best_len = length
            best_off = off
            if length >= max_len:
                break

    return best_off, best_len


def _emit_literals(writer, data, pos, count):
    """Emit count literal bytes backwards from pos. Returns new pos."""
    remaining = count
    while remaining > 0:
        if remaining >= 9:
            chunk = min(remaining, 264)
            writer.write_bits(0b1, 1)
            writer.write_bits(0b11, 2)
            writer.write_bits(chunk - 9, 8)
            for _ in range(chunk):
                pos -= 1
                writer.write_bits(data[pos], 8)
            remaining -= chunk
        else:
            writer.write_bits(0b00, 2)
            writer.write_bits(remaining - 1, 3)
            for _ in range(remaining):
                pos -= 1
                writer.write_bits(data[pos], 8)
            remaining = 0
    return pos


def _emit_match(writer, offset, length):
    """Emit a match reference."""
    if length == 2 and offset < 256:
        writer.write_bits(0b01, 2)
        writer.write_bits(offset, 8)
    elif length == 3 and offset < 512:
        writer.write_bits(0b1, 1)
        writer.write_bits(0b00, 2)
        writer.write_bits(offset, 9)
    elif length == 4 and offset < 1024:
        writer.write_bits(0b1, 1)
        writer.write_bits(0b01, 2)
        writer.write_bits(offset, 10)
    else:
        writer.write_bits(0b1, 1)
        writer.write_bits(0b10, 2)
        writer.write_bits(length - 1, 8)
        writer.write_bits(offset, 12)


def encode(data):
    """Encode data into CFF format. Returns complete CFF file bytes."""
    if len(data) > 0xFFFF:
        raise ValueError(f"data too large for CFF: {len(data)} bytes (max 65535)")

    writer = BitWriter()
    pos = len(data)

    while pos > 0:
        off, length = _find_match(data, pos)

        # Check if match is encodable
        if length >= 2 and off < 4096:
            _emit_match(writer, off, min(length, 256))
            pos -= min(length, 256)
        else:
            # Count consecutive non-matchable positions
            lit_count = 0
            scan = pos
            while scan > 0 and lit_count < 264:
                o, l = _find_match(data, scan)
                if l >= 3 and o < 4096:
                    break
                lit_count += 1
                scan -= 1
            if lit_count == 0:
                lit_count = 1
            pos = _emit_literals(writer, data, pos, lit_count)

    compressed = writer.finish()

    # Compute XOR checksum
    checksum = 0
    for i in range(0, len(compressed), 4):
        word = struct.unpack(">I", compressed[i : i + 4])[0]
        checksum ^= word

    # Build header
    header = bytearray(HEADER_SIZE)
    header[0:4] = MAGIC
    msg = COPYRIGHT[:44].ljust(44, b"\x00")
    header[4:0x30] = msg
    struct.pack_into(">H", header, 0x30, len(compressed))
    struct.pack_into(">H", header, 0x34, len(data))
    struct.pack_into(">I", header, 0x36, checksum)

    return bytes(header) + compressed


# Keep old names as aliases
decode_cff = decode
encode_cff = encode
