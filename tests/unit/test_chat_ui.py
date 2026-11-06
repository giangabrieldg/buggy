from __future__ import annotations

from types import SimpleNamespace
from queue import Queue

import personal_assistant.ui as assistant_ui
from personal_assistant.ui import AssistantApp


class FakeEntry:
    def __init__(self):
        self.configurations = []

    def get(self):
        return "hello while Ollama thinks"

    def delete(self, *_args):
        pass

    def configure(self, **kwargs):
        self.configurations.append(kwargs)


class FakeWidget:
    def configure(self, **_kwargs):
        pass


class FakeCtkWidget:
    instances = []

    def __init__(self, *_args, **kwargs):
        self.kwargs = kwargs
        self.layout = []
        type(self).instances.append(self)

    def grid(self, **kwargs):
        self.layout.append(("grid", kwargs))

    def pack(self, **kwargs):
        self.layout.append(("pack", kwargs))

    def grid_columnconfigure(self, *_args, **_kwargs):
        pass


def patch_ctk_widgets(monkeypatch):
    FakeCtkWidget.instances = []
    monkeypatch.setattr(assistant_ui.ctk, "CTkFrame", FakeCtkWidget)
    monkeypatch.setattr(assistant_ui.ctk, "CTkLabel", FakeCtkWidget)
    monkeypatch.setattr(assistant_ui.ctk, "CTkButton", FakeCtkWidget)
    monkeypatch.setattr(assistant_ui.ctk, "CTkFont", lambda **kwargs: kwargs)


def test_compact_header_shows_mascot_and_green_actions(monkeypatch):
    patch_ctk_widgets(monkeypatch)
    mascot = object()
    app = AssistantApp.__new__(AssistantApp)
    app.root = object()
    app._mascot_image = mascot
    app._model = "qwen3:1.7b"
    app._new_chat = lambda: None

    app._build_header()

    labels = [
        widget for widget in FakeCtkWidget.instances if "text" in widget.kwargs
    ]
    assert labels[0].kwargs["image"] is mascot
    assert labels[0].kwargs["text"] == ""
    model_label = next(
        widget
        for widget in labels
        if widget.kwargs.get("text", "").startswith("Local")
    )

    def green_accent(color):
        red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
        return green > red * 1.35 and green > blue * 1.05

    button = next(
        widget
        for widget in FakeCtkWidget.instances
        if widget.kwargs.get("text") == "+ New chat"
    )
    assert green_accent(button.kwargs["fg_color"])
    assert green_accent(button.kwargs["hover_color"])
    assert green_accent(model_label.kwargs["text_color"])


def test_header_has_compact_trash_button_for_selected_chat(monkeypatch):
    patch_ctk_widgets(monkeypatch)
    app = AssistantApp.__new__(AssistantApp)
    app.root = object()
    app._mascot_image = object()
    app._model = "qwen3:1.7b"
    app._new_chat = lambda: None
    app._confirm_delete_selected_conversation = lambda: None

    app._build_header()

    trash_button = next(
        widget
        for widget in FakeCtkWidget.instances
        if widget.kwargs.get("text") == "\U0001F5D1"
    )
    assert 0 < trash_button.kwargs["width"] <= 36
    assert trash_button.kwargs["command"] is app._confirm_delete_selected_conversation


def test_recent_chats_menu_keeps_clear_all_but_not_per_chat_delete():
    class FakeOptionMenu:
        def __init__(self):
            self.values = []
            self.selected = None

        def configure(self, **kwargs):
            self.values = kwargs["values"]

        def set(self, value):
            self.selected = value

    app = AssistantApp.__new__(AssistantApp)
    app._history = SimpleNamespace(
        list_recent_conversations=lambda: [
            SimpleNamespace(conversation_id="abc123456", title="Current chat")
        ]
    )
    app.session = SimpleNamespace(active_conversation_id="abc123456")
    app.recent_conversations = FakeOptionMenu()

    app._refresh_recent_conversations()

    assert assistant_ui.CLEAR_HISTORY_OPTION in app.recent_conversations.values
    assert not any(
        "delete selected chat" in value.casefold()
        for value in app.recent_conversations.values
    )


def test_assistant_reply_uses_mascot_avatar_and_forest_bubble(monkeypatch):
    patch_ctk_widgets(monkeypatch)
    mascot = object()
    app = AssistantApp.__new__(AssistantApp)
    app.message_list = FakeCtkWidget()
    app._mascot_image = mascot

    app._add_message_bubble("assistant", "Hello there")

    avatar = next(
        widget
        for widget in FakeCtkWidget.instances
        if widget.kwargs.get("image") is mascot
    )
    assert avatar.kwargs["text"] == ""
    bubbles = [
        widget
        for widget in FakeCtkWidget.instances
        if widget.kwargs.get("corner_radius") == 12
    ]
    bubble_color = bubbles[-1].kwargs["fg_color"]
    red, green, blue = (int(bubble_color[index : index + 2], 16) for index in (1, 3, 5))
    assert green > red * 1.35 and green > blue * 1.05


def test_mascot_image_is_loaded_at_compact_display_size():
    app = AssistantApp.__new__(AssistantApp)

    image = app._load_mascot_image()

    assert image is not None
    assert image._size == (36, 36)


def test_user_message_renders_before_chat_worker_starts(monkeypatch):
    events = []

    class DeferredThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            events.append(("worker_started",))

    monkeypatch.setattr(assistant_ui, "Thread", DeferredThread)
    app = AssistantApp.__new__(AssistantApp)
    app._busy = False
    app._chat_request_counter = 0
    app._live_reply_view = None
    app._expression_view = None
    app.input_box = FakeEntry()
    app.send_button = FakeWidget()
    app.status_label = FakeWidget()
    app.session = SimpleNamespace(active_conversation_id="conversation-1")
    app.command_dispatcher = SimpleNamespace(dispatch=lambda _text: None)
    app._add_message_bubble = lambda role, text: events.append(("bubble", role, text))
    app._set_expression = lambda *_args: None
    app._show_live_reply = lambda: None

    app._on_send()

    assert events == [
        ("bubble", "user", "hello while Ollama thinks"),
        ("worker_started",),
    ]
    assert {"state": "disabled"} not in app.input_box.configurations


def test_chat_worker_delivers_stream_events_without_touching_tk():
    events = Queue()
    chunks = ["Hello", " there"]

    def submit_stream(conversation_id, text, on_chunk):
        assert (conversation_id, text) == ("chat-a", "hi")
        for chunk in chunks:
            on_chunk(chunk)
        return "Hello there"

    app = AssistantApp.__new__(AssistantApp)
    app.session = SimpleNamespace(controller=SimpleNamespace(submit_stream=submit_stream))
    app._chat_events = events
    app.root = SimpleNamespace(after=lambda *_args: (_ for _ in ()).throw(AssertionError("worker touched Tk")))

    app._run_send("hi", "chat-a", 7)

    queued = [events.get_nowait() for _ in range(3)]
    assert [event.kind for event in queued] == ["chunk", "chunk", "complete"]
    assert [event.text for event in queued] == ["Hello", " there", "Hello there"]
    assert all(event.conversation_id == "chat-a" and event.request_id == 7 for event in queued)


def test_completion_in_other_chat_resets_busy_without_rendering_reply():
    events = Queue()
    events.put(SimpleNamespace(kind="complete", request_id=4, conversation_id="chat-a", text="Hello", error=None))
    renders = []
    send_updates = []
    app = AssistantApp.__new__(AssistantApp)
    app._chat_events = events
    app._active_chat_request = (4, "chat-a", "hi")
    app._busy = True
    app._closed = False
    app._visible = True
    app._needs_refresh_on_show = False
    app.session = SimpleNamespace(active_conversation_id="chat-b")
    app.send_button = SimpleNamespace(configure=lambda **kwargs: send_updates.append(kwargs))
    app.status_label = FakeWidget()
    app.input_box = FakeEntry()
    app._chat_worker = object()
    app._live_reply_view = None
    app._refresh_recent_conversations = lambda: None
    app._render_messages = lambda: renders.append("render")
    app._set_expression = lambda *_args: None
    app._stop_thinking = lambda: None

    app._drain_chat_events()

    assert app._busy is False
    assert {"state": "normal"} in send_updates
    assert renders == []


def test_search_activity_sets_thinking_then_concerned_on_failure():
    expressions = []
    app = AssistantApp.__new__(AssistantApp)
    app._busy = False
    app._set_expression = lambda name, **_kwargs: expressions.append(name)
    app._pending_search_requests = {3: ("chat-a", False)}
    app._search_events = [SimpleNamespace(kind="searching", request_id=3)]
    app._searching_views = {}
    app.session = SimpleNamespace(active_conversation_id="chat-a")
    app._append_search_event = lambda kind, **_kwargs: expressions.append(kind)
    app._set_search_status = lambda *_args, **_kwargs: None

    app._set_expression("thinking")
    app._on_search_results(3, "budget", [], "index unavailable")

    assert expressions[-1] == "concerned"


def test_new_assistant_row_replaces_previous_expression_target():
    previous_updates = []
    previous = SimpleNamespace(avatar=SimpleNamespace(configure=lambda **kw: previous_updates.append(kw)))
    current = SimpleNamespace(avatar=SimpleNamespace(configure=lambda **_kw: None))
    app = AssistantApp.__new__(AssistantApp)
    app._mascot_image = object()
    app._expression_view = previous
    app._live_reply_view = previous

    app._adopt_expression_view(current)

    assert app._expression_view is current
    assert app._live_reply_view is previous
    assert previous_updates == [{"image": app._mascot_image}]
