from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from queue import Empty, Queue
from threading import Event, Thread, current_thread
from time import monotonic
from tkinter import TclError
from typing import Sequence

import customtkinter as ctk
from PIL import Image, ImageTk

from personal_assistant.chat import ConversationController
from personal_assistant.commands import CommandDispatcher, CommandRouter
from personal_assistant.config import AppEntry
from personal_assistant.expressions import EXPRESSION_NAMES, choose_expression
from personal_assistant.history import ChatMessage, HistoryRepository
from personal_assistant.motion import MotionScheduler
from personal_assistant.search.controller import SearchController
from personal_assistant.search.indexer import DocumentIndexer, IndexProgress, SearchResult
from personal_assistant.windows.apps import AppLauncher, open_selected_file
from personal_assistant.windows.hotkey import GlobalHotkey
from personal_assistant.windows.tray import TrayController


SHUTDOWN_WAIT_TIMEOUT_SECONDS = 30.0
CLEAR_HISTORY_OPTION = "Clear all chats..."
PALETTE = {
    "background": "#0B1713",
    "surface": "#11201B",
    "assistant_bubble": "#172B23",
    "user_bubble": "#075D3B",
    "raised": "#20362C",
    "border": "#345044",
    "emerald": "#047857",
    "emerald_hover": "#065F46",
    "lime": "#A3E635",
    "amber": "#F4B740",
    "text": "#F1F5F9",
    "muted": "#A8B6C8",
    "subtle": "#718198",
    "success": "#4ADE80",
    "error": "#FDA4AF",
}


@dataclass(frozen=True)
class _SearchEvent:
    conversation_id: str
    kind: str
    request_id: int | None = None
    query: str = ""
    results: tuple[SearchResult, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class _ChatEvent:
    kind: str
    request_id: int
    conversation_id: str
    text: str = ""
    error: Exception | None = None


@dataclass
class _MessageView:
    row: object
    bubble: object
    label: object
    avatar: object | None = None


class ChatSession:
    """Keep active conversation state in sync with the history repository."""

    def __init__(self, history: HistoryRepository, controller: ConversationController):
        self.history = history
        self.controller = controller
        recent = self.history.list_recent_conversations()
        self.active_conversation_id = (
            recent[0].conversation_id if recent else self.history.create_conversation()
        )
        self.messages: list[ChatMessage] = self.history.list_messages(
            self.active_conversation_id
        )

    def refresh_messages(self) -> list[ChatMessage]:
        self.messages = self.history.list_messages(self.active_conversation_id)
        return self.messages

    def select_conversation(self, conversation_id: str) -> None:
        self.active_conversation_id = conversation_id
        self.refresh_messages()

    def start_conversation(self) -> str:
        self.active_conversation_id = self.history.create_conversation()
        self.messages = []
        return self.active_conversation_id

    def delete_conversation(self, conversation_id: str) -> None:
        self.history.delete_conversation(conversation_id)
        if conversation_id == self.active_conversation_id:
            self.start_conversation()
        else:
            self.refresh_messages()

    def delete_all_conversations(self) -> None:
        self.history.delete_all_conversations()
        self.start_conversation()

    def record_user_action(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
    ) -> None:
        target_id = conversation_id or self.active_conversation_id
        self.history.add_message(target_id, "user", text)
        if target_id == self.active_conversation_id:
            self.refresh_messages()

    def record_assistant_result(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
    ) -> None:
        target_id = conversation_id or self.active_conversation_id
        self.history.add_message(target_id, "assistant", text)
        if target_id == self.active_conversation_id:
            self.refresh_messages()

    def record_action(
        self,
        user_text: str,
        assistant_text: str,
        *,
        conversation_id: str | None = None,
    ) -> None:
        target_id = conversation_id or self.active_conversation_id
        self.record_user_action(user_text, conversation_id=target_id)
        self.record_assistant_result(assistant_text, conversation_id=target_id)

    def send(self, text: str, *, conversation_id: str | None = None) -> str | None:
        if not text.strip():
            return None
        target_id = conversation_id or self.active_conversation_id
        try:
            reply = self.controller.submit(target_id, text)
        except Exception:
            if target_id == self.active_conversation_id:
                self.refresh_messages()
            raise
        if target_id == self.active_conversation_id:
            self.refresh_messages()
        return reply


class AssistantApp:
    """Compact CustomTkinter chat window for the local assistant."""

    def __init__(
        self,
        root: ctk.CTk,
        controller: ConversationController,
        history: HistoryRepository,
        *,
        model: str = "qwen3:1.7b",
        search_indexer: DocumentIndexer | None = None,
        app_launcher: AppLauncher | None = None,
        allowed_apps: Sequence[AppEntry] = (),
        reduce_motion: bool = False,
    ):
        self.root = root
        self.session = ChatSession(history, controller)
        self._history = history
        self._chat_worker: Thread | None = None
        self._busy = False
        self._visible = True
        self._needs_refresh_on_show = False
        self._reduce_motion = reduce_motion
        self._chat_events: Queue[_ChatEvent] = Queue()
        self._chat_request_counter = 0
        self._active_chat_request: tuple[int, str, str] | None = None
        self._live_reply_view: _MessageView | None = None
        self._expression_view: _MessageView | None = None
        self._live_reply_text = ""
        self._thinking_after_id = None
        self._expression_after_id = None
        self._current_expression = "neutral"
        self._motion = MotionScheduler(root)
        self._closed = False
        self._shutdown_started = False
        self._shutdown_worker_done = Event()
        self._shutdown_timed_out = False
        self._conversation_labels: dict[str, str] = {}
        self._search_events: list[_SearchEvent] = []
        self._searching_views: dict[int, _MessageView] = {}
        self._pending_search_requests: dict[int, tuple[str, bool]] = {}
        self._search_dialog: ctk.CTkToplevel | None = None
        self._search_dialog_status: ctk.CTkLabel | None = None
        self._search_dialog_entry: ctk.CTkEntry | None = None
        self._model = model
        self._window_icon = None
        self._allowed_apps = tuple(allowed_apps)
        self.hotkey: GlobalHotkey | None = None
        self.tray_controller: TrayController | None = None
        self.app_launcher = app_launcher or AppLauncher(self._allowed_apps)
        self.search_controller = (
            SearchController(
                search_indexer,
                result_callback=self._on_search_results,
                progress_callback=self._on_index_progress,
                selected_path_callback=self._on_selected_path,
            )
            if search_indexer is not None
            else None
        )
        self.command_dispatcher = CommandDispatcher(
            CommandRouter(self._allowed_apps),
            self.app_launcher,
            self.search_controller,
        )

        ctk.set_appearance_mode("dark")
        self.root.title("Buggy — Personal Assistant")
        self._set_window_icon()
        self._mascot_image = self._load_mascot_image()
        self._expression_images = self._load_expression_images()
        self.root.geometry("480x620")
        self.root.minsize(380, 460)
        self.root.configure(fg_color=PALETTE["background"])
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_layout()
        self._render_messages()
        self._refresh_recent_conversations()
        self._schedule_chat_poll()
        if self.search_controller is not None:
            self.search_controller.start()
            self._schedule_search_poll()

    def _set_window_icon(self) -> None:
        """Apply the native Windows icon, falling back to Tk's image icon."""
        try:
            icon = files("personal_assistant").joinpath(
                "assets", "buggy-window-icon.ico"
            )
            self.root.iconbitmap(str(icon))
            return
        except Exception:
            pass

        try:
            image = self._read_window_icon()
            image.thumbnail((128, 128), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image, master=self.root)
            self.root.iconphoto(True, photo)
            self._window_icon = photo
        except Exception:
            # Tk icon support varies by platform; keep the app usable if it fails.
            self._window_icon = None

    @staticmethod
    def _read_mascot() -> Image.Image:
        mascot = files("personal_assistant").joinpath(
            "assets", "buggy-mascot.png"
        )
        with mascot.open("rb") as asset_stream, Image.open(asset_stream) as source:
            return source.convert("RGBA")

    @staticmethod
    def _read_window_icon() -> Image.Image:
        icon = files("personal_assistant").joinpath(
            "assets", "buggy-window-icon.png"
        )
        with icon.open("rb") as asset_stream, Image.open(asset_stream) as source:
            return source.convert("RGBA")

    def _load_mascot_image(self):
        try:
            image = self._read_mascot()
            return ctk.CTkImage(
                light_image=image,
                dark_image=image,
                size=(36, 36),
            )
        except Exception:
            return None

    def _load_expression_images(self) -> dict[str, ctk.CTkImage | None]:
        images: dict[str, ctk.CTkImage | None] = {"neutral": self._mascot_image}
        for name in EXPRESSION_NAMES:
            if name == "neutral":
                continue
            try:
                asset = files("personal_assistant").joinpath(
                    "assets", "expressions", f"{name}.png"
                )
                with asset.open("rb") as stream, Image.open(stream) as source:
                    image = source.convert("RGBA")
                images[name] = ctk.CTkImage(
                    light_image=image, dark_image=image, size=(36, 36)
                )
            except Exception:
                images[name] = self._mascot_image
        return images

    def _build_layout(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)
        self._build_header()

        self.message_list = ctk.CTkScrollableFrame(
            self.root,
            fg_color=PALETTE["background"],
            corner_radius=0,
        )
        self.message_list.grid(row=1, column=0, padx=12, pady=(12, 6), sticky="nsew")
        self.message_list.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self.root, fg_color=PALETTE["surface"], corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            footer,
            text="Ready when you are.",
            anchor="w",
            wraplength=440,
            font=ctk.CTkFont(size=11),
            text_color=PALETTE["muted"],
        )
        self.status_label.grid(row=0, column=0, columnspan=3, padx=12, pady=(8, 4), sticky="ew")

        input_row = ctk.CTkFrame(footer, fg_color="transparent")
        input_row.grid(row=1, column=0, columnspan=3, padx=10, sticky="ew")
        input_row.grid_columnconfigure(0, weight=1)
        self.input_box = ctk.CTkEntry(
            input_row,
            placeholder_text="Say something...",
            height=40,
            border_color=PALETTE["border"],
            fg_color=PALETTE["background"],
            text_color=PALETTE["text"],
        )
        self.input_box.grid(row=0, column=0, padx=(0, 7), sticky="ew")
        self.input_box.bind("<Return>", self._on_send)
        self.send_button = ctk.CTkButton(
            input_row,
            text="Send",
            width=66,
            height=40,
            fg_color=PALETTE["emerald"],
            hover_color=PALETTE["emerald_hover"],
            text_color=PALETTE["text"],
            command=self._on_send,
        )
        self.send_button.grid(row=0, column=1)

        action_row = ctk.CTkFrame(footer, fg_color="transparent")
        action_row.grid(row=2, column=0, columnspan=3, padx=8, pady=(7, 10), sticky="ew")
        action_row.grid_columnconfigure(2, weight=1)
        self.find_files_button = ctk.CTkButton(
            action_row,
            text="Find files",
            width=80,
            height=28,
            fg_color="transparent",
            hover_color=PALETTE["raised"],
            text_color=PALETTE["lime"],
            anchor="w",
            command=self._open_search_dialog,
        )
        self.find_files_button.grid(row=0, column=0, sticky="w")
        app_names = [entry.name for entry in self._allowed_apps]
        self.open_app_menu = ctk.CTkOptionMenu(
            action_row,
            values=["Open app", *app_names],
            width=106,
            height=28,
            fg_color=PALETTE["raised"],
            button_color=PALETTE["emerald"],
            button_hover_color=PALETTE["emerald_hover"],
            text_color=PALETTE["text"],
            command=self._launch_app,
        )
        self.open_app_menu.grid(row=0, column=1, padx=(2, 0), sticky="w")
        self.recent_conversations = ctk.CTkOptionMenu(
            action_row,
            values=["Recent chats"],
            width=170,
            height=28,
            fg_color=PALETTE["raised"],
            button_color=PALETTE["raised"],
            button_hover_color=PALETTE["emerald"],
            text_color=PALETTE["text"],
            command=self._select_conversation,
        )
        self.recent_conversations.grid(row=0, column=2, padx=(4, 0), sticky="e")
        ctk.CTkLabel(
            footer,
            text="Ctrl+Alt+A",
            font=ctk.CTkFont(size=10),
            text_color=PALETTE["subtle"],
        ).grid(row=3, column=2, padx=12, pady=(0, 7), sticky="e")

    def _build_header(self) -> None:
        header = ctk.CTkFrame(
            self.root,
            fg_color=PALETTE["surface"],
            corner_radius=0,
            height=58,
        )
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(2, weight=1)
        self.header_avatar = ctk.CTkLabel(
            header,
            image=self._mascot_image,
            text="" if self._mascot_image is not None else "B",
            width=36,
            height=36,
            fg_color=PALETTE["raised"] if self._mascot_image is None else "transparent",
            corner_radius=18,
            text_color=PALETTE["lime"],
        )
        self.header_avatar.grid(row=0, column=0, padx=(14, 8), pady=10, sticky="w")
        ctk.CTkLabel(
            header,
            text="Buggy",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=PALETTE["text"],
        ).grid(row=0, column=1, padx=(0, 10), pady=12, sticky="w")
        ctk.CTkLabel(
            header,
            text=f"Local \u00b7 {self._model}",
            font=ctk.CTkFont(size=11),
            text_color=PALETTE["lime"],
        ).grid(row=0, column=2, sticky="w")
        self.new_chat_button = ctk.CTkButton(
            header,
            text="+ New chat",
            width=92,
            height=32,
            fg_color=PALETTE["emerald"],
            hover_color=PALETTE["emerald_hover"],
            text_color=PALETTE["text"],
            command=self._new_chat,
        )
        self.new_chat_button.grid(row=0, column=3, padx=12, pady=10)
        self.delete_chat_button = ctk.CTkButton(
            header,
            text="\U0001F5D1",
            width=32,
            height=32,
            corner_radius=8,
            border_width=1,
            border_color=PALETTE["error"],
            fg_color=PALETTE["raised"],
            hover_color=PALETTE["border"],
            text_color=PALETTE["error"],
            font=ctk.CTkFont(size=17),
            command=self._confirm_delete_selected_conversation,
        )
        self.delete_chat_button.grid(row=0, column=4, padx=(0, 12), pady=10)

    def _render_messages(self) -> None:
        self._cancel_visual_motion()
        self._live_reply_view = None
        self._expression_view = None
        self._searching_views = {}
        request = self._active_chat_request
        expression = (
            "thinking"
            if request is not None and request[1] == self.session.active_conversation_id
            else "neutral"
        )
        self._set_expression(expression)
        for child in self.message_list.winfo_children():
            child.destroy()

        if not self.session.messages and not self._events_for_active_conversation():
            self._add_message_bubble(
                "assistant",
                "Hey, I’m Buggy! Want to talk, or should I help you find something?",
            )
        else:
            for message in self.session.messages:
                self._add_message_bubble(message.role, message.content)
            for event in self._events_for_active_conversation():
                self._render_search_event(event)

        if request is not None and request[1] == self.session.active_conversation_id:
            if not self.session.messages or (
                self.session.messages[-1].role != "user"
                or self.session.messages[-1].content != request[2]
            ):
                self._add_message_bubble("user", request[2])
            self._show_live_reply()

    def _events_for_active_conversation(self) -> list[_SearchEvent]:
        return [
            event
            for event in self._search_events
            if event.conversation_id == self.session.active_conversation_id
        ]

    def _add_message_bubble(self, role: str, content: str) -> _MessageView:
        is_user = role == "user"
        row = ctk.CTkFrame(self.message_list, fg_color="transparent")
        row.pack(fill="x", padx=5, pady=5)
        row.grid_columnconfigure(0, weight=1 if is_user else 0)
        row.grid_columnconfigure(2, weight=0 if is_user else 1)

        avatar = None
        if not is_user:
            avatar = ctk.CTkLabel(
                row,
                image=self._mascot_image,
                text="" if self._mascot_image is not None else "B",
                width=36,
                height=36,
                fg_color=PALETTE["raised"] if self._mascot_image is None else "transparent",
                corner_radius=18,
                text_color=PALETTE["lime"],
            )
            avatar.grid(row=0, column=0, padx=(2, 8), sticky="nw")

        bubble = ctk.CTkFrame(
            row,
            fg_color=PALETTE["user_bubble"] if is_user else PALETTE["assistant_bubble"],
            corner_radius=12,
        )
        bubble.grid(
            row=0,
            column=1 if not is_user else 1,
            sticky="e" if is_user else "w",
        )
        label = ctk.CTkLabel(
            bubble,
            text=content,
            justify="left",
            anchor="w",
            wraplength=320,
            text_color=PALETTE["text"],
            font=ctk.CTkFont(size=13),
        )
        label.pack(padx=11, pady=8)
        return _MessageView(row=row, bubble=bubble, label=label, avatar=avatar)

    def _cancel_visual_motion(self) -> None:
        self._motion.cancel_all()
        self._thinking_after_id = None
        self._expression_after_id = None

    def _near_bottom(self) -> bool:
        try:
            return self.message_list._parent_canvas.yview()[1] >= 0.96
        except (AttributeError, TclError):
            return True

    def _scroll_to_bottom(self) -> None:
        try:
            self.message_list._parent_canvas.yview_moveto(1.0)
        except (AttributeError, TclError):
            pass

    def _reveal_bubble(self, view: _MessageView, role: str) -> None:
        if self._reduce_motion or not self._visible:
            return
        target = PALETTE["user_bubble"] if role == "user" else PALETTE["assistant_bubble"]
        try:
            view.bubble.configure(fg_color=PALETTE["surface"])
            self._motion.schedule(80, self._configure_color, view.bubble, PALETTE["raised"])
            self._motion.schedule(170, self._configure_color, view.bubble, target)
        except TclError:
            pass

    @staticmethod
    def _configure_color(widget: object, color: str) -> None:
        try:
            widget.configure(fg_color=color)
        except TclError:
            pass

    def _adopt_expression_view(self, view: _MessageView | None) -> None:
        previous = self._expression_view
        if previous is not None and previous is not view and previous.avatar is not None:
            try:
                previous.avatar.configure(image=self._mascot_image)
            except TclError:
                pass
        self._expression_view = view

    def _set_expression(self, expression: str, *, hold_ms: int = 0) -> None:
        if self._expression_after_id is not None:
            self._motion.cancel(self._expression_after_id)
            self._expression_after_id = None
        self._current_expression = expression
        image = self._expression_images.get(expression, self._mascot_image)
        for avatar in (getattr(self, "header_avatar", None),
                       self._expression_view.avatar if self._expression_view else None):
            if avatar is None:
                continue
            try:
                avatar.configure(image=image)
                if not self._reduce_motion and self._visible:
                    avatar.configure(fg_color=PALETTE["raised"])
                    self._motion.schedule(170, self._configure_color, avatar, "transparent")
            except TclError:
                pass
        if hold_ms and self._visible:
            self._expression_after_id = self._motion.schedule(
                hold_ms, self._set_expression, "neutral"
            )

    def _stop_thinking(self) -> None:
        if self._thinking_after_id is not None:
            self._motion.cancel(self._thinking_after_id)
            self._thinking_after_id = None

    def _animate_thinking(self, dots: int = 0) -> None:
        if not self._busy or not self._visible or self._live_reply_text:
            return
        view = self._live_reply_view
        if view is None:
            return
        try:
            view.label.configure(text="Thinking" + "." * (dots + 1))
        except TclError:
            return
        self._thinking_after_id = self._motion.schedule(
            360, self._animate_thinking, (dots + 1) % 3
        )

    def _show_live_reply(self) -> None:
        if not self._visible:
            return
        follow = self._near_bottom()
        text = self._live_reply_text or "Thinking…"
        self._live_reply_view = self._add_message_bubble("assistant", text)
        self._adopt_expression_view(self._live_reply_view)
        if self._live_reply_view.avatar is not None:
            image = self._expression_images.get(self._current_expression, self._mascot_image)
            self._live_reply_view.avatar.configure(image=image)
        if not self._live_reply_text and not self._reduce_motion:
            self._animate_thinking()
        if follow:
            self.root.after_idle(self._scroll_to_bottom)

    def _update_live_reply(self) -> None:
        if not self._visible or self._live_reply_view is None or not self._live_reply_text:
            return
        self._stop_thinking()
        follow = self._near_bottom()
        try:
            self._live_reply_view.label.configure(text=self._live_reply_text)
        except TclError:
            return
        if follow:
            self.root.after_idle(self._scroll_to_bottom)

    def _render_search_event(self, event: _SearchEvent) -> _MessageView | None:
        if event.kind == "searching":
            view = self._add_message_bubble(
                "assistant", f'Searching your profile for "{event.query}"…'
            )
            if event.request_id is not None:
                self._searching_views[event.request_id] = view
            return view
        elif event.kind == "no_matches":
            return self._add_message_bubble(
                "assistant", f'No files matched "{event.query}".'
            )
        elif event.kind == "error":
            return self._add_message_bubble(
                "assistant", f"File search could not finish: {event.message}"
            )
        elif event.kind in {"results", "result_cards"}:
            if event.kind == "results":
                intro = ctk.CTkFrame(
                    self.message_list,
                    fg_color=PALETTE["assistant_bubble"],
                    corner_radius=12,
                )
                intro.pack(anchor="w", padx=5, pady=5, fill="x")
                ctk.CTkLabel(
                    intro,
                    text=f'Found {len(event.results)} file(s) for "{event.query}". Select a path to open it:',
                    justify="left",
                    anchor="w",
                    wraplength=340,
                    text_color=PALETTE["text"],
                    font=ctk.CTkFont(size=13),
                ).pack(padx=11, pady=8, anchor="w")
            for result in event.results:
                card = ctk.CTkFrame(
                    self.message_list,
                    fg_color=PALETTE["surface"],
                    border_color=PALETTE["border"],
                    border_width=1,
                    corner_radius=9,
                )
                card.pack(anchor="w", padx=8, pady=4, fill="x")
                button = ctk.CTkButton(
                    card,
                    text=f"{result.path.name}\n{result.path}\n{result.excerpt}",
                    justify="left",
                    anchor="w",
                    height=62,
                    fg_color="transparent",
                    hover_color=PALETTE["raised"],
                    text_color=PALETTE["lime"],
                    command=lambda selected=result.path: self._select_file(selected),
                )
                button.pack(fill="x", padx=5, pady=4)

    def _append_search_event(
        self,
        kind: str,
        *,
        request_id: int | None = None,
        query: str = "",
        results: tuple[SearchResult, ...] = (),
        message: str = "",
        conversation_id: str | None = None,
    ) -> None:
        event = _SearchEvent(
            conversation_id=conversation_id or self.session.active_conversation_id,
            kind=kind,
            request_id=request_id,
            query=query,
            results=results,
            message=message,
        )
        self._search_events.append(event)
        if event.conversation_id == self.session.active_conversation_id:
            view = self._render_search_event(event)
            if view is not None and not self._busy:
                self._adopt_expression_view(view)
            if not self._busy and kind == "searching":
                self._set_expression("thinking")
            elif not self._busy and kind == "error":
                self._set_expression("concerned", hold_ms=4000)

    def _refresh_recent_conversations(self) -> None:
        conversations = self._history.list_recent_conversations()
        self._conversation_labels = {}
        for item in conversations:
            label = f"{item.title} · {item.conversation_id[:6]}"
            self._conversation_labels[label] = item.conversation_id
        choices = list(self._conversation_labels) or ["Recent chats"]
        choices.append(CLEAR_HISTORY_OPTION)
        self.recent_conversations.configure(values=choices)
        active_label = next(
            (
                label
                for label, conversation_id in self._conversation_labels.items()
                if conversation_id == self.session.active_conversation_id
            ),
            choices[0],
        )
        self.recent_conversations.set(active_label)

    def _select_conversation(self, label: str) -> None:
        if label == CLEAR_HISTORY_OPTION:
            self._confirm_clear_conversation_history()
            return
        conversation_id = self._conversation_labels.get(label)
        if conversation_id is None:
            return
        self.session.select_conversation(conversation_id)
        self._render_messages()
        self.status_label.configure(text="Conversation restored.", text_color=PALETTE["muted"])

    def _history_change_blocked(self) -> bool:
        if self._busy:
            self.status_label.configure(
                text="Wait for the assistant to finish before changing chat history.",
                text_color=PALETTE["amber"],
            )
            self._refresh_recent_conversations()
            return True
        if self._pending_search_requests:
            self.status_label.configure(
                text="Wait for file searches to finish before changing chat history.",
                text_color=PALETTE["amber"],
            )
            self._refresh_recent_conversations()
            return True
        return False

    def _confirm_delete_selected_conversation(self) -> None:
        if self._history_change_blocked():
            return
        conversation_id = self.session.active_conversation_id
        selected_label = next(
            (
                label
                for label, item_id in self._conversation_labels.items()
                if item_id == conversation_id
            ),
            "the selected chat",
        )
        self._show_history_confirmation(
            title="Delete chat?",
            message=f"Delete {selected_label} and all of its messages?",
            confirm_label="Delete chat",
            on_confirm=lambda: self._delete_conversation(conversation_id),
        )

    def _confirm_clear_conversation_history(self) -> None:
        if self._history_change_blocked():
            return
        self._show_history_confirmation(
            title="Clear all chats?",
            message="Delete every saved conversation and message? This cannot be undone.",
            confirm_label="Clear all",
            on_confirm=self._delete_all_conversations,
        )

    def _show_history_confirmation(
        self,
        *,
        title: str,
        message: str,
        confirm_label: str,
        on_confirm: Callable[[], None],
    ) -> None:
        dialog = ctk.CTkToplevel(self.root)
        dialog.title(title)
        dialog.geometry("370x190")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.configure(fg_color=PALETTE["surface"])
        dialog.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            dialog,
            text=message,
            anchor="w",
            justify="left",
            wraplength=330,
            text_color=PALETTE["text"],
        ).grid(row=0, column=0, columnspan=2, padx=18, pady=(20, 28), sticky="ew")

        def close_dialog() -> None:
            try:
                dialog.grab_release()
            except TclError:
                pass
            dialog.destroy()
            self._refresh_recent_conversations()

        def confirm() -> None:
            try:
                on_confirm()
            finally:
                close_dialog()

        ctk.CTkButton(
            dialog,
            text="Cancel",
            fg_color=PALETTE["raised"],
            hover_color=PALETTE["border"],
            text_color=PALETTE["text"],
            command=close_dialog,
        ).grid(row=1, column=0, padx=(18, 6), pady=(0, 16), sticky="ew")
        ctk.CTkButton(
            dialog,
            text=confirm_label,
            fg_color=PALETTE["emerald"],
            hover_color=PALETTE["emerald_hover"],
            text_color=PALETTE["text"],
            command=confirm,
        ).grid(row=1, column=1, padx=(6, 18), pady=(0, 16), sticky="ew")
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.grab_set()

    def _delete_conversation(self, conversation_id: str) -> None:
        if self._history_change_blocked():
            return
        try:
            self.session.delete_conversation(conversation_id)
        except Exception as error:
            self.status_label.configure(
                text=f"Could not delete chat: {error}",
                text_color=PALETTE["error"],
            )
            return
        self._search_events = [
            event
            for event in self._search_events
            if event.conversation_id != conversation_id
        ]
        self._pending_search_requests = {
            request_id: context
            for request_id, context in self._pending_search_requests.items()
            if context[0] != conversation_id
        }
        self._render_messages()
        self._refresh_recent_conversations()
        self.status_label.configure(text="Chat deleted.", text_color=PALETTE["muted"])

    def _delete_all_conversations(self) -> None:
        if self._history_change_blocked():
            return
        try:
            self.session.delete_all_conversations()
        except Exception as error:
            self.status_label.configure(
                text=f"Could not clear chat history: {error}",
                text_color=PALETTE["error"],
            )
            return
        self._search_events.clear()
        self._pending_search_requests.clear()
        self._render_messages()
        self._refresh_recent_conversations()
        self.status_label.configure(
            text="All conversations cleared.",
            text_color=PALETTE["muted"],
        )

    def _new_chat(self) -> None:
        self.session.start_conversation()
        self._render_messages()
        self._refresh_recent_conversations()
        self.status_label.configure(text="New conversation started.", text_color=PALETTE["muted"])
        self.input_box.focus_set()

    def _on_send(self, _event=None) -> str | None:
        if self._busy:
            return "break"
        text = self.input_box.get()
        if not text.strip():
            return "break" if _event is not None else None

        self.input_box.delete(0, "end")
        conversation_id = self.session.active_conversation_id
        outcome = self.command_dispatcher.dispatch(text)
        if outcome is not None:
            try:
                self.session.record_user_action(
                    text,
                    conversation_id=conversation_id,
                )
                if outcome.action != "search" or not outcome.success:
                    self.session.record_assistant_result(
                        outcome.message,
                        conversation_id=conversation_id,
                    )
            except Exception as error:
                self.status_label.configure(
                    text=f"Action completed, but history could not be saved: {error}",
                    text_color=PALETTE["error"],
                )
            else:
                user_view = self._add_message_bubble("user", text)
                self._reveal_bubble(user_view, "user")
                if outcome.action != "search" or not outcome.success:
                    assistant_view = self._add_message_bubble("assistant", outcome.message)
                    self._adopt_expression_view(assistant_view)
                    self._reveal_bubble(assistant_view, "assistant")
                self._refresh_recent_conversations()
                if (
                    outcome.action == "search"
                    and outcome.success
                    and outcome.request_id is not None
                ):
                    self._pending_search_requests[outcome.request_id] = (
                        conversation_id,
                        True,
                    )
                    self._append_search_event(
                        "searching",
                        request_id=outcome.request_id,
                        query=outcome.value,
                        conversation_id=conversation_id,
                    )
                self.status_label.configure(
                    text=outcome.message,
                    text_color=PALETTE["muted"] if outcome.success else PALETTE["error"],
                )
            return "break" if _event is not None else None

        self._busy = True
        self._chat_request_counter += 1
        request_id = self._chat_request_counter
        self._active_chat_request = (request_id, conversation_id, text)
        self._live_reply_text = ""
        self._adopt_expression_view(None)
        self._live_reply_view = None
        self.send_button.configure(state="disabled")
        self.status_label.configure(text="Buggy is thinking…", text_color=PALETTE["lime"])
        user_view = self._add_message_bubble("user", text)
        if isinstance(user_view, _MessageView):
            self._reveal_bubble(user_view, "user")
        self._set_expression("thinking")
        self._show_live_reply()
        self._chat_worker = Thread(
            target=self._run_send,
            args=(text, conversation_id, request_id),
            name="assistant-chat-send",
            daemon=True,
        )
        self._chat_worker.start()
        return "break" if _event is not None else None

    def _run_send(self, text: str, conversation_id: str, request_id: int) -> None:
        try:
            reply = self.session.controller.submit_stream(
                conversation_id,
                text,
                lambda chunk: self._chat_events.put(
                    _ChatEvent("chunk", request_id, conversation_id, text=chunk)
                ),
            )
            self._chat_events.put(
                _ChatEvent("complete", request_id, conversation_id, text=reply)
            )
        except Exception as error:
            self._chat_events.put(
                _ChatEvent("error", request_id, conversation_id, error=error)
            )

    def _schedule_chat_poll(self) -> None:
        if self._closed:
            return
        try:
            self.root.after(50, self._poll_chat_events)
        except TclError:
            pass

    def _poll_chat_events(self) -> None:
        if self._closed:
            return
        self._drain_chat_events()
        self._schedule_chat_poll()

    def _drain_chat_events(self) -> None:
        changed = False
        while True:
            try:
                event = self._chat_events.get_nowait()
            except Empty:
                break
            request = self._active_chat_request
            if self._closed or request is None or event.request_id != request[0]:
                continue
            if event.kind == "chunk":
                self._live_reply_text += event.text
                changed = True
            elif event.kind == "complete":
                self._live_reply_text = event.text
                self._finish_chat(event, request)
                changed = False
            elif event.kind == "error":
                self._fail_chat(event)
                changed = False
        if changed:
            request = self._active_chat_request
            if request is not None and request[1] == self.session.active_conversation_id:
                self._update_live_reply()

    def _finish_chat(self, event: _ChatEvent, request: tuple[int, str, str]) -> None:
        self._stop_thinking()
        active = event.conversation_id == self.session.active_conversation_id
        if active:
            self.session.refresh_messages()
        if active and self._visible:
            self._update_live_reply()
            if self._live_reply_view is not None:
                self._adopt_expression_view(self._live_reply_view)
            expression = choose_expression("reply", request[2], event.text)
            self._set_expression(expression, hold_ms=4000 if expression != "neutral" else 0)
        else:
            self._needs_refresh_on_show = self._needs_refresh_on_show or not self._visible
            self._set_expression("neutral")
        self._active_chat_request = None
        self._busy = False
        self._chat_worker = None
        self.send_button.configure(state="normal")
        self._refresh_recent_conversations()
        self.status_label.configure(text="Ready when you are.", text_color=PALETTE["muted"])

    def _fail_chat(self, event: _ChatEvent) -> None:
        self._stop_thinking()
        active = event.conversation_id == self.session.active_conversation_id
        if active:
            self.session.refresh_messages()
        if active and self._visible and self._live_reply_view is not None:
            self._adopt_expression_view(None)
            try:
                self._live_reply_view.row.destroy()
            except TclError:
                pass
            self._live_reply_view = None
            self._set_expression("concerned", hold_ms=4000)
        else:
            self._needs_refresh_on_show = self._needs_refresh_on_show or not self._visible
            self._set_expression("neutral")
        self._active_chat_request = None
        self._busy = False
        self._chat_worker = None
        self._live_reply_text = ""
        self.send_button.configure(state="normal")
        self._refresh_recent_conversations()
        self.status_label.configure(text=str(event.error), text_color=PALETTE["error"])

    def _open_search_dialog(self) -> None:
        if self.search_controller is None:
            self.status_label.configure(
                text="Document search is unavailable.", text_color=PALETTE["error"]
            )
            return
        if self._search_dialog is not None and self._search_dialog.winfo_exists():
            self._search_dialog.deiconify()
            self._search_dialog.lift()
            self._search_dialog_entry.focus_set()
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Find files")
        dialog.geometry("390x175")
        dialog.resizable(False, False)
        dialog.configure(fg_color=PALETTE["surface"])
        dialog.transient(self.root)
        dialog.grid_columnconfigure(0, weight=1)
        self._search_dialog = dialog

        ctk.CTkLabel(
            dialog,
            text="Search file names and document contents",
            anchor="w",
            text_color=PALETTE["text"],
        ).grid(row=0, column=0, columnspan=2, padx=14, pady=(14, 7), sticky="ew")
        self._search_dialog_entry = ctk.CTkEntry(
            dialog,
            placeholder_text="Try a name or phrase",
            height=38,
            border_color=PALETTE["border"],
            fg_color=PALETTE["background"],
            text_color=PALETTE["text"],
        )
        self._search_dialog_entry.grid(
            row=1, column=0, columnspan=2, padx=14, pady=4, sticky="ew"
        )
        self._search_dialog_entry.bind("<Return>", self._submit_file_search)
        ctk.CTkButton(
            dialog,
            text="Search",
            command=self._submit_file_search,
            fg_color=PALETTE["emerald"],
            hover_color=PALETTE["emerald_hover"],
        ).grid(row=2, column=0, padx=(14, 5), pady=7, sticky="ew")
        ctk.CTkButton(
            dialog,
            text="Re-index",
            command=self._reindex_files,
            fg_color=PALETTE["raised"],
            hover_color=PALETTE["emerald_hover"],
        ).grid(row=2, column=1, padx=(5, 14), pady=7, sticky="ew")
        self._search_dialog_status = ctk.CTkLabel(
            dialog,
            text="Search results appear in chat.",
            anchor="w",
            wraplength=360,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(size=11),
        )
        self._search_dialog_status.grid(
            row=3, column=0, columnspan=2, padx=14, pady=(0, 10), sticky="ew"
        )
        dialog.protocol("WM_DELETE_WINDOW", dialog.withdraw)
        self._search_dialog_entry.focus_set()

    def _submit_file_search(self, _event=None) -> str | None:
        if self.search_controller is None or self._search_dialog_entry is None:
            return "break" if _event is not None else None
        query = self._search_dialog_entry.get().strip()
        if not query:
            if self._search_dialog_status is not None:
                self._search_dialog_status.configure(text="Enter a name or phrase to search.")
            return "break" if _event is not None else None

        conversation_id = self.session.active_conversation_id
        try:
            request_id = self.search_controller.search(query)
        except Exception as error:
            message = str(error)
            if self._search_dialog_status is not None:
                self._search_dialog_status.configure(
                    text=message,
                    text_color=PALETTE["error"],
                )
            self._set_search_status(message, error=True)
            return "break" if _event is not None else None
        self._pending_search_requests[request_id] = (conversation_id, False)
        self._append_search_event(
            "searching",
            request_id=request_id,
            query=query,
            conversation_id=conversation_id,
        )
        self._set_search_status(f'Searching for "{query}"…')
        return "break" if _event is not None else None

    def _reindex_files(self) -> None:
        if self.search_controller is None:
            return
        try:
            self.search_controller.reindex()
        except Exception as error:
            self._set_search_status(str(error), error=True)
        else:
            self._set_search_status("Re-indexing your profile in the background…")

    def _on_search_results(
        self,
        request_id: int | None,
        query: str,
        results: list[SearchResult],
        error: str | None,
    ) -> None:
        if not query:
            message = error or "Document indexing failed."
            self._append_search_event("error", message=message)
            self._set_search_status(message, error=True)
            return

        context = (
            self._pending_search_requests.pop(request_id, None)
            if request_id is not None
            else None
        )
        conversation_id, persist_result = (
            context
            if context is not None
            else (self.session.active_conversation_id, False)
        )
        if request_id is not None:
            self._search_events = [
                event
                for event in self._search_events
                if not (event.kind == "searching" and event.request_id == request_id)
            ]
            view = self._searching_views.pop(request_id, None)
            if view is not None:
                try:
                    view.row.destroy()
                except TclError:
                    pass
                if self._expression_view is view:
                    self._expression_view = None
        if error:
            if persist_result:
                self._record_search_completion(
                    conversation_id,
                    f'Search for "{query}" failed: {error}',
                )
            else:
                self._append_search_event(
                    "error", query=query, message=error, conversation_id=conversation_id
                )
            self._set_search_status(error, error=True)
        elif not results:
            if persist_result:
                self._record_search_completion(
                    conversation_id,
                    f'No files matched "{query}".',
                )
            else:
                self._append_search_event(
                    "no_matches", query=query, conversation_id=conversation_id
                )
            self._set_search_status(f'No files matched "{query}".')
        else:
            if persist_result:
                self._record_search_completion(
                    conversation_id,
                    f'Found {len(results)} file(s) matching "{query}".',
                )
            self._append_search_event(
                "result_cards" if persist_result else "results",
                request_id=request_id,
                query=query,
                results=tuple(results),
                conversation_id=conversation_id,
            )
            self._set_search_status(f"Found {len(results)} matching file(s).")

        if conversation_id == self.session.active_conversation_id and not self._busy:
            pending_here = any(
                target_id == conversation_id
                for target_id, _persist in self._pending_search_requests.values()
            )
            if pending_here:
                self._set_expression("thinking")
            elif error:
                self._set_expression("concerned", hold_ms=4000)
            else:
                self._set_expression("neutral")

    def _record_search_completion(self, conversation_id: str, text: str) -> None:
        self.session.record_assistant_result(text, conversation_id=conversation_id)
        if conversation_id == self.session.active_conversation_id:
            view = self._add_message_bubble("assistant", text)
            if not self._busy:
                self._adopt_expression_view(view)
            self._reveal_bubble(view, "assistant")

    def _on_index_progress(self, progress: IndexProgress) -> None:
        if progress.done and progress.complete:
            text = (
                f"Profile index ready: {progress.scanned} scanned, "
                f"{progress.indexed} indexed, {progress.skipped} skipped"
            )
        elif progress.done:
            text = (
                f"Indexing stopped early; existing results were kept "
                f"({progress.scanned} scanned, {progress.indexed} indexed, "
                f"{progress.skipped} skipped)"
            )
        else:
            text = (
                f"Indexing files: {progress.scanned} scanned, "
                f"{progress.indexed} indexed, {progress.skipped} skipped"
            )
        self._set_search_status(text)

    def _set_search_status(self, text: str, *, error: bool = False) -> None:
        color = PALETTE["error"] if error else PALETTE["muted"]
        self.status_label.configure(text=text, text_color=color)
        if self._search_dialog_status is not None:
            try:
                if self._search_dialog_status.winfo_exists():
                    self._search_dialog_status.configure(text=text, text_color=color)
            except TclError:
                self._search_dialog_status = None

    def _select_file(self, path) -> None:
        if self.search_controller is not None:
            self.search_controller.select_result(path)

    def _on_selected_path(self, path) -> None:
        try:
            open_selected_file(path)
        except Exception as error:
            self._set_search_status(f"Could not open selected file: {error}", error=True)
            if not self._busy:
                self._set_expression("concerned", hold_ms=4000)
        else:
            self._set_search_status(f"Opened {path}")
            if not self._busy:
                self._set_expression("neutral")

    def _launch_app(self, name: str) -> None:
        if name == "Open app":
            return
        result = self.app_launcher.launch(name)
        self.open_app_menu.set("Open app")
        self.status_label.configure(
            text=result.message,
            text_color=PALETTE["success"] if result.success else PALETTE["error"],
        )
        if not self._busy:
            self._set_expression(
                "neutral" if result.success else "concerned",
                hold_ms=0 if result.success else 4000,
            )

    def attach_lifecycle(
        self,
        hotkey: GlobalHotkey,
        tray_controller: TrayController,
    ) -> None:
        self.hotkey = hotkey
        self.tray_controller = tray_controller
        if not hotkey.registered:
            self.show_status(hotkey.error_message or "The global shortcut is unavailable.", error=True)

    def show(self) -> None:
        restore_view = not self._visible or self._needs_refresh_on_show
        self._visible = True
        if self._needs_refresh_on_show:
            self.session.refresh_messages()
            self._needs_refresh_on_show = False
        if restore_view:
            self._render_messages()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def show_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.configure(
            text=message,
            text_color=PALETTE["error"] if error else PALETTE["muted"],
        )

    def _schedule_search_poll(self) -> None:
        if self._closed or self.search_controller is None:
            return
        try:
            self.root.after(100, self._poll_search_updates)
        except TclError:
            pass

    def _poll_search_updates(self) -> None:
        if self._closed or self.search_controller is None:
            return
        self.search_controller.process_updates()
        self._schedule_search_poll()

    def _on_close(self) -> None:
        if self._shutdown_started:
            return
        self._visible = False
        self._needs_refresh_on_show = True
        self._cancel_visual_motion()
        if self.tray_controller is not None:
            self.tray_controller.hide()
        else:
            self.root.withdraw()

    def exit(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._closed = True
        self._visible = False
        motion = getattr(self, "_motion", None)
        if motion is not None:
            motion.close()
        self.root.deiconify()
        self.root.lift()
        self.status_label.configure(
            text="Finishing background work before exit…",
            text_color=PALETTE["amber"],
        )
        self.send_button.configure(state="disabled")
        self.input_box.configure(state="disabled")
        if self.search_controller is not None:
            self.search_controller.stop()

        def wait_for_background_work() -> None:
            deadline = monotonic() + SHUTDOWN_WAIT_TIMEOUT_SECONDS
            self._shutdown_timed_out = True
            try:
                if self.hotkey is not None:
                    try:
                        self.hotkey.unregister()
                    except Exception:
                        # A hotkey conflict/OS error should not bypass worker cleanup.
                        pass
                workers_stopped = True
                if self.search_controller is not None:
                    remaining = max(0.0, deadline - monotonic())
                    workers_stopped = self.search_controller.wait_for_workers(
                        timeout=remaining
                    )
                chat_worker = self._chat_worker
                if chat_worker is not None and chat_worker is not current_thread():
                    remaining = max(0.0, deadline - monotonic())
                    chat_worker.join(remaining)
                chat_stopped = chat_worker is None or not chat_worker.is_alive()
                self._shutdown_timed_out = not workers_stopped or not chat_stopped
            finally:
                self._shutdown_worker_done.set()

        Thread(
            target=wait_for_background_work,
            name="assistant-exit-waiter",
            daemon=True,
        ).start()
        self._poll_shutdown()

    def _poll_shutdown(self) -> None:
        if not self._shutdown_worker_done.is_set():
            try:
                self.root.after(50, self._poll_shutdown)
            except TclError:
                pass
            return
        if self._shutdown_timed_out:
            self.status_label.configure(
                text="A background task is still running; closing without closing its database connection.",
                text_color=PALETTE["amber"],
            )
        elif self.search_controller is not None:
            # All search workers have exited, so this only closes a connection
            # owned by the UI thread; worker-local connections are never touched.
            self.search_controller.indexer.close()
        self.root.destroy()
