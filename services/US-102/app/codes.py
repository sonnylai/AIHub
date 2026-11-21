import re
import secrets
import string
from collections.abc import Callable

ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 7
CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{7}$")
RESERVED_SEGMENTS = frozenset({"links", "health", "docs", "redoc", "openapi.json"})


def generate_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def is_valid_code(code: str) -> bool:
    return CODE_PATTERN.fullmatch(code) is not None


def is_reserved(code: str) -> bool:
    return code.lower() in RESERVED_SEGMENTS


def generate_unique_code(
    is_taken: Callable[[str], bool],
    generator: Callable[[], str] = generate_code,
) -> str:
    while True:
        candidate = generator()
        if is_valid_code(candidate) and not is_reserved(candidate) and not is_taken(candidate):
            return candidate
