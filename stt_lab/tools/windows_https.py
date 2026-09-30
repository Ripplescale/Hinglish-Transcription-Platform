"""Use Windows' trusted HTTPS issuers for setup processes without disabling TLS."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import ssl
import tempfile


@contextmanager
def verified_windows_https():
    """A temporary public CA bundle, scoped to this process and its children.

    Requests uses certifi by default and can miss organization-managed issuers
    already trusted by Windows. Keep certifi and add Windows certificates that
    permit server authentication. Never export private keys or change the system
    trust store. Existing CA overrides are retained and environment is restored.
    """
    if os.name != "nt":
        yield {"method": "existing-platform-trust", "certificate_verification": True}
        return
    pieces = []
    try:
        import certifi
        pieces.append(Path(certifi.where()).read_text(encoding="ascii"))
    except ImportError:
        pass
    names = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HF_HUB_DISABLE_XET")
    previous = {name: os.environ.get(name) for name in names}
    for name in names[:3]:
        if previous[name]:
            pieces.append(Path(previous[name]).read_text(encoding="ascii"))
    seen = set()
    for store in ("ROOT", "CA"):
        for certificate, encoding, trust in ssl.enum_certificates(store):
            if encoding == "x509_asn" and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust):
                seen.add(certificate)
    pieces.extend(ssl.DER_cert_to_PEM_cert(certificate) for certificate in sorted(seen))
    if not seen:
        raise RuntimeError("No Windows HTTPS trust certificates were available")
    with tempfile.TemporaryDirectory(prefix="sttapp-https-") as folder:
        bundle = Path(folder) / "verified-windows-ca.pem"
        bundle.write_text("\n".join(pieces), encoding="ascii")
        try:
            for name in names[:3]:
                os.environ[name] = str(bundle)
            # Keep download TLS in the Python HTTP stack that uses this bundle.
            # This does not change model contents or credentials.
            os.environ["HF_HUB_DISABLE_XET"] = "1"
            yield {"method": "windows-store-plus-certifi", "windows_certificates": len(seen),
                   "certificate_verification": True}
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def check_huggingface_https():
    """No authentication lookup, token or Authorization header is used."""
    import json
    import urllib.error
    import urllib.request
    with verified_windows_https() as trust:
        request = urllib.request.Request("https://huggingface.co/api/whoami-v2", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        if status != 401:
            raise RuntimeError(f"Unexpected unauthenticated Hugging Face response: HTTP {status}")
        print(json.dumps({**trust, "https_verified": True, "http_status": status,
                          "authorization_sent": False}))


if __name__ == "__main__":
    import argparse
    import runpy
    import sys
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-https", action="store_true")
    parser.add_argument("--login", action="store_true")
    arguments = parser.parse_args()
    if arguments.check_https:
        check_huggingface_https()
    elif arguments.login:
        # Import the Hugging Face CLI after the scoped CA environment is ready.
        with verified_windows_https():
            sys.argv = ["hf", "auth", "login"]
            runpy.run_module("huggingface_hub.cli.hf", run_name="__main__")
    else:
        parser.error("Choose --check-https or --login")
