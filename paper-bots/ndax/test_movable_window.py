import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import ndax_paper as bot
import apply_update as updater


class Checks(unittest.TestCase):
    def quote(self, price, bid=None, ask=None):
        bot.LAST_QUOTE = dict(bid=price-.01 if bid is None else bid,
                              ask=price+.01 if ask is None else ask,
                              bid_qty=100.0, ask_qty=100.0)

    def ready(self):
        self.quote(100)
        s = bot.initial_state(100, 13600)
        s['samples'] = [[t, 100.0] for t in range(13600, 100001, 300)]
        s['last_check'] = bot.iso(100000)
        bot.reset_window(s, 100)
        self.assertTrue(bot.box(s, 100000)[1])
        return s

    def tick(self, s, price, bid=None, ask=None):
        self.quote(price, bid, ask)
        return bot.step(s, price, s['samples'][-1][0]+180)

    def position(self):
        s = self.ready()
        self.assertEqual(self.tick(s, 99)[1], 'BUY')
        return s

    def test_flat_shift_moves_both_lines_and_preserves_history_balances(self):
        s = self.ready()
        before = copy.deepcopy(s)
        bot.move_window(s, 110, 100001)
        self.assertEqual(bot.window_bounds(s), (110, 108.9, 114.4))
        for key in ('cash', 'btc', 'cost', 'buys', 'sells', 'fees', 'samples',
                    'started_ts', 'created_at', 'benchmark_btc', 'realized_profit'):
            self.assertEqual(s[key], before[key])
        self.assertTrue(bot.box(s, 100001)[1])

    def test_open_position_shift_preserves_sell_and_original_costs(self):
        s = self.position()
        before = copy.deepcopy(s)
        target = bot.window_bounds(s)[2]
        for reference in (110, 90, 120):
            bot.move_window(s, reference, 100181)
            self.assertEqual(bot.window_bounds(s)[2], target)
            self.assertAlmostEqual(bot.window_bounds(s)[1], reference * .99)
            for key in ('cash', 'btc', 'cost', 'fees', 'buys', 'sells',
                        'samples', 'started_ts', 'benchmark_btc'):
                self.assertEqual(s[key], before[key])

    def test_first_buy_pins_target_and_additional_buys_keep_it(self):
        s = self.position()
        target = 99 * 1.04
        self.assertEqual(bot.window_bounds(s)[2], target)
        for price in (98, 97):
            self.assertEqual(self.tick(s, price)[1], 'BUY')
            self.assertEqual(bot.window_bounds(s)[2], target)
        self.assertEqual(s['cost'], 150)
        self.assertEqual(s['buys'], 3)

    def test_shifting_does_not_free_buy_slots_or_spending(self):
        s = self.position()
        self.tick(s, 98)
        self.tick(s, 97)
        for reference in (110, 120, 130):
            bot.move_window(s, reference, 101000)
            self.assertIsNone(self.tick(s, 96))
            self.assertEqual(s['cost'], 150)
            self.assertEqual(s['cash'], 850)
            self.assertEqual(s['buys'], 3)

    def test_profitable_sale_precedes_overlapping_buy_and_clears_target(self):
        s = self.position()
        bot.move_window(s, 110, 100181)
        # 105 is below new buy line 108.9, but above existing sell target 102.96.
        trade = self.tick(s, 105)
        self.assertEqual(trade[1], 'SELL')
        self.assertGreater(trade[5], 0)
        self.assertEqual(s['buys'], 1)
        self.assertEqual(s['sells'], 1)
        self.assertEqual(s['btc'], 0)
        self.assertEqual(s['cost'], 0)
        self.assertNotIn('position_sell_target', s)
        self.assertEqual(bot.window_bounds(s), (105, 105*.99, 105*1.04))

    def test_bid_spread_fees_and_slippage_can_block_unprofitable_sale(self):
        s = self.position()
        before = copy.deepcopy(s)
        self.assertIsNone(self.tick(s, 103, bid=97, ask=109))
        for key in ('cash', 'btc', 'cost', 'fees', 'buys', 'sells', 'realized_profit'):
            self.assertEqual(s[key], before[key])

    def test_legacy_position_adopts_exact_old_sell_line(self):
        s = self.position()
        s.pop('position_sell_target')
        s['upper'] = 101.5
        before = copy.deepcopy(s)
        bot.pin_sell_target(s)
        self.assertEqual(s['position_sell_target'], 101.5)
        self.assertEqual({k:v for k,v in s.items() if k != 'position_sell_target'}, before)
        bot.move_window(s, 120, 100181)
        self.assertEqual(bot.window_bounds(s)[2], 101.5)

    def test_invalid_reset_does_not_mutate_position(self):
        s = self.position()
        for price in (0, -1, float('nan'), float('inf')):
            before = copy.deepcopy(s)
            with self.assertRaises(ValueError):
                bot.move_window(s, price, 100181)
            self.assertEqual(s, before)

    def test_manual_command_backs_up_and_does_not_trade_or_reset_warmup(self):
        s = self.position()
        before = copy.deepcopy(s)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            state = folder / 'ndax_state.json'
            state.write_text(json.dumps(s))
            original_bytes = state.read_bytes()
            with patch.object(bot, 'BASE', folder), patch.object(bot, 'STATE', state), \
                 patch.object(bot, 'TRADES', folder / 'ndax_trades.csv'), \
                 patch.object(bot, 'verify_market'), patch.object(bot, 'price_now', return_value=110), \
                 patch('sys.argv', ['ndax_paper.py', '--shift-window', '--usd-cad', '1.3961']), \
                 contextlib.redirect_stdout(io.StringIO()):
                bot.main()
            loaded = json.loads(state.read_text())
            self.assertEqual(loaded['position_sell_target'], before['position_sell_target'])
            self.assertEqual(loaded['samples'], before['samples'])
            self.assertEqual(loaded['cost'], before['cost'])
            self.assertEqual(loaded['cash'], before['cash'])
            self.assertEqual(loaded['buys'], before['buys'])
            self.assertEqual(loaded['sells'], before['sells'])
            self.assertFalse((folder / 'ndax_trades.csv').exists())
            backups = list(folder.glob('ndax_state.before-shift-*.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original_bytes)

    def test_install_preserves_existing_state_and_pins_legacy_target(self):
        s = self.position()
        s.pop('position_sell_target')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            old_code = b'# existing reviewed source\n'
            (target / 'ndax_paper.py').write_bytes(old_code)
            original_state = json.dumps(s).encode()
            (target / 'ndax_state.json').write_bytes(original_state)
            expected = hashlib.sha256(old_code.strip()).hexdigest()
            stage = Path(__file__).resolve().parent
            with contextlib.redirect_stdout(io.StringIO()):
                updater.install(target, stage, expected)
            result = json.loads((target / 'ndax_state.json').read_text())
            self.assertEqual(result['position_sell_target'], s['upper'])
            self.assertEqual({k:v for k,v in result.items() if k != 'position_sell_target'}, s)
            self.assertEqual((target / 'ndax_paper.py').read_bytes(), (stage / 'ndax_paper.py').read_bytes())
            self.assertTrue((target / 'shift-window.sh').exists())
            backups = list(target.glob('ndax_state.before-movable-*.json'))
            self.assertEqual(backups[0].read_bytes(), original_state)

    def test_install_failure_rolls_back_source_and_state(self):
        s = self.position()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            source = target / 'ndax_paper.py'
            state = target / 'ndax_state.json'
            source.write_bytes(b'# original source\n')
            state.write_text(json.dumps(s))
            old_code, old_state = source.read_bytes(), state.read_bytes()
            expected = hashlib.sha256(old_code.strip()).hexdigest()
            real_write = updater.atomic_write
            failed = []
            def fail_once(path, contents, mode):
                if path == state and not failed:
                    failed.append(True)
                    raise OSError('simulated state write failure')
                real_write(path, contents, mode)
            with patch.object(updater, 'atomic_write', side_effect=fail_once):
                with self.assertRaises(OSError):
                    updater.install(target, Path(__file__).resolve().parent, expected)
            self.assertEqual(source.read_bytes(), old_code)
            self.assertEqual(state.read_bytes(), old_state)
            self.assertFalse((target / 'shift-window.sh').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
