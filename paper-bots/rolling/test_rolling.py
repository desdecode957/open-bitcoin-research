import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import overton_rolling_paper as bot


class Checks(unittest.TestCase):
    def fresh(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bot, 'DATA', Path(directory)/'state.json'):
            return bot.load()

    def warm(self):
        s = self.fresh()
        for t in range(1, 86402, 180):
            result = bot.step(s, t, 100.0)
            self.assertIsNone(result[-1])
            self.assertEqual(result[0], t >= 86401)
        return s

    def fill(self):
        s = self.warm()
        for price in (98.5, 98.0, 97.5):
            trade = bot.step(s, s['samples'][-1][0]+180, price)[-1]
            self.assertEqual(trade['side'], 'BUY')
        return s

    def test_observed_warmup_and_arithmetic_reference(self):
        s = self.warm()
        self.assertEqual(s['first_sample_ts'], 1)
        result = bot.step(s, s['samples'][-1][0]+180, 103)
        self.assertTrue(result[0])
        self.assertGreater(result[1], 100)
        self.assertAlmostEqual(result[2], result[1]*.99)
        self.assertAlmostEqual(result[3], result[1]*1.04)

    def test_three_buy_cap_and_profitable_full_exit(self):
        s = self.fill()
        self.assertEqual(s['cost'], 150)
        self.assertEqual(s['cash'], 10)
        self.assertIsNone(bot.step(s, s['samples'][-1][0]+180, 90)[-1])
        trade = bot.step(s, s['samples'][-1][0]+180, 110)[-1]
        self.assertEqual(trade['side'], 'SELL')
        self.assertGreater(trade['profit'], 0)
        self.assertEqual(s['btc'], 0)
        self.assertEqual(s['cost'], 0)
        self.assertEqual(s['buys_this_cycle'], 0)
        self.assertAlmostEqual(s['cash'], 160+s['realized'])

    def test_falling_reference_does_not_override_cost_and_buffer(self):
        s = self.fill()
        now = s['samples'][-1][0]
        s['samples'] = [[now-(99-i)*180, 90.0] for i in range(100)]
        before = copy.deepcopy(s)
        result = bot.step(s, now+180, 95)
        self.assertTrue(result[0])
        self.assertGreaterEqual(95, result[3])
        self.assertIsNone(result[-1])
        for key in ('cash', 'btc', 'cost', 'fees', 'buys', 'sells', 'realized'):
            self.assertEqual(s[key], before[key])


if __name__ == '__main__':
    unittest.main(verbosity=2)
