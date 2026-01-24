"""Zeroconf authentication for Spotify Connect devices."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# Spotify's DH prime (from librespot)
DH_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE65381"
    "FFFFFFFFFFFFFFFF",
    16
)
DH_GENERATOR = 2


def generate_dh_keys() -> tuple[int, int]:
    """Generate Diffie-Hellman key pair."""
    # Generate random private key (256 bits)
    private_key = int.from_bytes(os.urandom(32), 'big')
    # Calculate public key: g^private mod p
    public_key = pow(DH_GENERATOR, private_key, DH_PRIME)
    return private_key, public_key


def compute_shared_secret(private_key: int, peer_public_key: int) -> bytes:
    """Compute DH shared secret."""
    shared_secret = pow(peer_public_key, private_key, DH_PRIME)
    # Calculate the byte length needed (DH prime is 768-bit = 96 bytes)
    byte_length = (shared_secret.bit_length() + 7) // 8
    byte_length = max(byte_length, 96)  # Ensure at least 96 bytes
    return shared_secret.to_bytes(byte_length, 'big')


def create_encrypted_blob(shared_secret: bytes, credentials: dict[str, Any]) -> bytes:
    """Create encrypted blob for Spotify Connect authentication.
    
    The blob format is:
    - IV (16 bytes)
    - Encrypted data (variable)
    - HMAC-SHA1 (20 bytes)
    
    The encrypted data is the raw credentials blob that the device will use
    to authenticate with Spotify.
    """
    # Derive keys from shared secret
    base_key = hashlib.sha1(shared_secret).digest()
    checksum_key = hmac.new(base_key, b"checksum", hashlib.sha1).digest()
    encryption_key = hmac.new(base_key, b"encryption", hashlib.sha1).digest()[:16]
    
    # The blob is simply the raw auth_data from librespot credentials
    # This is what Spotify gave us and what the device needs to authenticate
    blob_data = base64.b64decode(credentials["auth_data"])
    
    # Generate random IV
    iv = os.urandom(16)
    
    # Encrypt with AES-128-CTR
    cipher = Cipher(algorithms.AES(encryption_key), modes.CTR(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(blob_data) + encryptor.finalize()
    
    # Calculate HMAC
    mac = hmac.new(checksum_key, encrypted, hashlib.sha1).digest()
    
    # Combine: IV + encrypted + MAC
    return iv + encrypted + mac


def decode_device_public_key(public_key_b64: str) -> int:
    """Decode device's DH public key from base64."""
    key_bytes = base64.b64decode(public_key_b64)
    # The key can be variable length, just convert whatever we get
    return int.from_bytes(key_bytes, 'big')


def encode_public_key(public_key: int) -> str:
    """Encode our DH public key to base64."""
    # Calculate the byte length needed
    byte_length = (public_key.bit_length() + 7) // 8
    # Ensure at least 96 bytes for compatibility
    byte_length = max(byte_length, 96)
    key_bytes = public_key.to_bytes(byte_length, 'big')
    return base64.b64encode(key_bytes).decode('utf-8')


async def authenticate_with_device(
    session,
    device_url: str,
    credentials: dict[str, Any],
    device_info: dict[str, Any],
    logger=None
) -> bool:
    """Authenticate with a Spotify Connect device using Zeroconf.
    
    Args:
        session: aiohttp ClientSession
        device_url: Base URL of the device (e.g., http://10.0.0.119:5389/zc)
        credentials: Librespot credentials dict with username, auth_type, auth_data
        device_info: Device info from getInfo response
        logger: Optional logger for debugging
        
    Returns:
        True if authentication succeeded
    """
    import aiohttp
    
    try:
        # Get device's public key
        device_public_key_b64 = device_info.get("publicKey")
        if not device_public_key_b64:
            if logger:
                logger.warning("No publicKey in device info")
            return False
        
        if logger:
            logger.debug("Device public key length: %d", len(device_public_key_b64))
        
        device_public_key = decode_device_public_key(device_public_key_b64)
        
        # Generate our DH key pair
        private_key, public_key = generate_dh_keys()
        
        # Compute shared secret
        shared_secret = compute_shared_secret(private_key, device_public_key)
        
        if logger:
            logger.debug("Shared secret computed, length: %d", len(shared_secret))
        
        # Create encrypted blob
        encrypted_blob = create_encrypted_blob(shared_secret, credentials)
        blob_b64 = base64.b64encode(encrypted_blob).decode('utf-8')
        
        # Encode our public key
        client_key_b64 = encode_public_key(public_key)
        
        if logger:
            logger.debug("Sending addUser with blob length: %d, clientKey length: %d", 
                len(blob_b64), len(client_key_b64))
        
        # Send addUser request
        data = aiohttp.FormData()
        data.add_field('action', 'addUser')
        data.add_field('userName', credentials["username"])
        data.add_field('blob', blob_b64)
        data.add_field('clientKey', client_key_b64)
        
        async with session.post(device_url, data=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
            result_text = await response.text()
            if logger:
                logger.info("addUser response: %s", result_text[:300])
            
            if response.status == 200:
                result = await response.json()
                if result.get("status") == 101:
                    return True
                else:
                    if logger:
                        logger.warning("addUser failed: status=%s, statusString=%s, spotifyError=%s",
                            result.get("status"), result.get("statusString"), result.get("spotifyError"))
                
    except Exception as e:
        if logger:
            logger.error("Zeroconf auth exception: %s", e)
    
    return False
