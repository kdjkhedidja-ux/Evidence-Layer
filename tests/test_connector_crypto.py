from __future__ import annotations

import base64
import unittest

from evidence_service.connector_crypto import TokenVault, TokenVaultUnavailable


class TokenVaultTests(unittest.TestCase):
    def setUp(self):
        self.key = base64.b64encode(b"a" * 32).decode("ascii")
        self.vault = TokenVault.from_base64(self.key)

    def test_round_trip(self):
        ciphertext = self.vault.encrypt("secret-token")
        self.assertIsNotNone(ciphertext)
        self.assertNotIn("secret-token", ciphertext)
        self.assertEqual(self.vault.decrypt(ciphertext), "secret-token")

    def test_none_passthrough(self):
        self.assertIsNone(self.vault.encrypt(None))
        self.assertIsNone(self.vault.decrypt(None))

    def test_tamper_fails_closed(self):
        ciphertext = self.vault.encrypt("secret-token")
        tampered = ciphertext[:-2] + ("AA" if ciphertext[-2:] != "AA" else "BB")
        with self.assertRaises(TokenVaultUnavailable):
            self.vault.decrypt(tampered)

    def test_invalid_key(self):
        with self.assertRaises(TokenVaultUnavailable):
            TokenVault.from_base64(base64.b64encode(b"short").decode("ascii"))
        with self.assertRaises(TokenVaultUnavailable):
            TokenVault.from_base64("not-base64")


if __name__ == "__main__":
    unittest.main()
