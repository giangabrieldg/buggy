from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

from personal_assistant.config import AppEntry


@dataclass(frozen=True)
class ActionIntent:
    action: str
    value: str


@dataclass(frozen=True)
class ActionOutcome:
    action: str
    value: str
    success: bool
    message: str
    request_id: int | None = None


class CommandRouter:
    """Recognize only clear, complete local actions from user-entered text."""

    def __init__(self, allowed_apps: Sequence[AppEntry]):
        self._apps_by_name = {entry.name.casefold(): entry.name for entry in allowed_apps}

    def route(self, text: str) -> ActionIntent | None:
        if not isinstance(text, str):
            return None
        normalized = text.strip()
        if not normalized:
            return None

        search_match = re.fullmatch(r"(?:find|search)\s+(.+?)\s*", normalized, re.IGNORECASE)
        if search_match:
            query = search_match.group(1).strip()
            query = re.sub(
                r"\b(?:my|the|a|an|file|files|document|documents|for)\b",
                " ",
                query,
                flags=re.IGNORECASE,
            )
            query = " ".join(query.split())
            if query:
                return ActionIntent("search", query)

        app_match = re.fullmatch(r"(?:open|launch)\s+(.+?)\s*", normalized, re.IGNORECASE)
        if not app_match:
            return None
        requested_name = app_match.group(1).strip()
        if not requested_name:
            return None

        canonical_name = self._apps_by_name.get(requested_name.casefold())
        if canonical_name is not None:
            return ActionIntent("launch", canonical_name)
        name_parts = requested_name.split()
        if len(name_parts) == 1 or all(part[0].isupper() for part in name_parts):
            return ActionIntent("unknown_app", requested_name)
        return None


class CommandDispatcher:
    """Dispatch parsed local actions through their narrow adapters."""

    def __init__(self, router: CommandRouter, app_launcher, search_controller):
        self.router = router
        self.app_launcher = app_launcher
        self.search_controller = search_controller

    def dispatch(self, text: str) -> ActionOutcome | None:
        intent = self.router.route(text)
        if intent is None:
            return None

        if intent.action == "unknown_app":
            return ActionOutcome(
                action=intent.action,
                value=intent.value,
                success=False,
                message=(
                    f"{intent.value} is not in the approved app list. "
                    "Choose one of the configured apps instead."
                ),
            )

        if intent.action == "search":
            try:
                request_id = self.search_controller.search(intent.value)
            except Exception as error:
                return ActionOutcome(
                    action=intent.action,
                    value=intent.value,
                    success=False,
                    message=f"I couldn't start that file search: {error}",
                )
            return ActionOutcome(
                action=intent.action,
                value=intent.value,
                success=True,
                message=f'Searching your files for "{intent.value}"…',
                request_id=request_id,
            )

        try:
            result = self.app_launcher.launch(intent.value)
        except Exception as error:
            return ActionOutcome(
                action=intent.action,
                value=intent.value,
                success=False,
                message=f"I couldn't launch {intent.value}: {error}",
            )
        return ActionOutcome(
            action=intent.action,
            value=intent.value,
            success=result.success,
            message=result.message,
        )
