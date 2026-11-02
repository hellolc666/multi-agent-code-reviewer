import unittest

from minishop.checkout import checkout
from minishop.inventory import Inventory


class CheckoutTests(unittest.TestCase):
    def test_exact_stock_can_be_purchased(self):
        inventory = Inventory({"book": 2})
        self.assertEqual(checkout(inventory, "book", 2), {"sku": "book", "quantity": 2})
        self.assertEqual(inventory.available("book"), 0)

    def test_insufficient_stock_is_rejected_without_mutation(self):
        inventory = Inventory({"book": 2})
        with self.assertRaisesRegex(ValueError, "not enough stock"):
            checkout(inventory, "book", 3)
        self.assertEqual(inventory.available("book"), 2)

    def test_nonpositive_quantity_is_rejected(self):
        inventory = Inventory({"book": 2})
        with self.assertRaisesRegex(ValueError, "quantity must be positive"):
            checkout(inventory, "book", 0)
        self.assertEqual(inventory.available("book"), 2)


if __name__ == "__main__":
    unittest.main()
