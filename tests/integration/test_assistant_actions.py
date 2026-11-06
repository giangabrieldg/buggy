from __future__ import annotations

from dataclasses import dataclass

import pytest

from personal_assistant.config import AppEntry
from personal_assistant.commands import CommandDispatcher, CommandRouter


@dataclass(frozen=True)
class LaunchOutcome:
    success: bool = True
    message: str = "Opened the application."


class FakeAppLauncher:
    def __init__(self):
        self.calls = []

    def launch(self, name):
        self.calls.append(name)
        return LaunchOutcome()


class FakeSearchController:
    def __init__(self):
        self.queries = []

    def search(self, query):
        self.queries.append(query)


@pytest.fixture
def action_stack():
    launcher = FakeAppLauncher()
    search_controller = FakeSearchController()
    router = CommandRouter(
        [
            AppEntry("Notepad", "notepad.exe"),
            AppEntry("Calculator", "calc.exe"),
            AppEntry("Word", "winword.exe"),
        ]
    )
    dispatcher = CommandDispatcher(router, launcher, search_controller)
    return dispatcher, launcher, search_controller


def test_search_action_dispatches_query_and_returns_outcome(action_stack):
    dispatcher, launcher, search_controller = action_stack

    outcome = dispatcher.dispatch("find travel itinerary")

    assert search_controller.queries == ["travel itinerary"]
    assert launcher.calls == []
    assert outcome is not None


def test_allowed_app_action_calls_launcher(action_stack):
    dispatcher, launcher, search_controller = action_stack

    outcome = dispatcher.dispatch("launch Notepad")

    assert launcher.calls == ["Notepad"]
    assert search_controller.queries == []
    assert outcome is not None


def test_unknown_app_returns_friendly_outcome_without_windows_calls(action_stack):
    dispatcher, launcher, search_controller = action_stack

    outcome = dispatcher.dispatch("open Photoshop")

    assert launcher.calls == []
    assert search_controller.queries == []
    assert outcome.message
    assert "Photoshop" in outcome.message
    assert any(word in outcome.message.lower() for word in ("not", "unknown", "allow"))


def test_ordinary_chat_is_left_for_the_conversation_path(action_stack):
    dispatcher, launcher, search_controller = action_stack

    outcome = dispatcher.dispatch("I use Notepad to write my thoughts")

    assert outcome is None
    assert launcher.calls == []
    assert search_controller.queries == []
