"""Build a throwaway PKCS#12 bundle for broker-binding tests.

The bind command opens the pfx with its password to read the certificate expiry;
a self-signed cert generated at test time is enough — no fixture file, no real key.
"""

from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

DEFAULT_NOT_AFTER = datetime(2027, 3, 1, tzinfo=UTC)


def build_test_pfx(*, password: str, not_valid_after: datetime = DEFAULT_NOT_AFTER) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "broker-binding-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_valid_after - timedelta(days=365))
        .not_valid_after(not_valid_after)
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        name=b"test",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    )
