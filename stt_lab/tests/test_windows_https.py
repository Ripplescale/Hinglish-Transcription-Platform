"""The login trust fix must remain scoped and respect certificate purposes."""
import importlib.util
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "tools" / "windows_https.py"
SPEC = importlib.util.spec_from_file_location("windows_https", TOOL)
https = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(https)


@unittest.skipUnless(os.name == "nt", "Windows certificate stores")
class WindowsHTTPSIsolationTests(unittest.TestCase):
    def test_only_server_trusted_public_certificates_are_included(self):
        certificates = [(b"server", "x509_asn", {ssl.Purpose.SERVER_AUTH.oid}),
                        (b"all", "x509_asn", True),
                        (b"client-only", "x509_asn", {ssl.Purpose.CLIENT_AUTH.oid}),
                        (b"unsupported", "pkcs_7_asn", True)]
        clean = {key: value for key, value in os.environ.items() if key not in
                 ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HF_HUB_DISABLE_XET")}
        with patch.dict(os.environ, clean, clear=True), patch.object(ssl, "enum_certificates", return_value=certificates):
            with https.verified_windows_https() as result:
                bundle = Path(os.environ["SSL_CERT_FILE"])
                text = bundle.read_text()
                self.assertIn(ssl.DER_cert_to_PEM_cert(b"server"), text)
                self.assertNotIn(ssl.DER_cert_to_PEM_cert(b"client-only"), text)
                self.assertEqual(result["windows_certificates"], 2)
                self.assertTrue(result["certificate_verification"])
                self.assertEqual(os.environ["REQUESTS_CA_BUNDLE"], str(bundle))
            self.assertFalse(bundle.exists())
            self.assertNotIn("SSL_CERT_FILE", os.environ)
            self.assertNotIn("HF_HUB_DISABLE_XET", os.environ)

    def test_existing_overrides_restore_even_after_login_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            custom = Path(folder) / "existing.pem"
            custom.write_text("existing public CA bundle")
            previous = {"SSL_CERT_FILE": str(custom), "REQUESTS_CA_BUNDLE": str(custom),
                        "CURL_CA_BUNDLE": str(custom), "HF_HUB_DISABLE_XET": "0"}
            with patch.dict(os.environ, previous), patch.object(ssl, "enum_certificates", return_value=[(b"server", "x509_asn", True)]):
                with self.assertRaisesRegex(RuntimeError, "simulated login failure"):
                    with https.verified_windows_https():
                        bundle = Path(os.environ["SSL_CERT_FILE"])
                        self.assertIn("existing public CA bundle", bundle.read_text())
                        raise RuntimeError("simulated login failure")
                self.assertFalse(bundle.exists())
                self.assertEqual({key: os.environ[key] for key in previous}, previous)
                self.assertTrue(custom.exists())


if __name__ == "__main__":
    unittest.main()
