from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Node:
    value: int
    next: Node | None = None


def to_list(head: Node | None) -> list[int]:
    values = []
    node = head
    while node.next is not None:
        values.append(node.value)
        node = node.next
    return values
