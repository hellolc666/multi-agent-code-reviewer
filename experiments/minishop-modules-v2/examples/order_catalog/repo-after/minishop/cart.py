"""A cart combines repeated additions of the same SKU."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CartLine:
    sku: str
    quantity: int


class Cart:
    def __init__(self) -> None:
        self._quantities: dict[str, int] = {}

    def add(self, sku: str, quantity: int = 1) -> None:
        if not sku.strip():
            raise ValueError("sku is required")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._quantities[sku] = self._quantities.get(sku, 0) + quantity

    def remove(self, sku: str) -> None:
        self._quantities.pop(sku, None)

    def lines(self) -> tuple[CartLine, ...]:
        return tuple(CartLine(sku, quantity) for sku, quantity in self._quantities.items())
