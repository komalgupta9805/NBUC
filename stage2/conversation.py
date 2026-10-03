from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Conversation:
    pending_intent: dict | None = None
    selected_recipe: str | None = None
    selected_topology: str | None = None
    collected_parameters: dict[str, int] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)


class ConversationStore:
    def __init__(self) -> None:
        self._items: dict[str, Conversation] = {}

    def get(self, conversation_id: str) -> Conversation:
        return self._items.setdefault(conversation_id, Conversation())


conversations = ConversationStore()
