# layers/kms_utils/python/kms_utils.py
"""
Shared KMS encryption/decryption utilities for stripe-cart Lambda functions.

This module provides consistent KMS encryption and decryption across all Lambda functions.
All functions use the encryption context: {"app": "stripe-cart"}

Usage:
    from kms_utils import kms_encrypt, kms_decrypt, kms_decrypt_wrapped
    
    # Encrypt a value
    encrypted = kms_encrypt(b"secret_value", kms_key_arn)
    # Returns: "ENCRYPTED(base64_ciphertext)"
    
    # Decrypt a value
    plaintext = kms_decrypt("ENCRYPTED(base64_ciphertext)", kms_key_arn)
    # Returns: b"secret_value"
    
    # Decrypt and return as string
    plaintext_str = kms_decrypt_wrapped("ENCRYPTED(base64_ciphertext)", kms_key_arn)
    # Returns: "secret_value"
"""

import os
import base64
import logging
from typing import Union, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Stable encryption context used across all KMS operations
ENCRYPTION_CONTEXT = {"app": "stripe-cart"}

# Cache KMS client
_kms_client = None


def _get_kms_client():
    """Get or create KMS client (cached)."""
    global _kms_client
    if _kms_client is None:
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
        _kms_client = boto3.client("kms", region_name=region)
    return _kms_client


def _unwrap_encrypted(value: str) -> str:
    """
    Remove ENCRYPTED() wrapper if present.
    
    Args:
        value: String that may be wrapped as ENCRYPTED(base64_ciphertext)
        
    Returns:
        Base64 ciphertext without wrapper, or original value if not wrapped
    """
    if isinstance(value, str) and value.startswith("ENCRYPTED(") and value.endswith(")"):
        return value[len("ENCRYPTED("):-1]
    return value


def kms_encrypt(plaintext: Union[str, bytes], kms_key_arn: Optional[str] = None) -> str:
    """
    Encrypt plaintext with KMS and return in ENCRYPTED(base64) format.
    
    Args:
        plaintext: The value to encrypt (string or bytes)
        kms_key_arn: KMS key ARN (defaults to STRIPE_KMS_KEY_ARN env var)
        
    Returns:
        Encrypted value in format: ENCRYPTED(base64_ciphertext)
        
    Raises:
        ValueError: If kms_key_arn not provided and not in environment
        ClientError: If KMS encryption fails
    """
    if kms_key_arn is None:
        kms_key_arn = os.environ.get("STRIPE_KMS_KEY_ARN")
        if not kms_key_arn:
            raise ValueError("kms_key_arn required: pass as argument or set STRIPE_KMS_KEY_ARN")
    
    # Convert string to bytes if needed
    if isinstance(plaintext, str):
        plaintext_bytes = plaintext.encode("utf-8")
    else:
        plaintext_bytes = plaintext
    
    kms = _get_kms_client()
    
    try:
        logger.info(f"[KMS] Encrypting value with key: {kms_key_arn[:50]}...")
        response = kms.encrypt(
            KeyId=kms_key_arn,
            Plaintext=plaintext_bytes,
            EncryptionContext=ENCRYPTION_CONTEXT
        )
        
        # Base64 encode the ciphertext
        ciphertext_base64 = base64.b64encode(response["CiphertextBlob"]).decode("utf-8")
        
        # Wrap in ENCRYPTED() format
        wrapped = f"ENCRYPTED({ciphertext_base64})"
        
        logger.info(f"[KMS] Successfully encrypted value (output length: {len(wrapped)})")
        return wrapped
        
    except ClientError as e:
        logger.error(f"[KMS] Encryption failed: {e}")
        raise


def kms_decrypt(ciphertext_wrapped: str, kms_key_arn: Optional[str] = None) -> bytes:
    """
    Decrypt KMS-encrypted value and return as bytes.
    
    Args:
        ciphertext_wrapped: Encrypted value (ENCRYPTED(base64) format or raw base64)
        kms_key_arn: KMS key ARN (optional, KMS can determine from ciphertext)
        
    Returns:
        Decrypted plaintext as bytes
        
    Raises:
        ValueError: If ciphertext format is invalid
        ClientError: If KMS decryption fails
    """
    if not ciphertext_wrapped:
        raise ValueError("Cannot decrypt empty/None value")
    
    # Remove ENCRYPTED() wrapper if present
    ciphertext_base64 = _unwrap_encrypted(ciphertext_wrapped)
    
    try:
        # Decode base64 to get ciphertext blob
        ciphertext_blob = base64.b64decode(ciphertext_base64)
        logger.info(f"[KMS] Decoded ciphertext blob ({len(ciphertext_blob)} bytes)")
    except Exception as e:
        raise ValueError(f"Invalid base64 ciphertext: {e}")
    
    kms = _get_kms_client()
    
    try:
        logger.info(f"[KMS] Decrypting value with context: {ENCRYPTION_CONTEXT}")
        
        # Build decrypt params
        decrypt_params = {
            "CiphertextBlob": ciphertext_blob,
            "EncryptionContext": ENCRYPTION_CONTEXT
        }
        
        # Add KeyId if provided (optional but can help with performance)
        if kms_key_arn:
            decrypt_params["KeyId"] = kms_key_arn
        
        response = kms.decrypt(**decrypt_params)
        plaintext = response["Plaintext"]
        
        logger.info(f"[KMS] Successfully decrypted value ({len(plaintext)} bytes)")
        return plaintext
        
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        logger.error(f"[KMS] Decryption failed: {error_code} - {error_msg}")
        logger.error(f"[KMS] Encryption context used: {ENCRYPTION_CONTEXT}")
        raise


def kms_decrypt_wrapped(blob: str, kms_key_arn: Optional[str] = None) -> str:
    """
    Decrypt KMS-encrypted value and return as UTF-8 string.
    If the value is not wrapped with ENCRYPTED(), returns it as-is (plaintext passthrough).
    
    Args:
        blob: Encrypted value (ENCRYPTED(base64) format) or plaintext
        kms_key_arn: KMS key ARN (optional)
        
    Returns:
        Decrypted plaintext as string, or original value if not encrypted
        
    Raises:
        ValueError: If decryption fails
    """
    if not blob:
        logger.warning("[KMS] decrypt_wrapped called with empty/None value")
        return ""
    
    # If not wrapped with ENCRYPTED(), treat as plaintext
    if not (blob.startswith("ENCRYPTED(") and blob.endswith(")")):
        logger.info(f"[KMS] Value not wrapped - treating as plaintext (length: {len(blob)})")
        return blob
    
    logger.info(f"[KMS] Value is wrapped - decrypting (length: {len(blob)})")
    
    try:
        plaintext_bytes = kms_decrypt(blob, kms_key_arn)
        plaintext_str = plaintext_bytes.decode("utf-8")
        logger.info(f"[KMS] Successfully decrypted and decoded to UTF-8 (length: {len(plaintext_str)})")
        return plaintext_str
    except Exception as e:
        logger.error(f"[KMS] Decryption failed: {e}")
        raise ValueError(f"Failed to decrypt wrapped value: {e}")


def mask_secret(secret: str, keep: int = 4) -> str:
    """
    Mask a secret value for logging/display.
    
    Args:
        secret: The secret to mask
        keep: Number of characters to keep visible at the end
        
    Returns:
        Masked string like "***abc123"
    """
    if not secret:
        return ""
    if len(secret) <= keep:
        return "*" * len(secret)
    return "*" * (len(secret) - keep) + secret[-keep:]


# Backwards compatibility aliases
def _kms_decrypt_wrapped(blob: str) -> str:
    """Legacy function name - use kms_decrypt_wrapped instead."""
    return kms_decrypt_wrapped(blob)


__all__ = [
    "kms_encrypt",
    "kms_decrypt", 
    "kms_decrypt_wrapped",
    "mask_secret",
    "ENCRYPTION_CONTEXT",
]