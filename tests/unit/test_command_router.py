from __future__ import annotations

import pytest

from personal_assistant.config import AppEntry
from personal_assistant.commands import CommandRouter


@pytest.fixture
def router():
    return CommandRouter(
        [
            AppEntry("Notepad", "notepad.exe"),
            AppEntry("Calculator", "calc.exe"),
            AppEntry("Word", "winword.exe"),
        ]
    )


def test_find_and_search_prefixes_extract_the_query(router):
    find_intent = router.route("find quarterly budget notes")
    search_intent = router.route("Search my project files for design notes")
    resume_intent = router.route("search for my resume")

    assert (find_intent.action, find_intent.value) == (
        "search",
        "quarterly budget notes",
    )
    assert (search_intent.action, search_intent.value) == (
        "search",
        "project design notes",
    )
    assert (resume_intent.action, resume_intent.value) == (
        "search",
        "resume",
    )


def test_open_and_launch_allowlisted_names_are_recognized(router):
    open_intent = router.route("open Notepad")
    launch_intent = router.route("Launch Word")

    assert (open_intent.action, open_intent.value) == ("launch", "Notepad")
    assert (launch_intent.action, launch_intent.value) == ("launch", "Word")


def test_unknown_open_target_is_a_distinct_unknown_app_intent(router):
    intent = router.route("open Paint")
    multiword_intent = router.route("open Microsoft Paint")

    assert (intent.action, intent.value) == ("unknown_app", "Paint")
    assert (multiword_intent.action, multiword_intent.value) == (
        "unknown_app",
        "Microsoft Paint",
    )


def test_ordinary_or_ambiguous_chat_is_not_routed_as_an_action(router):
    assert router.route("Can you find me something interesting to talk about?") is None
    assert router.route("Open your heart to a new idea") is None
    assert router.route("I like using Notepad") is None


def test_action_prefix_requires_a_complete_app_name(router):
    assert router.route("open Notepad later") is None
