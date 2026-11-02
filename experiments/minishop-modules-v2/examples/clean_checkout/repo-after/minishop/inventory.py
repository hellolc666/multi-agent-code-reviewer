"""In-memory stock accounting used by checkout."""


class Inventory:
    def __init__(self, stock: dict[str, int]) -> None:
        if any(quantity < 0 for quantity in stock.values()):
            raise ValueError("stock cannot be negative")
        self._stock = dict(stock)

    def available(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def snapshot(self) -> dict[str, int]:
        return dict(self._stock)

    def restock(self, sku: str, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._stock[sku] = self.available(sku) + quantity

    def reserve(self, sku: str, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.available(sku) < quantity:
            raise ValueError("not enough stock")
        self._stock[sku] -= quantity

    def reserve_many(self, quantities: dict[str, int]) -> None:
        if any(quantity <= 0 for quantity in quantities.values()):
            raise ValueError("quantity must be positive")
        if any(self.available(sku) < quantity for sku, quantity in quantities.items()):
            raise ValueError("not enough stock")
        for sku, quantity in quantities.items():
            self._stock[sku] -= quantity
