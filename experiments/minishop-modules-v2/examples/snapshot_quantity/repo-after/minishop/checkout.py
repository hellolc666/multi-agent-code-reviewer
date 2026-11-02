"""Checkout logic that reserves ordered items."""

from minishop.inventory import Inventory


def checkout(inventory: Inventory, sku: str, quantity: int) -> dict[str, int | str]:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if inventory.available(sku) < quantity:
        raise ValueError("not enough stock")
    inventory.reserve(sku, quantity)
    return {"sku": sku, "quantity": quantity}
