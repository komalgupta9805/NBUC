from dataclasses import dataclass, field


@dataclass
class Conversation:
    pending_intent: dict | None = None
    selected_recipe: str | None = None
    selected_topology: str | None = None
    collected_parameters: dict[str, int] = field(default_factory=dict)
    last_job_id: str | None = None
    history: list[dict] = field(default_factory=list)


class ConversationStore:
    def __init__(self):
        self._conversations: dict[str, Conversation] = {}

    def get(self, conversation_id: str) -> Conversation:
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = Conversation()

        return self._conversations[conversation_id]


conversations = ConversationStore()