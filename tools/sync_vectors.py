"""Test values for the sync protocol (notes/sync-protocol.md), so the
Android port can check its crypto gives the same bytes as the desktop.

    python3 tools/sync_vectors.py

Everything below is fixed input, nothing random: two P-256 private keys,
a code, two device ids, a pair key and two nonces.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from cryptography.hazmat.primitives.asymmetric import ec   # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

import sync_core as core                                    # noqa: E402

CLIENT_ID = "11" * 16
SERVER_ID = "22" * 16
CODE = "ABCDE12345"


def main():
    client = ec.derive_private_key(0xC1, ec.SECP256R1())
    server = ec.derive_private_key(0x5E, ec.SECP256R1())
    cpub, spub = core.ec_public_bytes(client), core.ec_public_bytes(server)
    z = core.ec_shared(client, spub)
    assert z == core.ec_shared(server, cpub)
    confirm, pair_key = core.pair_keys(z, CODE, cpub, spub, CLIENT_ID,
                                       SERVER_ID)
    print("pairing")
    print("  client private key   0xC1 (secp256r1)")
    print("  server private key   0x5E (secp256r1)")
    print("  client point         ", cpub.hex())
    print("  server point         ", spub.hex())
    print("  code                 ", CODE)
    print("  client id / server id", CLIENT_ID, SERVER_ID)
    print("  z (ECDH)             ", z.hex())
    print("  confirm_key          ", confirm.hex())
    print("  pair key             ", pair_key.hex())
    print("  client mac (b64)     ", core.b64(core.mac(confirm, "client")))
    print("  server mac (b64)     ", core.b64(core.mac(confirm, "server")))

    cn, sn = bytes(range(16)), bytes(range(16, 32))
    k_cs, k_sc = core.session_keys(pair_key, cn, sn)
    print("session (pair key above)")
    print("  client nonce         ", cn.hex())
    print("  server nonce         ", sn.hex())
    print("  k_cs                 ", k_cs.hex())
    print("  k_sc                 ", k_sc.hex())
    msg = b'{"t":"ping"}'
    for n in (0, 1):
        nonce = b"\0\0\0\0" + n.to_bytes(8, "big")
        box = AESGCM(k_cs).encrypt(nonce, msg, None)
        print(f"  frame {n} client->server, plaintext {msg.decode()}:")
        print("    ", box.hex())


if __name__ == "__main__":
    main()
