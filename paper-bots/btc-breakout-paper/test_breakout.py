import copy
import unittest
import breakout_paper as bot

DAY = 3000*86400


def records(day):
    result = {day-i*86400: dict(day=day-i*86400, valid=True, high=101., low=90., close=100.)
              for i in range(1, 61)}
    result[day] = dict(day=day, valid=True, high=103., low=99., close=102.)
    return result


class Checks(unittest.TestCase):
    def state(self):
        return bot.new_state(100., DAY+86000)

    def test_prior_levels_exclude_signal_day_and_require_valid_history(self):
        data = records(DAY)
        self.assertEqual(bot.levels(data, DAY), (101., 90.))
        data[DAY-60*86400]['valid'] = False
        self.assertEqual(bot.levels(data, DAY), (None, 90.))

    def test_fresh_entry_one_decision_and_signal_exit_can_realize_loss(self):
        s = self.state()
        data = records(DAY)
        trade = bot.process_day(s, data, DAY, 102., DAY+86460)
        self.assertEqual(trade['side'], 'BUY')
        self.assertEqual(s['cost'], 150)
        self.assertEqual(s['cash'], 850)
        self.assertIsNone(bot.process_day(s, data, DAY, 103., DAY+86461))
        next_day = DAY+86400
        data = records(next_day)
        data[DAY] = records(DAY)[DAY]
        data[next_day] = dict(day=next_day, valid=True, high=81., low=79., close=80.)
        trade = bot.process_day(s, data, next_day, 80., next_day+86460)
        self.assertEqual(trade['side'], 'SELL')
        self.assertLess(trade['profit'], 0)  # Research rule deliberately lacks an Overton profit floor.
        self.assertEqual(s['btc'], 0)
        bot.validate_state(s)

    def test_stale_and_incomplete_signals_do_not_create_trades(self):
        s = self.state()
        self.assertIsNone(bot.process_day(s, records(DAY), DAY, 102., DAY+86400+901))
        self.assertEqual(s['trades'], [])
        s = self.state()
        data = records(DAY)
        data[DAY]['valid'] = False
        self.assertIsNone(bot.process_day(s, data, DAY, 102., DAY+86460))
        self.assertIsNone(s['last_processed_day'])
        self.assertEqual(s['trades'], [])

    def test_candle_boundary_requirements_and_conflicting_duplicates(self):
        rows = [[DAY+i*300, 99., 101., 100., 100., 1.] for i in range(288)]
        parsed = bot.validate_candles(rows, DAY)
        self.assertTrue(bot.record_from_candles(parsed, DAY)['valid'])
        self.assertFalse(bot.record_from_candles(parsed[:-1], DAY)['valid'])
        changed = list(rows[0]); changed[4] = 100.5
        with self.assertRaises(ValueError):
            bot.validate_candles(rows+[changed], DAY)

    def test_fee_migration_preserves_account_and_rejects_ledger_tampering(self):
        s = self.state()
        s['protocol_hash'] = bot.LEGACY_FINGERPRINT
        s['benchmark_btc'] = 150*(1-bot.LEGACY_FEE)/(100*(1+bot.SLIP))
        before = copy.deepcopy(s)
        migrated = bot.migrate_fee_state(s, DAY+86460)
        for key in ('cash', 'btc', 'cost', 'fees', 'trades', 'started_ts'):
            self.assertEqual(migrated[key], before[key])
        self.assertEqual(s, before)
        bot.validate_state(migrated)
        migrated['cash'] += 1
        with self.assertRaises(ValueError):
            bot.validate_state(migrated)


if __name__ == '__main__':
    unittest.main(verbosity=2)
