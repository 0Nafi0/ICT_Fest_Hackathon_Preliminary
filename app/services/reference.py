"""Human-facing booking reference codes.

Codes are generated with enough entropy to stay unique under concurrency while
remaining customer-friendly.
"""
import uuid

def next_reference_code() -> str:
    return f"CW-{uuid.uuid4().hex[:12].upper()}"
