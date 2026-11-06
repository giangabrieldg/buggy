# Buggy compact chat motion and expressions — design

**Date:** 2026-11-06  
**Status:** Approved for implementation  
**App:** Buggy, the local Windows assistant

## Goal

Make the compact chat feel responsive and alive while keeping it easy to read and fully usable offline. Buggy's mascot should react both to what the app is doing and, when the meaning is clear, to the conversation's tone.

## Current starting point

- The CustomTkinter window starts at 480 × 620 pixels and can shrink to 380 × 460 pixels. It has a forest-green palette and a static green mascot in the header and assistant bubbles.
- The user's message appears immediately, but the assistant's answer appears all at once. Completing a reply rebuilds every bubble, which risks visible flicker.
- Ollama calls run on a worker thread. The worker currently also changes `ChatSession.messages` and schedules Tk callbacks. UI state and animation should instead be owned by the Tk thread.
- Conversation history is stored in SQLite. The desktop shortcut, tray behavior, document search, and application commands already exist.

## Design choice

Use short, event-driven motion with distinct mascot images. The assistant reply will stream into a single active bubble, and a small typing indicator will cover the delay before the first visible text. Use one locally stored image per expression, all derived from the approved green mascot. Classify tone with conservative local rules and fall back to neutral when uncertain.

| Approach | Benefit | Cost | Decision |
| --- | --- | --- | --- |
| Static mascot plus animated dots | Fastest to build | Does not deliver facial expressions | No |
| Local image variants plus rule-based tone | Offline, quick reaction, predictable on a small model | Some nuanced tones stay neutral | **Use for this release** |
| Ask Qwen for a second emotion label | Can read nuance | Adds local inference latency and may mislabel short replies | Consider later |

Emotion is presentation only. It never changes the assistant's words, the user's message, or saved history.

## Visual direction

Keep the current deep forest background, green chat surfaces, light text, and bright leaf-green accent. The mascot retains its present silhouette and crop. Create matching transparent PNG variants with the same scale and framing:

| Expression | Face cue | When shown |
| --- | --- | --- |
| Neutral | Existing approved mascot | Idle, older messages, uncertain tone |
| Thinking | Focused eyes and subtle glow | Waiting for Ollama or another app action |
| Happy | Soft smiling eyes | Clearly playful or celebratory reply |
| Curious | Attentive, slightly raised eyes | Clear discovery or question-oriented reply |
| Caring | Gentle eyes, subdued glow | Clear comfort or support reply |
| Concerned | Gentle worried look | A failed request or interruption |

Avoid sharp or distressed faces. The header avatar and the avatar beside the current assistant bubble share the active expression. Older assistant bubbles stay neutral so reopening history does not imply a fresh emotional reaction. The titlebar and tray icon continue using the existing companion artwork.

## Motion and interaction

1. **Send:** Keep the user's new bubble visible immediately. Give that bubble a brief 150–180 ms color reveal. Set Buggy to thinking and show three animated dots in a temporary assistant bubble.
2. **First reply text:** Replace the dots in place with the first visible answer text. Stream later text into that same bubble, batching screen updates about every 50 ms. Keep the text itself readable; do not type it out character by character after receiving it.
3. **Completion:** Save the complete reply once, remove the temporary state, choose a high-confidence expression from the user/reply pair, and keep that expression for about four seconds before returning to neutral. Give the avatar a brief 150–180 ms transition.
4. **Failure:** Remove any unsaved partial assistant text, show a clear error status, and briefly use the concerned face. The already submitted user message remains in history.
5. **Commands and search:** Use the same restrained reveal for newly added chat rows. Use thinking while a search is running, and show neutral or concerned after the result according to success or failure. Do not add a mascot reaction for every file result.

Animation uses Tk's nonblocking `after` timer. The input stays editable while a reply is running so the user can draft a follow-up, but Send stays disabled until that reply finishes. Pressing Enter during generation leaves the draft intact. Motion must not block scrolling, switching chats, or using the global shortcut. A temporary bubble is an in-memory view, not an SQLite message.

**Scroll behavior:** Follow new content only when the reader was already near the bottom. If they scroll up to read an earlier message, incoming chunks leave their position alone. Switching conversations renders the selected history immediately; a reply still in progress appears only in its originating conversation.

**Reduced motion:** Add `reduce_motion = false` to settings. When true, show each new element and expression immediately, with a static `Thinking…` indicator. Cancel visual motion on window hide and exit. A hidden window still consumes worker completion/error events, resets busy state, and refreshes saved history when shown; it simply skips visual animation. Long text and rapid responses should not create a backlog of frames.

## Expression policy

Activity has priority over tone: **error → thinking → completed-reply tone → neutral**. Completed-reply tone can be happy, curious, caring, or neutral. The classifier uses a small, testable set of high-confidence phrase and context rules on the user's message and Buggy's completed reply; contradictory or ambiguous cues produce neutral. It must not infer a person's mental state from one sentence. This deliberately favors a calm face over an incorrect reaction. Future model-based labeling can be added behind the same expression policy without changing the UI contract.

## Technical boundaries

- Keep inference at `127.0.0.1` via Ollama. [Ollama's Python streaming API](https://docs.ollama.com/capabilities/streaming) provides content chunks; thinking-capable models can send a separate `thinking` field. Only `message.content` belongs in the visible bubble or saved transcript.
- Use a thread-safe event queue from the chat worker to the Tk thread. Tk polls it with `after`; widgets, session view state, expression state, and timers change only on the Tk thread. [`CTk.after`](https://customtkinter.tomschimansky.com/documentation/windows/window/) supports this scheduling model.
- Keep expression images as packaged local assets. [`CTkImage`](https://customtkinter.tomschimansky.com/documentation/utility-classes/image/) can be reconfigured and supports high-DPI display when the source image is sufficiently large.
- Keep the existing history schema. Persist the user's message once when submitted and the assistant's completed reply once. Do not save partial streamed chunks, typing dots, or emotion labels.
- Give each request a unique generation ID and its conversation ID. Consume every completion/error event while the app is alive so request state is settled. Render chunks only for the active, visible conversation; skip stale visual updates after switching views or hiding the window. Discard events after app shutdown. A hidden window may finish and save a reply; its UI catches up when shown.

## Acceptance criteria

- A sent message appears immediately, and a slow local model shows a thinking face and dots until answer text arrives.
- Answer text becomes visible progressively without rebuilding existing bubbles or exposing model thinking text.
- The avatar reacts to app activity and clearly expressed reply tone, then returns to neutral. Uncertain replies remain neutral.
- Chat switching during generation keeps chunks out of the wrong conversation; the saved full reply is present on return.
- A failed or interrupted stream leaves no saved partial assistant answer and restores the input controls.
- A stream with no visible content reports a readable error, saves no assistant answer, and restores Send while preserving any follow-up draft.
- Reduced motion, 380-pixel width, high-DPI scaling, tray hide/show, search, and the Windows shortcut remain usable.
- No paid API, internet connection, new database table, or new runtime animation dependency is needed.

## Explicit limits

The expression classifier will miss some subtle humor and empathy. That is acceptable for this release because a neutral expression is safer than a misleading one. This pass does not add voice, lip syncing, a character body, or a settings screen; `reduce_motion` is configured in `settings.toml`.
