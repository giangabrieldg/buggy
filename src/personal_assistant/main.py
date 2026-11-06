from __future__ import annotations

import customtkinter as ctk

from personal_assistant.chat import ConversationController, OllamaChatService
from personal_assistant.config import load_settings
from personal_assistant.history import HistoryRepository
from personal_assistant.search.indexer import DocumentIndexer
from personal_assistant.ui import AssistantApp
from personal_assistant.windows.apps import AppLauncher
from personal_assistant.windows.hotkey import GlobalHotkey
from personal_assistant.windows.tray import TrayController


def main() -> None:
    settings = load_settings()
    history = HistoryRepository(settings.database_path)
    chat = OllamaChatService(model=settings.model)
    controller = ConversationController(history=history, chat=chat)
    indexer = DocumentIndexer(settings.database_path, settings.profile_root)
    app_launcher = AppLauncher(settings.allowed_apps)

    root = ctk.CTk()
    app = AssistantApp(
        root=root,
        controller=controller,
        history=history,
        model=settings.model,
        search_indexer=indexer,
        app_launcher=app_launcher,
        allowed_apps=settings.allowed_apps,
        reduce_motion=settings.reduce_motion,
    )
    tray = TrayController(root, on_exit=app.exit, on_show=app.show)
    hotkey = GlobalHotkey(
        settings.global_shortcut,
        on_activate=app.show,
        schedule=lambda callback: root.after(0, callback),
    )
    app.attach_lifecycle(hotkey, tray)
    root.mainloop()


if __name__ == "__main__":
    main()
