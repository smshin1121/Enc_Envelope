"""PAdES PDF digital signature using pyHanko 0.34+.

Signs PDF files using IncrementalPdfFileWriter for non-destructive
modification, with optional TSA timestamp token embedding.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .exceptions import PDFSigningError
from .types import SignatureVerificationResult

logger = logging.getLogger(__name__)


def sign_pdf(
    pdf_path: str | Path,
    cert_path: str | Path,
    key_path: str | Path,
    password: str,
    output_path: str | Path,
    tsa_url: str | None = None,
) -> str:
    """Sign a PDF file with a PAdES signature.

    Args:
        pdf_path: Path to the input PDF file.
        cert_path: Path to the signer's certificate PEM file.
        key_path: Path to the signer's private key PEM file.
        password: Password to decrypt the private key.
        output_path: Path for the signed output PDF.
        tsa_url: Optional TSA server URL for timestamp embedding.

    Returns:
        Empty string on success, or warning message if TSA skipped.

    Raises:
        PDFSigningError: If signing fails.
    """
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigSeedSubFilter

    pdf_file = Path(pdf_path)
    cert_file = Path(cert_path)
    key_file = Path(key_path)
    out_file = Path(output_path)

    if not pdf_file.exists():
        raise PDFSigningError(f"PDF file not found: {pdf_file}")
    if not cert_file.exists():
        raise PDFSigningError(f"Certificate file not found: {cert_file}")
    if not key_file.exists():
        raise PDFSigningError(f"Key file not found: {key_file}")
    if not password:
        raise PDFSigningError("Private key password is required")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    warning_msg = ""

    try:
        # Load signer credentials
        signer = signers.SimpleSigner.load(
            key_file=str(key_file),
            cert_file=str(cert_file),
            key_passphrase=password.encode("utf-8"),
        )

        # Configure timestamper
        timestamper = None
        if tsa_url:
            try:
                from pyhanko.sign.timestamps import HTTPTimeStamper
                timestamper = HTTPTimeStamper(tsa_url)
            except Exception as exc:
                warning_msg = f"TSA 설정 실패 (서명만 적용): {exc}"
                logger.warning(warning_msg)

        # PAdES signature metadata
        sig_metadata = signers.PdfSignatureMetadata(
            field_name="Signature1",
            md_algorithm="sha256",
            subfilter=SigSeedSubFilter.PADES,
        )

        # Sign
        pdf_signer = signers.PdfSigner(
            signature_meta=sig_metadata,
            signer=signer,
            timestamper=timestamper,
        )

        with open(pdf_file, "rb") as f_in:
            writer = IncrementalPdfFileWriter(f_in)
            with open(out_file, "wb") as f_out:
                pdf_signer.sign_pdf(writer, output=f_out)

        logger.info("PDF signed successfully: %s", out_file)
        return warning_msg

    except PDFSigningError:
        raise
    except Exception as exc:
        # If TSA failed during signing, retry without
        if timestamper is not None:
            logger.warning("Signing with TSA failed, retrying without: %s", exc)
            return _sign_without_tsa(pdf_file, cert_file, key_file, password, out_file, str(exc))
        raise PDFSigningError(f"Failed to sign PDF: {exc}") from exc


def _sign_without_tsa(
    pdf_file: Path,
    cert_file: Path,
    key_file: Path,
    password: str,
    out_file: Path,
    original_error: str,
) -> str:
    """Fallback: sign PDF without TSA timestamp."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigSeedSubFilter

    try:
        signer = signers.SimpleSigner.load(
            key_file=str(key_file),
            cert_file=str(cert_file),
            key_passphrase=password.encode("utf-8"),
        )
        sig_metadata = signers.PdfSignatureMetadata(
            field_name="Signature1",
            md_algorithm="sha256",
            subfilter=SigSeedSubFilter.PADES,
        )
        pdf_signer = signers.PdfSigner(
            signature_meta=sig_metadata,
            signer=signer,
            timestamper=None,
        )
        with open(pdf_file, "rb") as f_in:
            writer = IncrementalPdfFileWriter(f_in)
            with open(out_file, "wb") as f_out:
                pdf_signer.sign_pdf(writer, output=f_out)

        warning = f"TSA 미적용 (서명만 적용). 원인: {original_error}"
        logger.warning(warning)
        return warning
    except Exception as exc:
        raise PDFSigningError(f"Failed to sign PDF without TSA: {exc}") from exc


def verify_pdf_signature(pdf_path: str | Path) -> SignatureVerificationResult:
    """Verify digital signatures in a PDF file."""
    from pyhanko.pdf_utils.reader import PdfFileReader

    filepath = Path(pdf_path)
    if not filepath.exists():
        raise PDFSigningError(f"PDF file not found: {filepath}")

    try:
        with open(filepath, "rb") as f:
            reader = PdfFileReader(f)
            sigs = reader.embedded_signatures

            if not sigs:
                return SignatureVerificationResult(
                    valid=False,
                    signer_name="",
                    errors=("No signatures found in PDF",),
                )

            sig = sigs[0]
            from pyhanko.sign.validation import validate_pdf_signature
            status = validate_pdf_signature(sig)

            signer_name = ""
            try:
                cert = status.signing_cert
                if cert is not None:
                    signer_name = cert.subject.human_friendly
            except Exception:
                signer_name = "(unknown)"

            has_timestamp = False
            timestamp_time = None
            try:
                if status.timestamp_validity is not None:
                    has_timestamp = True
                    if hasattr(status.timestamp_validity, "timestamp"):
                        timestamp_time = str(status.timestamp_validity.timestamp)
            except Exception:
                pass

            errors: list[str] = []
            if not status.bottom_line:
                errors.append("Signature validation failed")

            return SignatureVerificationResult(
                valid=status.bottom_line,
                signer_name=signer_name,
                signing_time=None,
                has_timestamp=has_timestamp,
                timestamp_time=timestamp_time,
                errors=tuple(errors),
                warnings=(),
            )
    except PDFSigningError:
        raise
    except Exception as exc:
        raise PDFSigningError(f"Failed to verify PDF signature: {exc}") from exc
