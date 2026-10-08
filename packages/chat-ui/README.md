# Shared chat UI

`@knowledge-agent/chat-ui/conversation` exports `Conversation` and its message and
composer types. It renders a scrollable message timeline, Markdown answers,
copy/response actions, and an optional composer with Enter to send, Shift+Enter
for newlines and a stop action while running. The shadcn message scroller follows
streaming content and lets the reader scroll back or jump to the latest message.

Callers supply messages, response actions, composer state and send/stop callbacks.
The component does not load agents, call APIs, store sessions or perform tuning.
The administration Test page owns these operations.
The client application can use this export with its own request callbacks.

As with the existing `ChatPanel`, imports under `~/components` and `~/lib` resolve
to each consuming application's Markdown renderer, shadcn controls and utilities.
The administration application scans this package for Tailwind classes. The older
default `ChatPanel` export remains in the package but has no consumer since the
Reader application was removed.

`ui/message.tsx`, `ui/bubble.tsx` and `ui/message-scroller.tsx` were added with the
shadcn CLI from the official Radix Nova registry on 2026-09-11. Local adaptations
reuse the applications' `cn`, Button and Radix Slot imports; no preset or existing
control was overwritten. The scroller uses `@shadcn/react`.
