"""Tests für den QR-Encoder (reine Standardbibliothek).

Strukturtests laufen immer. Wenn `segno` bzw. `cv2` verfügbar sind, wird
zusätzlich bit-genau gegen segno und per echtem Decoder round-trip geprüft.
"""

import unittest

from saillog import qrcode


class StructureTest(unittest.TestCase):
    def test_size_and_finders(self):
        m = qrcode.encode("http://192.168.9.50:8770/", error="M")
        n = len(m)
        self.assertEqual(n % 4, 21 % 4)                 # gültige QR-Größe (4k+1)
        for row in m:
            self.assertEqual(len(row), n)
            self.assertTrue(all(v in (0, 1) for v in row))
        # Finder-Muster: dunkler 7er-Rand oben-links
        self.assertTrue(all(m[0][c] == 1 for c in range(7)))
        self.assertTrue(all(m[r][0] == 1 for r in range(7)))
        self.assertEqual(m[1][1], 0)                     # heller Ring
        self.assertEqual(m[3][3], 1)                     # dunkler 3x3-Kern

    def test_deterministic(self):
        a = qrcode.encode("SailLog", error="M")
        b = qrcode.encode("SailLog", error="M")
        self.assertEqual(a, b)

    def test_longer_data_uses_larger_version(self):
        small = len(qrcode.encode("abc", error="M"))
        big = len(qrcode.encode("x" * 120, error="M"))
        self.assertGreater(big, small)


try:
    import segno  # noqa: F401
    _HAVE_SEGNO = True
except Exception:  # noqa: BLE001
    _HAVE_SEGNO = False


@unittest.skipUnless(_HAVE_SEGNO, "segno nicht installiert")
class SegnoParityTest(unittest.TestCase):
    def test_bit_exact_forced(self):
        import segno
        data_set = ["http://192.168.9.50:8770/", "hello world", "SailLog",
                    "https://example.org/p?q=1", "x" * 40, "masarasi 2026"]
        checked = 0
        for data in data_set:
            for ecc in ("L", "M", "Q", "H"):
                ref = segno.make_qr(data, error=ecc, mode="byte", boost_error=False)
                if ref.version > 10:
                    continue
                theirs = [[int(b) for b in row] for row in ref.matrix]
                for mask in range(8):
                    mine = qrcode.encode(data, error=ecc, version=ref.version, mask=mask)
                    seg = [[int(b) for b in row] for row in
                           segno.make_qr(data, error=ecc, version=ref.version,
                                         mask=mask, mode="byte", boost_error=False).matrix]
                    self.assertEqual(mine, seg,
                                     f"{data!r} ecc={ecc} v={ref.version} mask={mask}")
                    checked += 1
        self.assertGreater(checked, 0)


try:
    import cv2  # noqa: F401
    import numpy as np  # noqa: F401
    _HAVE_CV2 = True
except Exception:  # noqa: BLE001
    _HAVE_CV2 = False


@unittest.skipUnless(_HAVE_CV2, "cv2/numpy nicht installiert")
class DecodeRoundTripTest(unittest.TestCase):
    def test_real_decoder(self):
        import cv2
        import numpy as np

        def render(matrix, scale=8, quiet=4):
            n = len(matrix)
            size = (n + 2 * quiet) * scale
            img = np.full((size, size), 255, np.uint8)
            for r in range(n):
                for c in range(n):
                    if matrix[r][c]:
                        y, x = (r + quiet) * scale, (c + quiet) * scale
                        img[y:y + scale, x:x + scale] = 0
            return img

        det = cv2.QRCodeDetector()
        for data in ["http://192.168.9.50:8770/", "http://10.10.10.1:8770/",
                     "hello world", "SailLog Fern-Erfassung"]:
            for ecc in ("L", "M", "Q", "H"):
                val, _pts, _ = det.detectAndDecode(render(qrcode.encode(data, error=ecc)))
                self.assertEqual(val, data, f"decode {data!r} ecc={ecc}")


if __name__ == "__main__":
    unittest.main()
