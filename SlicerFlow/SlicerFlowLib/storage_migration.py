"""One-time: skills and preferences move from the sign-in file's storage into their own
(the reason for ``Talk2View(data_storage=...)`` in ``runtime.py``; the two files are ``host.py``'s ``storage()`` and ``data_storage()``).

Before ``data_storage`` had a file of its own, it defaulted to ``storage`` (auth.json),
which is where ``T2VSkills``/``T2VPreferences`` wrote: an
existing install's font size and any saved skills read as defaults after this upgrade
unless they are copied across once, and the old copy is removed so a later start with an
emptier ``data_storage`` (say, the file was deleted by hand) never migrates it again.

Pure Python (``talk2view.storage``'s ``TokenStorage`` protocol, and the two keys
``T2VSkills``/``T2VPreferences`` themselves define): no Slicer needed, so it is tested
without it.
"""

from talk2view.preferences import STORAGE_KEY as _PREFERENCES_KEY
from talk2view.skills import STORAGE_KEY as _SKILLS_KEY

#: The only keys ``Talk2View(data_storage=...)`` ever wrote into the sign-in file before
#: it had a file of its own. A stored conversation's own key is new here — nothing to
#: migrate for it.
_MIGRATED_KEYS = (_PREFERENCES_KEY, _SKILLS_KEY)


def migrate_once(token_storage, data_storage):
    """Copy each of ``_MIGRATED_KEYS`` from ``token_storage`` into ``data_storage`` when
    ``data_storage`` does not already have it, then remove it from ``token_storage``
    regardless -- a key ``data_storage`` already holds (a second install, or a value
    written there since) is never overwritten, but the old copy is cleared either way, so
    it is never migrated again, wrongly, over a value the new store now owns.

    Idempotent, and meant to be called on every start rather than gated behind an
    "already migrated" flag: the first call that finds a key moves it (or, losing the
    race, just clears it) and leaves nothing behind for a later call to act on.
    """
    for key in _MIGRATED_KEYS:
        value = token_storage.get(key)
        if value is None:
            continue  # nothing left to migrate for this key
        if data_storage.get(key) is None:
            data_storage.set(key, value)
        token_storage.delete(key)
