import copy
import unittest
import moving_box as bot


class Checks(unittest.TestCase):
    def position(self):
        s = bot.initial_state(100, 1000)
        bot.step(s, 100, 1000)
        self.assertEqual(bot.step(s, 99, 1180)[1], 'BUY')
        return s

    def test_current_trade_reset_rule_and_three_buy_cap(self):
        s = self.position()
        self.assertTrue(s['ready'])
        self.assertEqual(s['window_anchor'], 99)
        for t, price in ((1360, 98), (1540, 97)):
            self.assertEqual(bot.step(s, price, t)[1], 'BUY')
            self.assertEqual(s['window_anchor'], price)
        self.assertIsNone(bot.step(s, 90, 1720))
        self.assertEqual(s['cost'], 150)
        self.assertEqual(s['buys'], 3)

    def test_trade_reset_cannot_override_positive_profit_guard(self):
        s = self.position()
        bot.reset_window(s, 90)
        before = copy.deepcopy(s)
        self.assertIsNone(bot.step(s, 98, 1360))
        for key in ('cash', 'btc', 'cost', 'buys', 'sells', 'fees', 'realized_profit'):
            self.assertEqual(s[key], before[key])

    def test_profitable_full_sale_resets_reference_and_frees_cap(self):
        s = self.position()
        trade = bot.step(s, 110, 1360)
        self.assertEqual(trade[1], 'SELL')
        self.assertGreater(trade[5], 0)
        self.assertEqual(s['window_anchor'], 110)
        self.assertEqual(s['btc'], 0)
        self.assertEqual(s['cost'], 0)
        self.assertGreater(s['cash'], 1000)


if __name__ == '__main__':
    unittest.main(verbosity=2)
