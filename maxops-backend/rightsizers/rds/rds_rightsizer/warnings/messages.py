"""Stable reason-code presentation metadata."""
from __future__ import annotations


def warning_details(codes: list[str]) -> list[dict[str, str]]:
    return [
        {"code": code, "message": code.lower().replace("_", " ").capitalize()}
        for code in dict.fromkeys(codes)
    ]
