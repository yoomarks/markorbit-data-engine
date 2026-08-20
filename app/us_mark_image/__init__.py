"""Task-driven USPTO mark-image acquisition for the US domain."""

SOURCE_VERSION = "US_MARK_IMAGE_V1"
DEFAULT_REQUEST_INTERVAL_SECONDS = 2.5
DEFAULT_QUEUE_TARGET = 100_000
DEFAULT_QUEUE_FLOOR = 20_000
MARK_IMAGE_TEMPLATE = "https://tsdr.uspto.gov/img/{serial_number}/large"


def mark_image_url(serial_number: object) -> str:
    serial = str(serial_number or "").strip()
    if len(serial) != 8 or not serial.isdigit():
        raise ValueError(f"US serial_number must contain exactly 8 digits: {serial!r}")
    return MARK_IMAGE_TEMPLATE.format(serial_number=serial)


__all__ = [
    "SOURCE_VERSION",
    "DEFAULT_REQUEST_INTERVAL_SECONDS",
    "DEFAULT_QUEUE_TARGET",
    "DEFAULT_QUEUE_FLOOR",
    "MARK_IMAGE_TEMPLATE",
    "mark_image_url",
]
