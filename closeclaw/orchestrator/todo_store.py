"""Run-scoped TODO store for Phase5 P3."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass
class TodoItem:
    """Minimal todo item tracked during orchestrated runs."""

    id: str
    title: str
    status: str
    updated_at: str
    source_step: int


class TodoStore:
    """In-memory todo store with deterministic update semantics."""

    def __init__(self) -> None:
        self._items: dict[str, TodoItem] = {}

    def upsert(self, item_id: str, title: str, status: str, source_step: int) -> TodoItem:
        timestamp = datetime.now(timezone.utc).isoformat()
        item = TodoItem(
            id=item_id,
            title=title,
            status=status,
            updated_at=timestamp,
            source_step=source_step,
        )
        self._items[item_id] = item
        return item

    def remove(self, item_id: str) -> bool:
        if item_id in self._items:
            del self._items[item_id]
            return True
        return False

    def get(self, item_id: str) -> TodoItem | None:
        return self._items.get(item_id)

    def list_items(self) -> list[TodoItem]:
        return sorted(self._items.values(), key=lambda item: item.id)

    def export_snapshot(self) -> list[dict]:
        return [asdict(item) for item in self.list_items()]

