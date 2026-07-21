"""Minimaler QR-Code-Encoder (Byte-Modus) — reine Standardbibliothek.

Erzeugt aus einem kurzen Text (z. B. der Adresse der Fern-Erfassung) eine
Schwarz/Weiß-Modulmatrix, die SailLog auf einem tk-Canvas zeichnet — damit
man die Adresse am Handy nur abscannen muss.

Umfang: Byte-Modus, Fehlerkorrektur-Level L/M/Q/H, Versionen 1–10 (reicht für
URLs weit über 100 Zeichen). Maskenwahl nach den Standard-Strafregeln.

Korrektheit wird im Test gegen die etablierte Bibliothek *segno* geprüft
(nur im Test, nicht zur Laufzeit).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

# --- Galois-Feld GF(256), Primitivpolynom 0x11d ----------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11d
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_divisor(degree: int) -> List[int]:
    """Reed-Solomon-Divisor (kanonische Nayuki-Variante), Länge = degree."""
    result = [0] * degree
    result[degree - 1] = 1
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_encode(data: List[int], degree: int) -> List[int]:
    divisor = _rs_divisor(degree)
    res = [0] * degree
    for d in data:
        factor = d ^ res[0]
        res = res[1:] + [0]
        for i in range(degree):
            res[i] ^= _gf_mul(divisor[i], factor)
    return res


# --- Fehlerkorrektur-Kennwerte (Version 1–10) ------------------------------
# ecc -> version -> (ec_codewords_je_block, [(anzahl_bloecke, daten_je_block), …])
_ECC_TABLE = {
    "L": {
        1: (7, [(1, 19)]), 2: (10, [(1, 34)]), 3: (15, [(1, 55)]),
        4: (20, [(1, 80)]), 5: (26, [(1, 108)]), 6: (18, [(2, 68)]),
        7: (20, [(2, 78)]), 8: (24, [(2, 97)]), 9: (30, [(2, 116)]),
        10: (18, [(2, 68), (2, 69)]),
    },
    "M": {
        1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]),
        4: (18, [(2, 32)]), 5: (24, [(2, 43)]), 6: (16, [(4, 27)]),
        7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]),
        9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)]),
    },
    "Q": {
        1: (13, [(1, 13)]), 2: (22, [(1, 22)]), 3: (18, [(2, 17)]),
        4: (26, [(2, 24)]), 5: (18, [(2, 15), (2, 16)]), 6: (24, [(4, 19)]),
        7: (18, [(2, 14), (4, 15)]), 8: (22, [(4, 18), (2, 19)]),
        9: (20, [(4, 16), (4, 17)]), 10: (24, [(6, 19), (2, 20)]),
    },
    "H": {
        1: (17, [(1, 9)]), 2: (28, [(1, 16)]), 3: (22, [(2, 13)]),
        4: (16, [(4, 9)]), 5: (22, [(2, 11), (2, 12)]), 6: (28, [(4, 15)]),
        7: (26, [(4, 13), (1, 14)]), 8: (26, [(4, 14), (2, 15)]),
        9: (24, [(4, 12), (4, 13)]), 10: (28, [(6, 15), (2, 16)]),
    },
}

# Zentren der Ausrichtungsmuster je Version
_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

_ECC_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}


def _data_capacity(version: int, ecc: str) -> int:
    _, blocks = _ECC_TABLE[ecc][version]
    return sum(cnt * dc for cnt, dc in blocks)


def _choose_version(nbytes: int, ecc: str) -> int:
    for v in range(1, 11):
        cc = 8 if v < 10 else 16
        needed = 4 + cc + 8 * nbytes           # Bits
        cap = _data_capacity(v, ecc) * 8
        # Terminator/Padding passen, solange die reinen Datenbits reinpassen
        if needed <= cap:
            return v
    raise ValueError("Text zu lang für QR-Version ≤ 10")


# --- Bitstrom (Daten + Fehlerkorrektur, interleaved) -----------------------
def _make_codewords(data: bytes, version: int, ecc: str) -> List[int]:
    cc = 8 if version < 10 else 16
    bits: List[int] = []

    def put(value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)                 # Byte-Modus
    put(len(data), cc)             # Zeichenanzahl
    for b in data:
        put(b, 8)

    cap_bits = _data_capacity(version, ecc) * 8
    put(0, min(4, cap_bits - len(bits)))       # Terminator
    # Padding-Bits bis zur Byte-Grenze — wie ISO/segno: 8 - (len % 8). Bei
    # bereits ausgerichtetem Strom ergibt das ein volles 0x00-Byte.
    bits.extend([0] * (8 - len(bits) % 8))
    bits = bits[:cap_bits]                      # nie über die Datenkapazität
    codewords = [int("".join(str(x) for x in bits[i:i + 8]), 2)
                 for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    i = 0
    while len(codewords) < _data_capacity(version, ecc):
        codewords.append(pad[i % 2])
        i += 1

    # In Blöcke aufteilen, ECC je Block, dann interleaven
    ec_per_block, blocks = _ECC_TABLE[ecc][version]
    data_blocks: List[List[int]] = []
    ec_blocks: List[List[int]] = []
    pos = 0
    for cnt, dc in blocks:
        for _ in range(cnt):
            blk = codewords[pos:pos + dc]
            pos += dc
            data_blocks.append(blk)
            ec_blocks.append(_rs_encode(blk, ec_per_block))

    result: List[int] = []
    maxlen = max(len(b) for b in data_blocks)
    for i in range(maxlen):
        for blk in data_blocks:
            if i < len(blk):
                result.append(blk[i])
    for i in range(ec_per_block):
        for blk in ec_blocks:
            result.append(blk[i])
    return result


# --- Matrix-Aufbau ----------------------------------------------------------
def _new_matrix(version: int) -> Tuple[List[List[Optional[int]]], List[List[bool]], int]:
    size = version * 4 + 17
    m: List[List[Optional[int]]] = [[None] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]
    return m, reserved, size


def _place_finder(m, reserved, size, r0, c0) -> None:
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            r, c = r0 + dr, c0 + dc
            if not (0 <= r < size and 0 <= c < size):
                continue
            reserved[r][c] = True
            if 0 <= dr <= 6 and 0 <= dc <= 6:
                edge = dr in (0, 6) or dc in (0, 6)
                inner = 2 <= dr <= 4 and 2 <= dc <= 4
                m[r][c] = 1 if (edge or inner) else 0
            else:
                m[r][c] = 0          # Separator


def _place_alignment(m, reserved, size, version) -> None:
    centers = _ALIGN[version]
    for r in centers:
        for c in centers:
            # nicht über die drei Finder legen
            if (r <= 8 and c <= 8) or (r <= 8 and c >= size - 9) or \
               (r >= size - 9 and c <= 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    rr, cc = r + dr, c + dc
                    reserved[rr][cc] = True
                    ring = max(abs(dr), abs(dc))
                    m[rr][cc] = 1 if ring != 1 else 0


def _place_timing(m, reserved, size) -> None:
    for i in range(8, size - 8):
        val = 1 if i % 2 == 0 else 0
        if not reserved[6][i]:
            m[6][i] = val
            reserved[6][i] = True
        if not reserved[i][6]:
            m[i][6] = val
            reserved[i][6] = True


def _reserve_format(reserved, size) -> None:
    for i in range(9):
        reserved[8][i] = True
        reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = True
        reserved[size - 1 - i][8] = True
    reserved[size - 8][8] = True          # Dunkelmodul (immer 1)


def _reserve_version(reserved, size, version) -> None:
    if version < 7:
        return
    for i in range(6):
        for j in range(3):
            reserved[i][size - 11 + j] = True
            reserved[size - 11 + j][i] = True


def _place_data(m, reserved, size, codewords) -> None:
    bits: List[int] = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1                       # vertikale Timing-Spalte überspringen
        cols = [col, col - 1]
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in cols:
                if reserved[r][c]:
                    continue
                m[r][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        upward = not upward
        col -= 2


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _apply_mask(m, reserved, size, mask) -> List[List[int]]:
    fn = _MASKS[mask]
    out = [[int(m[r][c] or 0) for c in range(size)] for r in range(size)]
    for r in range(size):
        for c in range(size):
            if not reserved[r][c] and fn(r, c):
                out[r][c] ^= 1
    return out


def _format_bits(ecc: str, mask: int) -> List[int]:
    data = (_ECC_BITS[ecc] << 3) | mask
    rem = data << 10
    gen = 0b10100110111
    for i in range(14, 9, -1):
        if rem & (1 << i):
            rem ^= gen << (i - 10)
    bits = ((data << 10) | rem) ^ 0b101010000010010
    return [(bits >> i) & 1 for i in range(14, -1, -1)]


def _place_format(mat, size, ecc, mask) -> None:
    bits = _format_bits(ecc, mask)
    # Kopie 1: um die obere linke Ecke
    coords1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
               (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    for bit, (r, c) in zip(bits, coords1):
        mat[r][c] = bit
    # Kopie 2: unten links + oben rechts
    coords2 = [(size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8),
               (size - 5, 8), (size - 6, 8), (size - 7, 8),
               (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5),
               (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1)]
    for bit, (r, c) in zip(bits, coords2):
        mat[r][c] = bit
    mat[size - 8][8] = 1                    # Dunkelmodul


def _version_bits(version: int) -> List[int]:
    rem = version << 12
    gen = 0b1111100100101
    for i in range(17, 11, -1):
        if rem & (1 << i):
            rem ^= gen << (i - 12)
    bits = (version << 12) | rem
    return [(bits >> i) & 1 for i in range(17, -1, -1)]


def _place_version(mat, size, version) -> None:
    if version < 7:
        return
    bits = _version_bits(version)
    k = 0
    for i in range(6):
        for j in range(3):
            b = bits[17 - k]
            mat[i][size - 11 + j] = b
            mat[size - 11 + j][i] = b
            k += 1


def _penalty(mat, size) -> int:
    """Strafwertung nach ISO/IEC 18004 (wortgetreu wie die Referenz)."""
    pat = bytes((1, 0, 1, 1, 1, 0, 1))            # 1:1:3:1:1-Muster

    def n3_occ(seq: bytes) -> int:
        count = 0
        idx = seq.find(pat)
        while idx != -1:
            offset = idx + 7
            if idx in (0, size - 7) \
                    or not any(seq[max(idx - 4, 0):min(idx, size)]) \
                    or not any(seq[max(offset, 0):min(offset + 4, size)]):
                count += 40
            else:
                offset = idx + 4
            idx = seq.find(pat, offset)
        return count

    rows = [bytes(r) for r in mat]
    cols = [bytes(mat[r][c] for r in range(size)) for c in range(size)]
    n1 = n2 = n3 = 0
    dark = 0
    last_row = None
    for i in range(size):
        row = mat[i]
        rprev = cprev = -1
        rc = cc = 0
        for j in range(size):
            rb = row[j]
            cb = mat[j][i]
            dark += rb
            if rb == rprev:
                rc += 1
            else:
                if rc >= 5:
                    n1 += rc - 2
                rc = 1
            if cb == cprev:
                cc += 1
            else:
                if cc >= 5:
                    n1 += cc - 2
                cc = 1
            if last_row is not None and j and rb == rprev == last_row[j] == last_row[j - 1]:
                n2 += 3
            rprev, cprev = rb, cb
        last_row = row
        n3 += n3_occ(rows[i])
        n3 += n3_occ(cols[i])
        if rc >= 5:
            n1 += rc - 2
        if cc >= 5:
            n1 += cc - 2
    percent = dark / (size * size)
    n4 = 10 * int(abs(percent * 100 - 50) / 5)
    return n1 + n2 + n3 + n4


def encode(data, error: str = "M", version: Optional[int] = None,
           mask: Optional[int] = None) -> List[List[int]]:
    """Kodiert ``data`` (str/bytes) und gibt die Modulmatrix (0/1) zurück."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    ecc = error.upper()
    if version is None:
        version = _choose_version(len(data), ecc)
    codewords = _make_codewords(data, version, ecc)

    base_m, reserved, size = _new_matrix(version)
    _place_finder(base_m, reserved, size, 0, 0)
    _place_finder(base_m, reserved, size, 0, size - 7)
    _place_finder(base_m, reserved, size, size - 7, 0)
    _place_alignment(base_m, reserved, size, version)
    _place_timing(base_m, reserved, size)
    _reserve_format(reserved, size)
    _reserve_version(reserved, size, version)
    _place_data(base_m, reserved, size, codewords)

    candidates = [mask] if mask is not None else range(8)
    best = None
    for mk in candidates:
        mat = _apply_mask(base_m, reserved, size, mk)
        _place_format(mat, size, ecc, mk)
        _place_version(mat, size, version)
        pen = _penalty(mat, size)
        if best is None or pen < best[0]:
            best = (pen, mat)
    return best[1]
