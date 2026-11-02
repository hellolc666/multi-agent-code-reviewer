"""Product catalog with prices stored as integer cents."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_cents: int
    active: bool = True

    def __post_init__(self) -> None:
        if not self.sku.strip() or not self.name.strip():
            raise ValueError("sku and name are required")
        if self.price_cents < 0:
            raise ValueError("price cannot be negative")


class Catalog:
    def __init__(self, products: list[Product]) -> None:
        self._products: dict[str, Product] = {}
        for product in products:
            if product.sku in self._products:
                raise ValueError("duplicate sku")
            self._products[product.sku] = product

    def purchasable(self, sku: str) -> Product:
        product = self._products.get(sku)
        if product is None or not product.active:
            raise ValueError("product is unavailable")
        return product
