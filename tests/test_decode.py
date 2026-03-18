from pathlib import Path

import pytest

from cff_most import decode, encode

DATA = Path(__file__).parent / "data"


def test_decode_about_reset():
    raw = (DATA / "About_Reset.cff").read_bytes()
    text = decode(raw)
    assert len(text) == 575
    assert b"ABOUT RESET" in text
    assert b"Robert Gill" in text


def test_decode_most_docs():
    raw = (DATA / "Most_docs.cff").read_bytes()
    text = decode(raw)
    assert len(text) == 5825
    assert b"MOST" in text


def test_invalid_magic():
    with pytest.raises(ValueError, match="invalid CFF magic"):
        decode(b"NOT_CFF_DATA")


def test_roundtrip_simple():
    original = b"Hello, Amiga! " * 20
    compressed = encode(original)
    assert compressed[:3] == b"CFF"
    recovered = decode(compressed)
    assert recovered == original


def test_roundtrip_about_reset():
    raw = (DATA / "About_Reset.cff").read_bytes()
    text = decode(raw)
    recompressed = encode(text)
    recovered = decode(recompressed)
    assert recovered == text


def test_roundtrip_most_docs():
    raw = (DATA / "Most_docs.cff").read_bytes()
    text = decode(raw)
    recompressed = encode(text)
    recovered = decode(recompressed)
    assert recovered == text


def test_encode_empty():
    compressed = encode(b"")
    recovered = decode(compressed)
    assert recovered == b""


def test_encode_single_byte():
    compressed = encode(b"X")
    recovered = decode(compressed)
    assert recovered == b"X"


def test_encode_compresses():
    original = b"A" * 1000
    compressed = encode(original)
    assert len(compressed) < len(original)
