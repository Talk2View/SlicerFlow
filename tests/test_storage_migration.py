"""storage_migration.migrate_once: skills and preferences moving from the sign-in file
into their own (the reason for ``Talk2View(data_storage=...)``)."""

from talk2view import MemoryStorage
from talk2view.preferences import STORAGE_KEY as PREFERENCES_KEY
from talk2view.skills import STORAGE_KEY as SKILLS_KEY
from SlicerFlowLib import storage_migration


def test_moves_both_keys_and_removes_them_from_the_old_store():
    old = MemoryStorage()
    old.set(PREFERENCES_KEY, '{"fontSize": "large"}')
    old.set(SKILLS_KEY, '[{"name": "s1", "content": "x"}]')
    old.set("access_token", "keep-me")  # never touched: not a data_storage key
    new = MemoryStorage()

    storage_migration.migrate_once(old, new)

    assert new.get(PREFERENCES_KEY) == '{"fontSize": "large"}'
    assert new.get(SKILLS_KEY) == '[{"name": "s1", "content": "x"}]'
    assert old.get(PREFERENCES_KEY) is None
    assert old.get(SKILLS_KEY) is None
    assert old.get("access_token") == "keep-me"


def test_a_key_already_in_the_new_store_is_never_overwritten():
    """A second start after the first migration, or two Slicer processes racing: whatever
    the new store already has (possibly written since) must win, not a stale copy from the
    old one."""
    old = MemoryStorage()
    old.set(PREFERENCES_KEY, '{"fontSize": "large"}')
    new = MemoryStorage()
    new.set(PREFERENCES_KEY, '{"fontSize": "small"}')

    storage_migration.migrate_once(old, new)

    assert new.get(PREFERENCES_KEY) == '{"fontSize": "small"}'
    # Still removed from the old store: it already lost the race for good reason, and
    # leaving it there would just migrate it again, wrongly, on a later, emptier start.
    assert old.get(PREFERENCES_KEY) is None


def test_nothing_to_migrate_is_a_quiet_no_op():
    old = MemoryStorage()
    new = MemoryStorage()
    storage_migration.migrate_once(old, new)
    assert old.get(PREFERENCES_KEY) is None
    assert new.get(PREFERENCES_KEY) is None


def test_running_it_twice_moves_nothing_the_second_time():
    old = MemoryStorage()
    old.set(PREFERENCES_KEY, '{"fontSize": "large"}')
    new = MemoryStorage()

    storage_migration.migrate_once(old, new)
    storage_migration.migrate_once(old, new)  # idempotent: safe on every start

    assert new.get(PREFERENCES_KEY) == '{"fontSize": "large"}'
    assert old.get(PREFERENCES_KEY) is None
