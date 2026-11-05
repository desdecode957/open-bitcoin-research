import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import accepted_range_paper as bot


class Checks(unittest.TestCase):
    def warm(self):
        s = bot.initial(100.0)
        for t in range(1, 3602, 180):
            s, trade = bot.step(s, 100.0, t)
            self.assertIsNone(trade)
        self.assertTrue(s['initialized'])
        self.assertEqual(s['anchor'], 100.0)
        return s

    def tick(self, s, price):
        return bot.step(s, price, s['last_ts'] + 180)

    def test_hour_warmup_and_time_weighted_average(self):
        s = bot.initial(100.0)
        for t in range(1, 3602, 180):
            s, trade = bot.step(s, 100.0 if t < 1801 else 200.0, t)
            self.assertIsNone(trade)
            if t < 3601:
                self.assertIsNone(s['average'])
                self.assertFalse(s['initialized'])
        self.assertAlmostEqual(s['average'], 150.0)
        self.assertAlmostEqual(s['anchor'], 150.0)

    def test_persistent_up_move_waits_full_confirmation(self):
        s = self.warm()
        found = False
        for _ in range(50):
            before = copy.deepcopy(s)
            s, _ = self.tick(s, 103.0)
            if s['shifts']:
                self.assertIsNotNone(before['candidate_since'])
                self.assertGreaterEqual(s['last_ts'] - before['candidate_since'], 1800)
                self.assertEqual(s['anchor'], s['average'])
                self.assertEqual(s['events'][-1]['direction'], 1)
                found = True
                break
            self.assertEqual(s['anchor'], 100.0)
        self.assertTrue(found)

    def test_spike_and_broken_confirmation_do_not_shift(self):
        s = self.warm()
        s, _ = self.tick(s, 106.0)
        for _ in range(25):
            s, _ = self.tick(s, 100.0)
        self.assertEqual(s['shifts'], 0)
        s = self.warm()
        for _ in range(9):
            s, _ = self.tick(s, 103.0)
        self.assertEqual(s['candidate_direction'], 1)
        for _ in range(4):
            s, _ = self.tick(s, 97.0)
        self.assertEqual(s['candidate_direction'], 0)
        self.assertEqual(s['shifts'], 0)

    def test_down_shift_preserves_cap_and_cost_based_target(self):
        s = self.warm()
        for _ in range(3):
            s, trade = self.tick(s, 95.0)
            self.assertEqual(trade['side'], 'BUY')
        self.assertEqual(s['anchor'], 100.0)
        target = bot.sell_target(s)
        for _ in range(35):
            s, trade = self.tick(s, 95.0)
            self.assertIsNone(trade)
            self.assertEqual(bot.sell_target(s), target)
            self.assertEqual(s['cost'], 150.0)
            self.assertEqual(s['open_buys'], 3)
        self.assertGreater(s['shifts'], 0)
        self.assertLess(s['anchor'], 100.0)
        s, trade = self.tick(s, target - 0.01)
        self.assertIsNone(trade)
        old_anchor = s['anchor']
        s, trade = self.tick(s, target)
        self.assertEqual(trade['side'], 'SELL')
        self.assertGreaterEqual(trade['profit'], 2.0)
        self.assertAlmostEqual(s['cash'], 1000.0 + trade['profit'])
        self.assertEqual(s['open_buys'], 0)
        self.assertEqual(s['anchor'], old_anchor)

    def test_quote_gap_restarts_hour_and_confirmation(self):
        s = self.warm()
        for _ in range(9):
            s, _ = self.tick(s, 103.0)
        self.assertIsNotNone(s['candidate_since'])
        s, trade = bot.step(s, 90.0, s['last_ts'] + 601)
        self.assertIsNone(trade)
        self.assertIsNone(s['average'])
        self.assertIsNone(s['candidate_since'])
        self.assertEqual(s['shifts'], 0)
        self.assertEqual(s['anchor'], 100.0)
        self.assertEqual(len(s['samples']), 1)

    def test_restart_preserves_confirmation_and_atomic_journal(self):
        s = self.warm()
        for _ in range(9):
            s, _ = self.tick(s, 103.0)
        loaded = json.loads(json.dumps(s))
        self.assertEqual(self.tick(s, 103.0), self.tick(loaded, 103.0))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with patch.object(bot, 'STATE', path):
                bot.save(s)
            self.assertEqual(json.loads(path.read_text()), s)
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_invalid_prices_clock_or_state_never_change_input(self):
        s = self.warm()
        before = copy.deepcopy(s)
        for p, t in ((0, 4000), (float('nan'), 4000), (100, 3601),
                     (100, float('inf'))):
            with self.assertRaises(ValueError):
                bot.step(s, p, t)
            self.assertEqual(s, before)
        for key, value in (('cash', -1), ('cost', 200), ('open_buys', 4)):
            broken = copy.deepcopy(s)
            broken[key] = value
            with self.assertRaises(ValueError):
                self.tick(broken, 100)

    def test_a_single_buy_can_sell_for_two_net_without_moving_reference(self):
        s = self.warm()
        s, trade = self.tick(s, 99)
        self.assertEqual(trade['side'], 'BUY')
        target = bot.sell_target(s)
        s, trade = self.tick(s, target)
        self.assertEqual(trade['side'], 'SELL')
        self.assertGreaterEqual(trade['profit'], 2)
        self.assertEqual(s['anchor'], 100)
        self.assertEqual(s['shifts'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
