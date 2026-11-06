# Buggy Personal AI Assistant

A compact Windows desktop chat assistant that runs against a local Ollama model. Conversations and the document index are stored in a local SQLite database.

## Requirements

- Windows 10 or 11
- Python 3.11 or later
- Ollama installed, with `qwen3:1.7b` available locally

The assistant does not use a paid API. Ollama and the model need to be installed before the assistant can chat. After setup, chat inference runs on your computer.

## Run

From the project folder in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m personal_assistant.main
```

To check that Ollama has the configured model, run:

```powershell
ollama list
```

If `qwen3:1.7b` is missing, download it once with:

```powershell
ollama pull qwen3:1.7b
```

Ollama must be running locally at `http://127.0.0.1:11434` when you chat.

## Using Buggy

- Type a message and press Enter or click **Send** to chat.
- Buggy shows your message immediately, then streams its local reply into the chat. You can draft a follow-up while it responds; **Send** becomes available when the reply finishes. The mascot reacts to thinking, clear reply tone, and errors.
- Use **Find files** or type `find <words>` / `search <words>` to search your Windows user profile. Search results show paths; select a path to open that file.
- Use **Open app** or type `open Notepad`, `open Calculator`, or `open Word` to launch a configured application.
- **New chat** starts a conversation. Recent conversations are available in the menu.
- Press **Ctrl+Alt+A** to show and focus Buggy from anywhere in Windows.
- Closing the window hides it to the notification area. Use the tray icon's **Show** item to restore Buggy or **Exit** to close it.

After updating Buggy, choose **Exit** from its tray icon and run the command above again to load the new interface. Closing the chat window only hides the current process.

The first document scan can take time because it indexes files under your user profile, including hidden folders. Current content extraction supports text, Markdown, PDF, and DOCX files. Re-index from **Find files** if you want to refresh the index.

## Settings and local data

Edit [settings.toml](settings.toml) to change the model, profile search root, global shortcut, approved application list, or motion setting. Set `reduce_motion = true` to show chat updates and mascot expressions without animation.

By default, the conversation database and file index are stored at `%LOCALAPPDATA%\PersonalAssistant\assistant.db`. The index stores extracted text locally to support full-text search. Files are opened only when you select a result.

## Development

Run the test suite with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

The current MVP supports local chat, basic conversation history, document search, selected app launching, and a compact desktop interface. Direct mouse/keyboard control of Windows is not part of this version.
