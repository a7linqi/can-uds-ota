"""Pure-Python Ed25519 (RFC 8032) for firmware signing.

Standard-library only (hashlib.sha512). Used by the PC packaging tool to
sign IMG2 update images. The MCU verifies the same signatures with the
vendored C implementation in third_party/ed25519. This module is
cross-checked against the RFC 8032 section 7.1 test vectors, and against
the C build in tests_tools.

Key/signature formats (standard Ed25519):
  seed       : 32 bytes of entropy
  private key: 64 bytes = SHA-512(seed) with the low half clamped
  public key : 32 bytes (compressed Edwards point)
  signature  : 64 bytes = R(32) || S(32)
"""

import hashlib

B = 256
Q = 2**255 - 19                                   # field prime p
L = 2**252 + 27742317777372353535851937790883648493  # group order l
D = (-121665 * pow(121666, -1, Q)) % Q            # Edwards curve d
I = pow(2, (Q - 1) // 4, Q)                       # sqrt(-1) in the field


def _H(data):
    return hashlib.sha512(data).digest()


def _xrecover(y):
    """Recover the x coordinate (even) of a point from its y."""
    xx = (y * y - 1) * pow(D * y * y + 1, -1, Q) % Q
    x = pow(xx, (Q + 3) // 8, Q)
    if (x * x - xx) % Q != 0:
        x = (x * I) % Q
    if x & 1:
        x = Q - x
    return x


BY = 4 * pow(5, -1, Q) % Q
BX = _xrecover(BY)
BASE = (BX % Q, BY % Q)                           # standard base point


def _point_add(P, other):
    """Edwards point addition, complete for all inputs including (0,1)."""
    x1, y1 = P
    x2, y2 = other
    x3 = (x1 * y2 + x2 * y1) * pow(1 + D * x1 * x2 * y1 * y2, -1, Q) % Q
    y3 = (y1 * y2 + x1 * x2) * pow(1 - D * x1 * x2 * y1 * y2, -1, Q) % Q
    return (x3, y3)


def _scalarmult(P, e):
    """Iterative double-and-add scalar multiplication."""
    result = (0, 1)                               # identity element
    addend = P
    while e:
        if e & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        e >>= 1
    return result


def _encode_int(y):
    bits = [(y >> i) & 1 for i in range(B)]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8))
                 for i in range(B // 8))


def _encode_point(P):
    x, y = P
    bits = [(y >> i) & 1 for i in range(B - 1)] + [x & 1]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8))
                 for i in range(B // 8))


def _decode_int(s):
    return sum(2**i * ((s[i // 8] >> (i % 8)) & 1) for i in range(B))


def _decode_point(s):
    if len(s) != 32:
        raise ValueError("point must be 32 bytes")
    y = sum(2**i * ((s[i // 8] >> (i % 8)) & 1) for i in range(B - 1))
    if y >= Q:
        raise ValueError("noncanonical point coordinate")
    x = _xrecover(y)
    sign_bit = (s[B // 8 - 1] >> 7) & 1
    if x == 0 and sign_bit:
        raise ValueError("zero x coordinate cannot have its sign bit set")
    if x & 1 != sign_bit:
        x = Q - x
    P = (x, y)
    if (-x * x + y * y - 1 - D * x * x * y * y) % Q != 0:
        raise ValueError("decoding point not on the curve")
    return P


def _hint(m):
    h = _H(m)
    return sum(2**i * ((h[i // 8] >> (i % 8)) & 1) for i in range(2 * B))


def generate_keypair(seed):
    """Return (public_key, private_key) from a 32-byte seed."""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    private_key = bytearray(_H(seed))
    private_key[0] &= 248
    private_key[31] &= 63
    private_key[31] |= 64
    a = _decode_int(bytes(private_key[0:32]))
    public_key = _encode_point(_scalarmult(BASE, a))
    return bytes(public_key), bytes(private_key)


def sign(message, public_key, private_key):
    """Return a 64-byte signature. private_key is the 64-byte expanded key."""
    if len(private_key) != 64 or len(public_key) != 32:
        raise ValueError("private key must be 64 bytes, public key 32 bytes")
    a = _decode_int(private_key[0:32])
    prefix = private_key[32:64]
    r = _hint(prefix + message) % L
    R = _encode_point(_scalarmult(BASE, r))
    hram = _hint(R + public_key + message) % L
    S = (r + hram * a) % L
    return R + _encode_int(S)


def verify(signature, message, public_key):
    """Return True if the signature is valid for message under public_key."""
    if len(signature) != 64 or len(public_key) != 32:
        return False
    S = _decode_int(signature[32:64])
    if S >= L:
        return False
    try:
        R = _decode_point(signature[0:32])
        A = _decode_point(public_key)
    except ValueError:
        return False
    h = _hint(signature[0:32] + public_key + message) % L
    left = _scalarmult(BASE, S)
    right = _point_add(R, _scalarmult(A, h))
    return left == right
