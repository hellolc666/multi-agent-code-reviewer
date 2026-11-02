"""Place an order without partially consuming stock on validation failure."""

from dataclasses import dataclass

from minishop.cart import Cart, CartLine
from minishop.catalog import Catalog
from minishop.checkout import checkout
from minishop.inventory import Inventory
from minishop.pricing import Coupon, Quote, quote


@dataclass(frozen=True)
class Order:
    lines: tuple[CartLine, ...]
    price: Quote


class OrderService:
    def __init__(self, catalog: Catalog, inventory: Inventory) -> None:
        self._catalog = catalog
        self._inventory = inventory

    def place(self, cart: Cart, coupon: Coupon | None = None) -> Order:
        price = quote(cart, self._catalog, coupon)
        lines = cart.lines()

        # Validate every line using checkout's business rule on a temporary copy.
        # The real inventory changes only after all lines pass.
        staged = Inventory(self._inventory.snapshot())
        for line in lines:
            checkout(staged, line.sku, line.quantity)
        self._inventory.reserve_many({line.sku: line.quantity for line in lines})
        return Order(lines, price)
