"""Network Workspace encryption (cryptography: X25519 + HKDF-SHA256 + AES-GCM).

Handshake
    Host  -> challenge (plain): nonce, host public key
    Client-> key       (plain): client public key            (both switch to encryption)
    Client-> hello     (encrypted): name + proof = HMAC(join code, nonce | name | both keys)
    Host  -> welcome / reject (encrypted)

Each side's traffic key comes from the X25519 shared secret (an eavesdropper
cannot compute it). The join-code proof travels encrypted and binds both
public keys, so a man in the middle who swaps keys is rejected and nobody can
brute-force the code from captured traffic. Records carry an implicit counter
nonce, so replayed, reordered or altered records fail authentication.
"""

from __future__ import annotations

import hashlib
import hmac
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_MAGIC = b"LLVE"
_RECORD = struct.Struct(">4sI")
MAX_RECORD = 64 * 1024 * 1024


class SecureError(ValueError):
    pass


class KeyPair:
    def __init__(self):
        self._private = X25519PrivateKey.generate()
        self.public = self._private.public_key().public_bytes(serialization.Encoding.Raw,
                                                               serialization.PublicFormat.Raw)

    def session(self, peer_public_hex: str, nonce: str, *, is_host: bool) -> "Session":
        try:
            peer = X25519PublicKey.from_public_bytes(bytes.fromhex(peer_public_hex))
        except ValueError:
            raise SecureError("Invalid peer key.") from None
        shared = self._private.exchange(peer)
        material = HKDF(algorithm=hashes.SHA256(), length=64, salt=nonce.encode("utf-8"),
                        info=b"LabLogViewer Network Workspace v2").derive(shared)
        host_to_client, client_to_host = material[:32], material[32:]
        return Session(send=host_to_client if is_host else client_to_host,
                       receive=client_to_host if is_host else host_to_client)


def join_proof(code: str, nonce: str, client_name: str, client_public: str, host_public: str) -> str:
    message = f"{nonce}|{client_name}|{client_public}|{host_public}".encode("utf-8")
    return hmac.new(code.encode("utf-8"), message, hashlib.sha256).hexdigest()


class Session:
    """Two AES-GCM directions with implicit 64-bit counters."""

    def __init__(self, send: bytes, receive: bytes):
        self._send, self._receive = AESGCM(send), AESGCM(receive)
        self._send_counter = 0
        self._receive_counter = 0

    @staticmethod
    def _nonce(counter: int) -> bytes:
        return b"\x00\x00\x00\x00" + counter.to_bytes(8, "big")

    def seal(self, plaintext: bytes) -> bytes:
        ciphertext = self._send.encrypt(self._nonce(self._send_counter), plaintext, RECORD_MAGIC)
        self._send_counter += 1
        return _RECORD.pack(RECORD_MAGIC, len(ciphertext)) + ciphertext

    def open_records(self, buffer: bytearray) -> list[bytes]:
        """Decrypt every complete record at the front of ``buffer`` (consumed)."""
        out = []
        while len(buffer) >= _RECORD.size:
            magic, length = _RECORD.unpack_from(buffer, 0)
            if magic != RECORD_MAGIC or length > MAX_RECORD:
                raise SecureError("Unencrypted or oversized data on an encrypted connection.")
            if len(buffer) < _RECORD.size + length:
                break
            ciphertext = bytes(buffer[_RECORD.size:_RECORD.size + length])
            del buffer[:_RECORD.size + length]
            try:
                out.append(self._receive.decrypt(self._nonce(self._receive_counter), ciphertext, RECORD_MAGIC))
            except Exception:
                raise SecureError("Decryption failed (tampered, replayed or wrong key).") from None
            self._receive_counter += 1
        return out
