#!/usr/bin/env python3
"""Exercise the Squid ClientHello guard without network or credentials."""

import base64
import importlib.util
import io
from pathlib import Path
import ssl
import struct
import subprocess
import sys
import unittest
from urllib.parse import quote_from_bytes


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "clienthello_policy", ROOT / "clienthello-policy.py"
)
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


def clienthello(name="api.anthropic.com", version=ssl.TLSVersion.TLSv1_2):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.minimum_version = version
    context.maximum_version = version
    outgoing = ssl.MemoryBIO()
    connection = context.wrap_bio(ssl.MemoryBIO(), outgoing, server_hostname=name)
    try:
        connection.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


def record(payload, version=b"\x03\x01"):
    return b"\x16" + version + struct.pack("!H", len(payload)) + payload


def extension_parts(raw):
    hello = raw[9:]
    offset = 34
    offset += 1 + hello[offset]
    offset += 2 + int.from_bytes(hello[offset : offset + 2], "big")
    offset += 1 + hello[offset]
    prefix = hello[:offset]
    reader = POLICY.Reader(hello[offset:])
    extensions = POLICY.Reader(reader.vector(2))
    reader.done()
    pairs = []
    while extensions.offset < len(extensions.data):
        pairs.append((extensions.number(2), extensions.vector(2)))
    return prefix, pairs


def rewrite_extensions(raw, pairs):
    prefix, _ = extension_parts(raw)
    extensions = b"".join(
        struct.pack("!HH", kind, len(data)) + data for kind, data in pairs
    )
    body = prefix + struct.pack("!H", len(extensions)) + extensions
    return record(b"\x01" + len(body).to_bytes(3, "big") + body)


def name_extension(name):
    entry = b"\x00" + struct.pack("!H", len(name)) + name
    return struct.pack("!H", len(entry)) + entry


def request(raw, host=b"api.anthropic.com", data=False):
    fields = [base64.b64encode(raw)]
    if host is not None:
        fields.append(host)
    if data:
        fields.append(b"-")
    return b" ".join(fields) + b"\n"


class ClientHelloTests(unittest.TestCase):
    def setUp(self):
        self.raw = clienthello()
        self.prefix, self.extensions = extension_parts(self.raw)

    def test_real_tls12_clienthello(self):
        self.assertEqual(POLICY.clienthello_sni(self.raw), b"api.anthropic.com")
        self.assertTrue(POLICY.accept_line(request(self.raw)))

    @unittest.skipUnless(ssl.HAS_TLSv1_3, "Python TLS library has no TLS 1.3")
    def test_real_tls13_clienthello(self):
        raw = clienthello(version=ssl.TLSVersion.TLSv1_3)
        self.assertEqual(POLICY.clienthello_sni(raw), b"api.anthropic.com")
        self.assertTrue(POLICY.accept_line(request(raw)))

    def test_sni_is_required_even_with_allowed_connect_hostname(self):
        self.assertFalse(POLICY.accept_line(request(clienthello(name=None))))

    def test_connect_equality_case_insensitive_and_optional(self):
        self.assertTrue(POLICY.accept_line(request(self.raw, b"API.ANTHROPIC.COM")))
        self.assertTrue(POLICY.accept_line(request(self.raw, None)))
        self.assertFalse(POLICY.accept_line(request(self.raw, b"api.openai.com")))
        self.assertFalse(POLICY.accept_line(request(self.raw, b"-")))

    def test_invalid_sni_names_are_denied(self):
        for name in (
            b"",
            b"127.0.0.1",
            b"::1",
            b"bad_name.test",
            b"-a.test",
            b"a-.test",
            b"api..test",
            b"api.test.",
            b"a\x00.test",
            b"\xff.test",
            b"a" * 64 + b".test",
            b".".join([b"a" * 63] * 4),
        ):
            with self.subTest(name=name):
                pairs = [
                    (kind, name_extension(name) if kind == 0 else data)
                    for kind, data in self.extensions
                ]
                self.assertFalse(
                    POLICY.accept_line(
                        request(rewrite_extensions(self.raw, pairs), None)
                    )
                )

    def test_idna_ascii_hostname_is_valid(self):
        raw = clienthello(name="xn--bcher-kva.example")
        self.assertTrue(POLICY.accept_line(request(raw, b"xn--bcher-kva.example")))

    def test_ech_and_inner_only_extension_are_denied(self):
        for kind in (0xFE0D, 0xFD00):
            for payload in (b"", b"\x00\x00\x01\x00\x01\x01\x00\x00\x00\x01\x00"):
                with self.subTest(kind=kind, payload=payload):
                    raw = rewrite_extensions(
                        self.raw, self.extensions + [(kind, payload)]
                    )
                    self.assertFalse(POLICY.accept_line(request(raw)))

    def test_duplicate_extensions_are_denied(self):
        for pair in self.extensions:
            with self.subTest(kind=pair[0]):
                raw = rewrite_extensions(self.raw, self.extensions + [pair])
                self.assertFalse(POLICY.accept_line(request(raw)))

    def test_multiple_sni_entries_and_unknown_name_type_are_denied(self):
        entry = b"\x00\x00\x03one"
        for data in (
            struct.pack("!H", len(entry) * 2) + entry * 2,
            b"\x00\x06\x01\x00\x03one",
            name_extension(b"one") + b"x",
        ):
            pairs = [
                (kind, data if kind == 0 else value) for kind, value in self.extensions
            ]
            self.assertFalse(
                POLICY.accept_line(request(rewrite_extensions(self.raw, pairs), None))
            )

    def test_handshake_fragmented_across_tls_records(self):
        payload = self.raw[5:]
        for cuts in ((1, 2, 3, 17), (3, 4, 34), (len(payload) // 2,)):
            offset = 0
            chunks = []
            for cut in cuts:
                chunks.append(record(payload[offset:cut]))
                offset = cut
            chunks.append(record(payload[offset:]))
            self.assertTrue(POLICY.accept_line(request(b"".join(chunks))))

    def test_record_count_is_bounded(self):
        payload = self.raw[5:]
        raw = b"".join(record(payload[i : i + 1]) for i in range(len(payload)))
        self.assertFalse(POLICY.accept_line(request(raw)))

    def test_truncated_record_and_handshake_lengths_are_denied(self):
        variants = [self.raw[:offset] for offset in range(len(self.raw))]
        variants += [
            self.raw[:3] + struct.pack("!H", len(self.raw) - 4) + self.raw[5:],
            record(
                self.raw[5:6] + (len(self.raw) - 8).to_bytes(3, "big") + self.raw[9:]
            ),
            self.raw + b"\x00",
            self.raw + record(b"\x01"),
            record(self.raw[5:] + b"x"),
        ]
        for raw in variants:
            with self.subTest(length=len(raw)):
                self.assertFalse(POLICY.accept_line(request(raw)))

    def test_clienthello_vectors_are_checked(self):
        extensions = b"".join(
            struct.pack("!HH", kind, len(data)) + data for kind, data in self.extensions
        )
        suffix = struct.pack("!H", len(extensions)) + extensions
        version_and_random = self.raw[9:43]
        for vectors in (
            b"\x21" + b"x" * 33 + b"\x00\x02\x00\x2f\x01\x00",
            b"\x00\x00\x00\x01\x00",  # No cipher suites.
            b"\x00\x00\x01\x00\x01\x00",  # Odd cipher suite length.
            b"\x00\x00\x02\x00\x2f\x00",  # No compression methods.
            b"\x00\x00\x02\x00\x2f\x01\x01",  # No null compression.
        ):
            body = version_and_random + vectors + suffix
            raw = record(b"\x01" + len(body).to_bytes(3, "big") + body)
            self.assertFalse(POLICY.accept_line(request(raw)))

    def test_bad_extension_lengths_are_denied(self):
        for data in (
            b"",
            b"\x00\x01",
            b"\x00\x08\x00\x00\x03one",
            b"\x00\x06\x00\x00\x04one",
        ):
            pairs = [
                (kind, data if kind == 0 else value) for kind, value in self.extensions
            ]
            self.assertFalse(
                POLICY.accept_line(request(rewrite_extensions(self.raw, pairs)))
            )

    def test_unsupported_tls_record_is_denied(self):
        for raw in (
            b"\x15" + self.raw[1:],
            self.raw[:1] + b"\x02\x00" + self.raw[3:],
            record(b""),
            record(b"\x02" + self.raw[6:]),
        ):
            self.assertFalse(POLICY.accept_line(request(raw)))

    def test_oversized_records_raw_and_hello_are_denied(self):
        raw = record(self.raw[5:] + b"x" * 16384)
        self.assertFalse(POLICY.accept_line(request(raw)))
        self.assertFalse(POLICY.accept_line(request(b"x" * (POLICY.MAX_RAW_BYTES + 1))))
        self.assertFalse(POLICY.accept_line(request(record(b"\x01\xff\xff\xff"))))

    def test_helper_protocol_escaping_and_data_placeholder(self):
        encoded = quote_from_bytes(base64.b64encode(self.raw), safe="").encode()
        self.assertTrue(POLICY.accept_line(encoded + b" api.anthropic.com -\n"))
        self.assertTrue(POLICY.accept_line(request(self.raw, data=True)))
        for line in (
            b"",
            b"- api.anthropic.com\n",
            b"not-base64 api.anthropic.com\n",
            request(self.raw).rstrip() + b" extra\n",
            request(self.raw).rstrip() + b" - extra\n",
            b"%zz api.anthropic.com\n",
        ):
            self.assertFalse(POLICY.accept_line(line))

    def test_oversized_line_is_drained_without_poisoning_next_request(self):
        source = io.BytesIO(
            b"x" * (POLICY.MAX_LINE_BYTES * 2) + b"\n" + request(self.raw)
        )
        output = io.BytesIO()
        POLICY.serve(source, output)
        self.assertEqual(output.getvalue(), b"ERR\nOK\n")

    def test_protocol_rejects_bad_then_accepts_good_without_disclosing_input(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "clienthello-policy.py")],
            input=b"private-invalid-input\n" + request(self.raw, data=True),
            capture_output=True,
            check=True,
        )
        self.assertEqual(result.stdout, b"ERR\nOK\n")
        self.assertEqual(result.stderr, b"")

    def test_unterminated_final_protocol_request(self):
        output = io.BytesIO()
        POLICY.serve(io.BytesIO(request(self.raw).rstrip(b"\n")), output)
        self.assertEqual(output.getvalue(), b"OK\n")


if __name__ == "__main__":
    unittest.main()
