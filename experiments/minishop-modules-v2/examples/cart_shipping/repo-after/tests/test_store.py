"""Cross-module store behavior and its important boundary conditions."""

import unittest

from minishop.cart import Cart, CartLine
from minishop.catalog import Catalog, Product
from minishop.inventory import Inventory
from minishop.orders import OrderService
from minishop.pricing import Coupon, Quote, quote


def catalog() -> Catalog:
    return Catalog(
        [
            Product("book", "Book", 2000),
            Product("pen", "Pen", 300),
            Product("retired", "Old edition", 100, active=False),
        ]
    )


class CatalogTests(unittest.TestCase):
    def test_rejects_duplicate_sku_and_negative_price(self):
        with self.assertRaisesRegex(ValueError, "duplicate sku"):
            Catalog([Product("book", "Book", 2000), Product("book", "Other", 100)])
        with self.assertRaisesRegex(ValueError, "price cannot be negative"):
            Product("book", "Book", -1)

    def test_rejects_unknown_and_inactive_products(self):
        for sku in ("missing", "retired"):
            with self.subTest(sku=sku), self.assertRaisesRegex(ValueError, "unavailable"):
                catalog().purchasable(sku)


class CartTests(unittest.TestCase):
    def test_repeated_additions_merge_and_remove_clears_line(self):
        cart = Cart()
        cart.add("book", 2)
        cart.add("book")
        cart.add("pen")
        self.assertEqual(cart.lines(), (CartLine("book", 3), CartLine("pen", 1)))
        cart.remove("book")
        self.assertEqual(cart.lines(), (CartLine("pen", 1),))

    def test_rejects_nonpositive_quantity(self):
        cart = Cart()
        with self.assertRaisesRegex(ValueError, "quantity must be positive"):
            cart.add("book", 0)
        self.assertEqual(cart.lines(), ())


class InventoryTests(unittest.TestCase):
    def test_restock_and_snapshot_are_independent(self):
        inventory = Inventory({"book": 1})
        snapshot = inventory.snapshot()
        inventory.restock("book", 2)
        inventory.restock("pen", 1)
        self.assertEqual(snapshot, {"book": 1})
        self.assertEqual(inventory.snapshot(), {"book": 3, "pen": 1})

    def test_batch_reservation_is_all_or_nothing(self):
        inventory = Inventory({"book": 2, "pen": 1})
        with self.assertRaisesRegex(ValueError, "not enough stock"):
            inventory.reserve_many({"book": 2, "pen": 2})
        self.assertEqual(inventory.snapshot(), {"book": 2, "pen": 1})
        inventory.reserve_many({"book": 2, "pen": 1})
        self.assertEqual(inventory.snapshot(), {"book": 0, "pen": 0})


class PricingTests(unittest.TestCase):
    def test_coupon_and_shipping_below_threshold(self):
        cart = Cart()
        cart.add("book")
        cart.add("pen", 2)
        self.assertEqual(
            quote(cart, catalog(), Coupon("SAVE10", 10, minimum_cents=2000)),
            Quote(2600, 260, 500, 2840),
        )

    def test_free_shipping_at_threshold(self):
        cart = Cart()
        cart.add("book", 2)
        cart.add("pen", 4)
        self.assertEqual(quote(cart, catalog()), Quote(5200, 0, 0, 5200))

    def test_empty_cart_and_unavailable_product_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cart is empty"):
            quote(Cart(), catalog())
        cart = Cart()
        cart.add("retired")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            quote(cart, catalog())

    def test_coupon_minimum_is_checked_before_discount(self):
        cart = Cart()
        cart.add("pen")
        with self.assertRaisesRegex(ValueError, "coupon minimum not met"):
            quote(cart, catalog(), Coupon("SAVE10", 10, minimum_cents=2000))


class OrderTests(unittest.TestCase):
    def test_multiline_order_prices_and_reserves_all_items(self):
        inventory = Inventory({"book": 3, "pen": 4})
        cart = Cart()
        cart.add("book", 2)
        cart.add("pen", 2)
        order = OrderService(catalog(), inventory).place(cart, Coupon("SAVE10", 10))
        self.assertEqual(order.lines, (CartLine("book", 2), CartLine("pen", 2)))
        self.assertEqual(order.price, Quote(4600, 460, 500, 4640))
        self.assertEqual(inventory.snapshot(), {"book": 1, "pen": 2})

    def test_exact_stock_order_is_valid(self):
        inventory = Inventory({"book": 2, "pen": 1})
        cart = Cart()
        cart.add("book", 2)
        cart.add("pen", 1)
        order = OrderService(catalog(), inventory).place(cart)
        self.assertEqual(order.price.total_cents, 4800)
        self.assertEqual(inventory.snapshot(), {"book": 0, "pen": 0})

    def test_later_item_failure_keeps_all_stock(self):
        inventory = Inventory({"book": 2, "pen": 1})
        cart = Cart()
        cart.add("book")
        cart.add("pen", 2)
        with self.assertRaisesRegex(ValueError, "not enough stock"):
            OrderService(catalog(), inventory).place(cart)
        self.assertEqual(inventory.snapshot(), {"book": 2, "pen": 1})

    def test_pricing_failure_keeps_stock(self):
        inventory = Inventory({"pen": 2})
        cart = Cart()
        cart.add("pen")
        with self.assertRaisesRegex(ValueError, "coupon minimum not met"):
            OrderService(catalog(), inventory).place(
                cart, Coupon("SAVE10", 10, minimum_cents=2000)
            )
        self.assertEqual(inventory.snapshot(), {"pen": 2})


if __name__ == "__main__":
    unittest.main()
