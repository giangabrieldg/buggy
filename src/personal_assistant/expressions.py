"""Visual-only mascot expression choices.

The rules intentionally require clear language and otherwise use the neutral face.
They never alter the conversation or persist an emotion label.
"""

from __future__ import annotations

import re


EXPRESSION_NAMES = (
    "neutral",
    "thinking",
    "happy",
    "curious",
    "caring",
    "concerned",
)


def _has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = " ".join(re.findall(r"[\w']+", text.casefold().replace("’", "'")))
    return any(f" {phrase} " in f" {normalized} " for phrase in phrases)


def tone_expression(user_text: str, reply_text: str) -> str:
    """Choose a restrained face for a completed reply, or neutral if unclear."""
    happy = _has_phrase(
        reply_text,
        (
            "congratulations", "congrats", "wonderful news", "that's wonderful",
            "great news", "that's exciting", "awesome news", "so happy for you",
            "sounds fun", "yay",
        ),
    )
    caring = _has_phrase(
        user_text,
        (
            "i feel sad", "i'm sad", "i am sad", "having a hard day",
            "i feel lonely", "i'm lonely", "i'm feeling down", "rough day",
            "i had a bad day", "i'm stressed",
        ),
    ) and _has_phrase(
        reply_text,
        (
            "i'm sorry", "i am sorry", "here to listen", "that sounds hard",
            "that sounds tough", "i'm here for you", "i'm listening",
            "you're not alone",
        ),
    )
    curious = _has_phrase(
        reply_text,
        (
            "interesting question", "great question", "good question",
            "let's explore", "let us explore", "let's find out", "i wonder",
        ),
    ) and ("?" in user_text or _has_phrase(user_text, ("why", "how", "what if")))

    matches = [name for name, matched in (("happy", happy), ("caring", caring), ("curious", curious)) if matched]
    return matches[0] if len(matches) == 1 else "neutral"


def choose_expression(activity: str, user_text: str = "", reply_text: str = "") -> str:
    """Apply app-state priority before the completed reply's tone."""
    if activity == "error":
        return "concerned"
    if activity == "thinking":
        return "thinking"
    if activity == "reply":
        return tone_expression(user_text, reply_text)
    return "neutral"
