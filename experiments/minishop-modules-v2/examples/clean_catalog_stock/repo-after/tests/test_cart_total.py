import unittest

from minishop.cart import Cart


class CartTotalTests(unittest.TestCase):
    def test_total_counts_units(self):
        cart = Cart()
        self.assertEqual(cart.total_quantity(), 0)
        cart.add('book', 2)
        cart.add('pen', 3)
        self.assertEqual(cart.total_quantity(), 5)
