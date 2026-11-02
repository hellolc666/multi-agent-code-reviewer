"""Deterministic checkout pricing; all amounts are integer cents."""

from dataclasses import dataclass

from minishop.cart import Cart
from minishop.catalog import Catalog


@dataclass(frozen=True)
class Coupon:
    code: str
    percent_off: int
    minimum_cents: int = 0

    def __post_init__(self) -> None:
        if not self.code.strip() or not 1 <= self.percent_off <= 100:
            raise ValueError("invalid coupon")
        if self.minimum_cents < 0:
            raise ValueError("minimum cannot be negative")


@dataclass(frozen=True)
class Quote:
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    total_cents: int


def quote(
    cart: Cart,
    catalog: Catalog,
    coupon: Coupon | None = None,
    *,
    free_shipping_at_cents: int = 5000,
    shipping_cents: int = 500,
) -> Quote:
    if free_shipping_at_cents < 0 or shipping_cents < 0:
        raise ValueError("shipping amounts cannot be negative")
    lines = cart.lines()
    if not lines:
        raise ValueError("cart is empty")
    subtotal = sum(catalog.purchasable(line.sku).price_cents * line.quantity for line in lines)
    discount = 0
    if coupon is not None:
        if subtotal < coupon.minimum_cents:
            raise ValueError("coupon minimum not met")
        discount = subtotal * coupon.percent_off // 100
    shipping = 0 if subtotal >= free_shipping_at_cents else shipping_cents
    return Quote(subtotal, discount, shipping, subtotal - discount + shipping)
