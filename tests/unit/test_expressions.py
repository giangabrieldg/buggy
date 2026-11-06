from importlib import import_module, resources

import pytest
from PIL import Image


@pytest.mark.parametrize(
    ("activity", "user", "reply", "expected"),
    [
        ("error", "I passed!", "Congratulations!", "concerned"),
        ("thinking", "I passed!", "Congratulations!", "thinking"),
        ("reply", "I passed!", "Congratulations! That's wonderful news.", "happy"),
        ("reply", "I feel sad today", "I'm sorry you're having a hard day. I'm here to listen.", "caring"),
        ("reply", "Why do stars shine?", "Interesting question! Let's explore how stars make light.", "curious"),
        ("reply", "Hi", "Hello. How can I help?", "neutral"),
        ("idle", "I passed!", "Congratulations!", "neutral"),
    ],
)
def test_activity_and_clear_reply_tone_select_expected_face(activity, user, reply, expected):
    policy = import_module("personal_assistant.expressions")
    assert policy.choose_expression(activity, user, reply) == expected


def test_conflicting_tone_stays_neutral():
    policy = import_module("personal_assistant.expressions")
    assert policy.tone_expression(
        "I feel sad today", "Congratulations! I'm sorry you're having a hard day."
    ) == "neutral"


@pytest.mark.parametrize(
    ("user", "reply", "expected"),
    [
        ("I won a game", "That's exciting! Sounds fun.", "happy"),
        ("I'm feeling down today", "That sounds tough. I'm here for you.", "caring"),
        ("How does rain form?", "Great question! Let's find out.", "curious"),
    ],
)
def test_common_conversational_phrases_trigger_clear_expression(user, reply, expected):
    policy = import_module("personal_assistant.expressions")
    assert policy.tone_expression(user, reply) == expected


def test_expression_assets_are_transparent_square_images():
    policy = import_module("personal_assistant.expressions")
    assets = resources.files("personal_assistant").joinpath("assets", "expressions")
    dimensions = set()
    for name in policy.EXPRESSION_NAMES:
        with assets.joinpath(f"{name}.png").open("rb") as stream, Image.open(stream) as image:
            dimensions.add(image.size)
            assert image.mode == "RGBA"
            assert image.size[0] == image.size[1]
            assert image.getextrema()[3][0] == 0
            assert image.getextrema()[3][1] == 255
    assert len(dimensions) == 1
