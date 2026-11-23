"""Which Talk2View partner key and engine the chat uses, and which 3D Slicer it needs.

The extension ships its partner key in ``Resources/partner.json``. A partner key is a
client-side identifier, not a secret: the TypeScript SDK puts one in every web page that
uses it, and every chat request also needs the end-user's own sign-in. So the key ships
with the extension, and nobody has to type one.

A developer or a partner who builds on SlicerFlow can replace either value, in this
order of precedence:

    1. the environment: T2V_PARTNER_KEY, T2V_BASE_URL
    2. Resources/partner.json (the extension's own)
    3. for the engine only: https://engine.talk2view.com

The module panel has no UI for either value any more (there is nothing to see or clear),
so the settings-saved override is no longer read; see ``SETTINGS_KEY`` / ``SETTINGS_URL``.

Pure Python: it runs without Slicer.
"""

import json
import logging
from collections import namedtuple
from urllib.parse import urlsplit

DEFAULT_ENGINE_URL = "https://engine.talk2view.com"
ENV_KEY = "T2V_PARTNER_KEY"
ENV_URL = "T2V_BASE_URL"
#: Kept only so ``migrate_settings`` still has somewhere to copy an upgrading user's
#: Talk2Slicer values; the module panel has no field for either any more, and
#: ``resolve()`` no longer reads them (the connection is the environment or
#: Resources/partner.json only).
SETTINGS_KEY = "SlicerFlow/PartnerKey"
SETTINGS_URL = "SlicerFlow/EngineUrl"
#: Whether the chat panel (the dock on the right of the main window) was last shown.
SETTINGS_PANEL_SHOWN = "SlicerFlow/ChatPanelShown"
#: Whether the user has started the chat once, after the panel said what it sends: until
#: then nothing starts by itself and nothing goes over the network (``CONSENT_NOTICE``).
SETTINGS_CONSENTED = "SlicerFlow/ChatConsented"
#: Set (to anything but "" or "0") to keep the chat from starting by itself.
ENV_NO_AUTOSTART = "SLICERFLOW_NO_AUTOSTART"
#: Replaces the folder SlicerFlow keeps its files in (``paths.home``).
ENV_HOME = "SLICERFLOW_HOME"

#: The extension was called Talk2Slicer. Each new settings key, and the one it replaces:
#: ``migrate_settings`` copies them across once, and leaves the old ones for an older build.
LEGACY_SETTINGS = {
    SETTINGS_KEY: "Talk2Slicer/PartnerKey",
    SETTINGS_URL: "Talk2Slicer/EngineUrl",
    SETTINGS_PANEL_SHOWN: "Talk2Slicer/ChatPanelShown",
}
#: Set once ``migrate_settings`` has run, so it never runs again: a value the user removes
#: later (the panel's "Use defaults") must not come back from the old key.
SETTINGS_MIGRATED = "SlicerFlow/LegacySettingsMigrated"
#: Each environment variable, and the Talk2Slicer name still read when it is unset.
LEGACY_ENV = {
    ENV_HOME: "TALK2SLICER_HOME",
    ENV_NO_AUTOSTART: "TALK2SLICER_NO_AUTOSTART",
}

NO_PARTNER_KEY = (
    "This copy of SlicerFlow has no partner key. Set T2V_PARTNER_KEY, or add it to "
    "Resources/partner.json."
)

#: What the chat panel says before the chat has ever been started: what leaves this
#: computer once it is. Nothing does until the user presses Start chat.
CONSENT_NOTICE = (
    "The chat runs on Talk2View's servers. Starting it installs the Talk2View SDK from "
    "PyPI (the first time), then sends Talk2View what you type or dictate, what the "
    "assistant's tools find in your scene (such as the names of what is loaded, which for "
    "DICOM data can include the patient's name), and any screenshot you approve, for "
    "Talk2View's AI models to answer. Nothing is sent until you press Start chat."
)
PRIVACY_URL = "https://talk2view.com/legal/privacy-policy"

MIN_SLICER = (5, 12)
UNSUPPORTED_MESSAGE = (
    "SlicerFlow needs 3D Slicer {min} or newer, and this is {have}. Install the current "
    "release from https://download.slicer.org."
)

#: ``key_source`` is where the key came from: "environment", "extension", or "" when there
#: is none. (Callers may still label a ``Connection`` they build themselves "settings" for
#: their own purposes; ``resolve()`` itself never returns it any more.)
Connection = namedtuple("Connection", "partner_key engine_url key_source")

logger = logging.getLogger("SlicerFlow")


def load_bundled(path):
    """``Resources/partner.json`` as a dict; ``{}`` when it is missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("SlicerFlow: ignoring %s (%s)", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _first(*candidates):
    for value, source in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip(), source
    return "", ""


def resolve(environ, bundled):
    """The ``Connection`` to use: the environment (``ENV_KEY`` / ``ENV_URL``), else
    ``bundled`` (``Resources/partner.json``, as ``load_bundled`` reads it)."""
    key, source = _first(
        (environ.get(ENV_KEY), "environment"),
        (bundled.get("partnerKey"), "extension"),
    )
    url, _source = _first(
        (environ.get(ENV_URL), "environment"),
        (bundled.get("engineUrl"), "extension"),
        (DEFAULT_ENGINE_URL, "default"),
    )
    return Connection(key, url.rstrip("/"), source)


def engine_url_problem(url):
    """Why ``url`` cannot be the engine, or None. The partner key and the user's tokens go
    there, so it must be https; plain http is allowed only on this computer, for
    development."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return "The engine URL is not a valid URL."
    if not host:
        return "The engine URL needs a host name, for example {}.".format(DEFAULT_ENGINE_URL)
    if parts.scheme == "https":
        return None
    # Exactly the two the message below and the README name; ``::1`` is loopback too, but
    # an allowance nobody documents is one nobody checked.
    if parts.scheme == "http" and host in ("127.0.0.1", "localhost"):
        return None
    return "The engine URL must start with https:// (plain http only for 127.0.0.1 or localhost)."


def connection_problem(connection):
    """Why the chat cannot start with ``connection``, or None."""
    if not connection.partner_key:
        return NO_PARTNER_KEY
    return engine_url_problem(connection.engine_url)


#: ``qSlicerCommandOptions`` properties (Slicer 5.12) that mark a Slicer run by a script or
#: without a window: ``--python-script``, ``--python-code``, ``--exit-after-startup``,
#: ``--no-main-window``, and ``runPythonAndExit`` (set with the first two). Any of them
#: set means the chat must not start by itself there: it would take the chat lock (and so
#: turn away the user's own Slicer) and could open the SDK install dialog in a batch run.
SCRIPTED_RUN_OPTIONS = (
    "pythonScript", "pythonCode", "exitAfterStartup", "noMainWindow", "runPythonAndExit",
)


def command_options(options):
    """``{name: value}`` for ``SCRIPTED_RUN_OPTIONS``, read from Slicer's command options
    object; a property this Slicer does not have reads as None."""
    return {name: getattr(options, name, None) for name in SCRIPTED_RUN_OPTIONS}


def migrate_settings(settings):
    """Copy each Talk2Slicer setting (``LEGACY_SETTINGS``) that has no SlicerFlow value yet,
    once. ``settings`` is a ``qt.QSettings`` (``contains``, ``value``, ``setValue``). A
    SlicerFlow value is never overwritten, and the old keys are kept, so an older build
    still finds them. Returns the new keys it wrote."""
    if settings.contains(SETTINGS_MIGRATED):
        return []
    copied = []
    for key, legacy in LEGACY_SETTINGS.items():
        if settings.contains(legacy) and not settings.contains(key):
            settings.setValue(key, settings.value(legacy))
            copied.append(key)
    settings.setValue(SETTINGS_MIGRATED, True)
    return copied


def getenv(environ, name, default=None):
    """``environ[name]``, or its Talk2Slicer name's value (``LEGACY_ENV``) when ``name`` is
    unset or empty; the new name wins when both are set."""
    for candidate in (name, LEGACY_ENV.get(name)):
        if candidate and environ.get(candidate):
            return environ[candidate]
    return default


def autostart_allowed(testing, environ, options):
    """Whether the chat may start by itself when Slicer starts (or when its panel is first
    shown). ``options`` is ``command_options(...)``. Never in Slicer's ``--testing`` mode,
    so tests start it themselves; never in a scripted or windowless run
    (``SCRIPTED_RUN_OPTIONS``); never with ``SLICERFLOW_NO_AUTOSTART`` (or the older
    ``TALK2SLICER_NO_AUTOSTART``) set."""
    if testing or any(options.get(name) for name in SCRIPTED_RUN_OPTIONS):
        return False
    return (getenv(environ, ENV_NO_AUTOSTART) or "").strip() in ("", "0")


def setting_is_true(value, default):
    """A boolean read back from ``QSettings``: a bool, or the string an INI file stores
    ("true"/"false"); ``default`` when it was never saved (None)."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def slicer_supported(major, minor):
    return (int(major), int(minor)) >= MIN_SLICER


def unsupported_message(version):
    return UNSUPPORTED_MESSAGE.format(min="{}.{}".format(*MIN_SLICER), have=version)
