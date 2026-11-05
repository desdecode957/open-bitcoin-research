"""Install pretested paper-only code with backups and no price/window reset."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time


def normalized_hash(path):
    return hashlib.sha256(path.read_text().strip().encode()).hexdigest()


def atomic_write(path, contents, mode):
    temporary = path.with_name(path.name + '.window-update.tmp')
    with temporary.open('wb') as f:
        os.fchmod(f.fileno(), mode)
        f.write(contents)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def install(target, stage, expected_old_hash):
    source = target / 'ndax_paper.py'
    state_path = target / 'ndax_state.json'
    wrapper = target / 'shift-window.sh'
    with (target / 'ndax.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if normalized_hash(source) != expected_old_hash:
            raise RuntimeError('Program differs from reviewed source; nothing changed')
        if wrapper.exists():
            raise RuntimeError('A shift helper already exists; nothing changed')
        old_code = source.read_bytes()
        old_state = state_path.read_bytes()
        state = json.loads(old_state)
        spec = importlib.util.spec_from_file_location('ndax_upgrade', stage / 'ndax_paper.py')
        bot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bot)
        before = json.loads(old_state)
        bot.pin_sell_target(state)
        # Migration may add/remove only the sell-target metadata.
        for key in before.keys() | state.keys():
            if key != 'position_sell_target' and before.get(key) != state.get(key):
                raise RuntimeError('Unexpected state migration: ' + key)
        if state['btc'] > 0 and bot.window_bounds(state)[2] != bot.window_bounds(before)[2]:
            raise RuntimeError('Migration would change the existing sell line')
        stamp = str(time.time_ns())
        shutil.copy2(source, target / ('ndax_paper.before-movable-' + stamp + '.py'))
        shutil.copy2(state_path, target / ('ndax_state.before-movable-' + stamp + '.json'))
        code_mode = stat.S_IMODE(source.stat().st_mode)
        state_mode = stat.S_IMODE(state_path.stat().st_mode)
        try:
            atomic_write(source, (stage / 'ndax_paper.py').read_bytes(), code_mode)
            atomic_write(state_path, (json.dumps(state, indent=2, allow_nan=False)+'\n').encode(), state_mode)
            atomic_write(wrapper, (stage / 'shift-window.sh').read_bytes(), 0o700)
        except BaseException:
            atomic_write(source, old_code, code_mode)
            atomic_write(state_path, old_state, state_mode)
            if wrapper.exists():
                wrapper.unlink()
            raise
        print('NDAX PAPER update installed. Existing reference was NOT moved.')
        print('History, balances, purchase costs and counters preserved; backups saved.')
        if state['btc'] > 0:
            print('Existing open-position sell target preserved.')
        print('Future manual moves: bash ~/ndax-overton-paper/shift-window.sh')
        print('For USD display, supply a checked rate with --usd-cad RATE.')


if __name__ == '__main__':
    install(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
