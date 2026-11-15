import pytest

from hondalog import protocol as p


def test_checksum_matches_known_request():
    # 20 05 00 10 CB is the row-1 request from the kerpz source
    assert p.checksum(bytes([0x20, 0x05, 0x00, 0x10])) == 0xCB
    assert p.build_read_request(0x00, 0x10) == bytes.fromhex("20050010cb")


@pytest.mark.parametrize("addr,length,expected", [
    (0x10, 0x10, "20051010bb"),
    (0x20, 0x10, "20052010ab"),
    (0x76, 0x0A, "2005760a5b"),
])
def test_other_known_requests(addr, length, expected):
    assert p.build_read_request(addr, length) == bytes.fromhex(expected)


def test_every_frame_sums_to_zero():
    for addr in range(0, 256, 7):
        for length in (1, 2, 16):
            assert sum(p.build_read_request(addr, length)) % 256 == 0


def test_checksum_wraps():
    assert p.checksum(b"") == 0
    assert p.checksum(bytes([0xFF, 0x01])) == 0
    assert p.checksum(bytes([0xFF, 0xFF])) == 0x02
    assert p.verify(p.with_checksum(bytes(range(200))))
    assert not p.verify(b"")


def test_request_range_checks():
    with pytest.raises(ValueError):
        p.build_read_request(0x100, 1)
    with pytest.raises(ValueError):
        p.build_read_request(0, 0)


def test_build_reply_framing():
    reply = p.build_reply(bytes([0x0B, 0xB8]))
    assert reply[0] == 0x00            # header
    assert reply[1] == 2 + 3           # total length
    assert reply[2:4] == bytes([0x0B, 0xB8])
    assert p.verify(reply)
    assert len(reply) == p.reply_size(2)


# ---- echo stripping ----

def test_strip_echo_present():
    req = p.build_read_request(0, 2)
    rest, seen = p.strip_echo(req + b"\x00\x05", req)
    assert seen and rest == b"\x00\x05"


def test_strip_echo_absent():
    req = p.build_read_request(0, 2)
    rest, seen = p.strip_echo(b"\x00\x05\x01", req)
    assert not seen and rest == b"\x00\x05\x01"


def test_strip_echo_after_junk():
    req = p.build_read_request(0, 2)
    rest, seen = p.strip_echo(b"\xff\x00" + req + b"\xaa", req)
    assert seen and rest == b"\xaa"


def test_strip_echo_only():
    req = p.build_read_request(0, 2)
    assert p.strip_echo(req, req) == (b"", True)


# ---- reply parsing ----

DATA = bytes([0x0B, 0xB8, 0x00, 0x11])
REQ = p.build_read_request(0x00, len(DATA))
REPLY = p.build_reply(DATA)


def test_parse_with_echo():
    assert p.parse_reply(REQ + REPLY, REQ, len(DATA)) == DATA


def test_parse_without_echo():
    assert p.parse_reply(REPLY, REQ, len(DATA)) == DATA


def test_parse_junk_before_echo_and_before_frame():
    buf = b"\xfe\x00" + REQ + b"\x55\x00\x99" + REPLY + b"\x01"
    assert p.parse_reply(buf, REQ, len(DATA)) == DATA


def test_parse_reply_containing_zero_data():
    data = bytes(16)
    req = p.build_read_request(0x40, 16)
    assert p.parse_reply(req + p.build_reply(data), req, 16) == data


def test_parse_data_that_looks_like_a_header():
    # data starts with 00 07 - same as the header of a 4-byte reply
    data = bytes([0x00, 0x07, 0x00, 0x07])
    req = p.build_read_request(0x00, 4)
    assert p.parse_reply(req + p.build_reply(data), req, 4) == data


def test_nothing_received():
    with pytest.raises(p.NoReply, match="no bytes"):
        p.parse_reply(b"", REQ, len(DATA))


def test_echo_only():
    with pytest.raises(p.NoReply, match="echo only"):
        p.parse_reply(REQ, REQ, len(DATA))


def test_truncated_reply():
    with pytest.raises(p.ShortReply):
        p.parse_reply(REQ + REPLY[:-2], REQ, len(DATA))


def test_bad_checksum():
    bad = bytearray(REPLY)
    bad[3] ^= 0x40
    with pytest.raises(p.BadChecksum):
        p.parse_reply(REQ + bytes(bad), REQ, len(DATA))


def test_wrong_length_byte():
    bad = bytearray(REPLY)
    bad[1] += 1
    bad[-1] = p.checksum(bad[:-1])
    with pytest.raises(p.ProtocolError):
        p.parse_reply(REQ + bytes(bad), REQ, len(DATA))


def test_pure_garbage():
    with pytest.raises(p.BadFrame):
        p.parse_reply(REQ + b"\xff\xfe\xfd\xfc\xfb\xfa\xf9", REQ, len(DATA))


def test_all_errors_share_a_base_class():
    for exc in (p.NoReply, p.ShortReply, p.BadChecksum, p.BadFrame):
        assert issubclass(exc, p.ProtocolError)


def test_custom_framing():
    f = p.DEFAULT.with_overrides({"reply_header": -1, "cmd_read": 0x21})
    req = p.build_read_request(0x00, 2, f)
    assert req[0] == 0x21
    reply = p.with_checksum(bytes([0x7E, 5, 0xAA, 0xBB]))   # odd header byte
    assert p.parse_reply(req + reply, req, 2, f) == b"\xaa\xbb"
    with pytest.raises(p.ProtocolError):
        p.parse_reply(req + reply, req, 2, p.DEFAULT)


def test_framing_rejects_unknown_keys():
    with pytest.raises(ValueError):
        p.DEFAULT.with_overrides({"bogus": 1})


def test_init_sequence_override_from_string():
    assert p.DEFAULT.with_overrides({"init_sequence": ""}).init_sequence == b""
    assert p.DEFAULT.with_overrides({"init_sequence": "68 6a"}).init_sequence == b"\x68\x6a"


def test_find_any_frame():
    assert p.find_any_frame(b"\x99" + REPLY + b"\x01") == REPLY
    assert p.find_any_frame(b"\x01\x02\x03") is None


def test_parse_hex_forms():
    assert p.parse_hex("20 05 00 10") == bytes([0x20, 5, 0, 0x10])
    assert p.parse_hex("20050010") == bytes([0x20, 5, 0, 0x10])
    assert p.parse_hex("0x20, 0x5, 0, 10") == bytes([0x20, 5, 0, 0x10])
    assert p.parse_hex("") == b""
    with pytest.raises(ValueError):
        p.parse_hex("zz")
    assert p.fmt_hex(b"\x00\xab") == "00 AB"
