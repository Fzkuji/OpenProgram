"""
Hash utilities for generating short identifiers.
"""
import hashlib


def short_hash(text: str) -> str:
    """
    Generate a short 8-character hash of the input text.
    
    Args:
        text: The text to hash
        
    Returns:
        An 8-character hexadecimal hash string
    """
    return hashlib.sha256(text.encode()).hexdigest()[:8]
