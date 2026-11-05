import copy
from datetime import datetime, timezone
import unittest
import trend_paper as bot

WEEK = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())


def records(week, close=110.):
    data = {week-i*86400: dict(day=week-i*86400, valid=True, close=100., high=101., low=99.)
            for i in range(200)}
    data[week].update(close=close, high=max(101., close), low=min(99., close))
    return data


class Checks(unittest.TestCase):
    def state(self):
        return bot.new_state(100., WEEK+86000)

    def test_average_uses_200_completed_closes_including_signal_day(self):
        data = records(WEEK)
        data[WEEK+86400] = dict(valid=True, close=10000.)
        self.assertAlmostEqual(bot.average(data, WEEK), 100.05)
        data[WEEK-199*86400]['valid'] = False
        self.assertIsNone(bot.average(data, WEEK))

    def test_signal_week_never_uses_unfinished_sunday(self):
        self.assertEqual(bot.signal_week(WEEK+12*3600), WEEK-7*86400)
        self.assertEqual(bot.signal_week(WEEK+86460), WEEK)
        self.assertIsNone(bot.signal(100, 100, False))
        self.assertIsNone(bot.signal(100, None, False))

    def test_fresh_weekly_entry_and_signal_exit_can_realize_loss(self):
        s = self.state()
        trade = bot.process_week(s, records(WEEK), WEEK, 110., WEEK+86460)
        self.assertEqual(trade['side'], 'BUY')
        self.assertEqual(s['cost'], 150)
        self.assertIsNone(bot.process_week(s, records(WEEK), WEEK, 111., WEEK+86461))
        next_week = WEEK+7*86400
        data = records(next_week, 90.)
        data[WEEK] = records(WEEK)[WEEK]
        trade = bot.process_week(s, data, next_week, 90., next_week+86460)
        self.assertEqual(trade['side'], 'SELL')
        self.assertLess(trade['profit'], 0)
        self.assertEqual(s['btc'], 0)
        bot.validate_state(s)

    def test_stale_and_invalid_closing_signals_do_not_trade(self):
        s = self.state()
        self.assertIsNone(bot.process_week(s, records(WEEK), WEEK, 110., WEEK+86400+901))
        self.assertEqual(s['trades'], [])
        s = self.state()
        data = records(WEEK); data[WEEK]['valid'] = False
        self.assertIsNone(bot.process_week(s, data, WEEK, 110., WEEK+86460))
        self.assertIsNone(s['last_processed_week'])

    def test_close_only_validation_and_explicit_migration_preserve_ledger(self):
        close_bar = [[WEEK+86100, 99., 101., 100., 100., 1.]]
        self.assertTrue(bot.record_from_candles(bot.validate_candles(close_bar, WEEK), WEEK)['valid'])
        self.assertFalse(bot.record_from_candles([[WEEK+85800, 99., 101., 100., 100., 1.]], WEEK)['valid'])
        s = self.state(); s['protocol_hash'] = bot.LEGACY_FINGERPRINT
        before = copy.deepcopy(s)
        updated = bot.migrate_close_policy(s, WEEK+86460)
        for key in ('cash', 'btc', 'cost', 'fees', 'trades', 'started_ts', 'last_processed_week'):
            self.assertEqual(updated[key], before[key])
        self.assertEqual(s, before)
        self.assertTrue(updated['refresh_average'])
        bot.validate_state(updated)


if __name__ == '__main__':
    unittest.main(verbosity=2)
