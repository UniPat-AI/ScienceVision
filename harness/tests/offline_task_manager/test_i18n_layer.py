"""Contract tests for the shared language layer.

The platform ships a Chinese-first interface with an English toggle. The
layer is deliberately thin — a key/value table plus `data-i18n` attributes —
so these tests pin the parts that silently rot: an untranslated key, an
English default leaking back into the markup, or the asset falling behind
the authentication gate.
"""

from __future__ import annotations

import ast
import contextlib
import functools
import http.cookiejar
import http.server
import json
import re
import socket
import socketserver
import threading
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import pytest

from tools.offline_task_manager.online_auth import create_credentials, load_credentials
from tools.offline_task_manager.online_server import Handler, OnlineServer

ONLINE_APP_ROOT = Path("tools/offline_task_manager/online_app")
I18N_PATH = ONLINE_APP_ROOT / "i18n.js"
LOGIN_HTML_PATH = ONLINE_APP_ROOT / "login.html"
LOGIN_JS_PATH = ONLINE_APP_ROOT / "login.js"
EXPERT_HTML_PATH = Path("tools/offline_task_manager/expert_app/index.html")
EXPERT_JS_PATH = Path("tools/offline_task_manager/expert_app/expert-app.js")
MANAGER_HTML_PATH = Path("tools/offline_task_manager/manager_app/index.html")
MANAGER_JS_PATH = Path("tools/offline_task_manager/manager_app/app.js")
# Every module a browser request can reach: the online gateway and the local
# console that answer requests, the store they drive, the contract the store
# validates against, and the auth, packaging, export and review helpers they
# call.
SERVER_MODULE_PATHS = tuple(
    Path("tools/offline_task_manager") / name
    for name in (
        "online_server.py", "server.py", "store.py", "contract.py", "bundle.py",
        "online_auth.py", "review_bundles.py", "results_export.py", "packager.py",
        "trajectories.py",
    )
)
# Keys whose name is the English text arriving over a boundary rather than a
# name of our own: a rejection worded by the server or thrown by the workspace,
# and the vocabulary a viewer declares for itself. Chinese only, so an English
# reader is shown the wording exactly as it was sent.
WIRE_KEYED_PREFIXES = ("error.", "viewer.")
# What the viewers declared when this catalog was written. The viewer is the
# contract; this only exists so the wording for it can be pinned.
VIEWER_VOCABULARY_PATH = Path(
    "tests/offline_task_manager/data/viewer_tool_vocabulary.json"
)
# What the viewer frame drew when this catalog was written: Mol*'s own panel and
# the viewer pages' chrome. Same role as the file above -- the frame is the
# contract, this pins the wording -- with the strings a loaded structure supplies
# listed separately, because those must never be keyed.
VIEWER_FRAME_VOCABULARY_PATH = Path(
    "tests/offline_task_manager/data/viewer_frame_vocabulary.json"
)
MCP_ROOT = Path("tools/offline_task_manager/expert_app/mcp")
# Vendored runtime files whose copy is routed through the language layer, each
# with the key namespace it owns.
LOCALIZED_MCP_SCRIPTS = (
    ("task-mcp-controls.js", "mcp"),
    ("task-trajectory.js", "trajectory"),
)

# Elements whose text is data rather than copy must drop their placeholder key
# once real content arrives, or a language switch would overwrite it.
DATA_BEARING_ELEMENTS = ("els.packageMeta", "els.taskTitle")

# Same obligation on the manager side: the activity log, the signed-in
# operator, and the publish status line all start as translated copy and end up
# holding run output. Each pair is (element id, local handle in app.js).
MANAGER_DATA_BEARING_ELEMENTS = (
    ("operation-log", "target"),
    ("admin-name", "adminName"),
    ("publish-status", "status"),
)

# Buttons each shell already keeps in its header. The language switch shares
# that corner, so it must join the row instead of floating over it.
HEADER_CONTROL_IDS = {
    "expert": ("viewer-focus-toggle", "save-draft", "export-results", "logout"),
    "manager": ("refresh",),
}

KEY_PATTERN = re.compile(r"^\s*'([^']+)':", re.MULTILINE)
CJK_PATTERN = re.compile(r"[一-鿿]")


def _block(source: str, opening: str) -> str:
    """Return the brace-balanced body that follows `opening` in `source`."""
    start = source.index(opening) + len(opening)
    depth = 1
    for index in range(start, len(source)):
        character = source[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise AssertionError(f"unbalanced block after {opening!r}")


def _table_keys(language: str) -> set[str]:
    source = I18N_PATH.read_text(encoding="utf-8")
    opening = "'zh-CN': {" if language == "zh-CN" else "\n    en: {"
    return set(KEY_PATTERN.findall(_block(source, opening)))


def _markup_entries(path: Path) -> list[tuple[str, str]]:
    """Return (key, inline default text) for every data-i18n element."""
    markup = path.read_text(encoding="utf-8")
    pattern = re.compile(r'data-i18n="([\w.\-]+)"[^>]*>([^<]*)<')
    return pattern.findall(markup)


@pytest.fixture
def language_server(imported_workflow: dict) -> dict:
    root: Path = imported_workflow["tmp"] / "language-server"
    credentials = root / "credentials.json"
    plaintext = root / "handoff.csv"
    secret = root / "session.key"
    create_credentials(
        imported_workflow["store"],
        credentials_path=credentials,
        plaintext_path=plaintext,
        session_secret_path=secret,
    )
    server = OnlineServer(("127.0.0.1", 0), Handler)
    server.store = imported_workflow["store"]
    server.store_lock = threading.RLock()
    server.base_path = "/sv-annotate"
    server.users = load_credentials(credentials)
    server.credentials_path = credentials
    server.session_secret = secret.read_bytes()
    server.secure_cookie = False
    server.login_attempts = defaultdict(deque)
    server.runtime_manifests = {}
    server.review_bundles = {}
    server.review_servers = {}
    server.public_origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"base": f"http://127.0.0.1:{server.server_port}/sv-annotate"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_language_asset_is_served_before_authentication(language_server: dict) -> None:
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    with opener.open(language_server["base"] + "/static/i18n.js", timeout=5) as response:
        assert response.status == 200
        assert "javascript" in response.headers["Content-Type"]
        body = response.read().decode("utf-8")
    assert "SVI18n" in body
    assert "'zh-CN'" in body


def test_default_language_is_chinese() -> None:
    source = I18N_PATH.read_text(encoding="utf-8")
    assert "const DEFAULT_LANGUAGE = 'zh-CN';" in source
    assert "const LANGUAGES = ['zh-CN', 'en'];" in source
    markup = LOGIN_HTML_PATH.read_text(encoding="utf-8")
    assert '<html lang="zh-CN">' in markup


def test_english_table_covers_every_interface_key() -> None:
    chinese = _table_keys("zh-CN")
    english = _table_keys("en")
    assert chinese, "the Chinese table must not be empty"
    # Server messages and viewer vocabulary are already English on the wire, so
    # only the Chinese table maps them; every interface key must exist in both.
    interface_keys = {
        key for key in chinese if not key.startswith(WIRE_KEYED_PREFIXES)
    }
    assert interface_keys == english
    assert english <= chinese


def _rendered(node: ast.AST) -> str | None:
    """Return a message wording with `{}` wherever a value was interpolated.

    That is the shape the catalog keys, so a diagnostic that spells a path or a
    count into itself can still be matched and translated whole.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "{}"
            for part in node.values
        ]
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _rendered(node.left), _rendered(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.Call):
        # A joined list — `", ".join(missing)` — reads as one value.
        return "{}"
    return None


@functools.lru_cache(maxsize=None)
def _server_messages() -> frozenset[str]:
    """Return every rejection wording a browser request can be answered with.

    Not only the ones a handler words itself: a `ContractError` raised anywhere
    the handler reaches leaves through the same field, and a `PreflightError`
    carries a whole list of them. Read from the syntax tree rather than by
    regex, because several of these messages are written as adjacent string
    literals across two lines.
    """
    found: set[str] = set()
    for path in SERVER_MODULE_PATHS:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            wordings: list[str | None] = []
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name in {"ContractError", "PreflightError"}:
                    first = node.args[0] if node.args else None
                    if isinstance(first, (ast.List, ast.Tuple)):
                        wordings += [_rendered(item) for item in first.elts]
                    elif first is not None:
                        wordings.append(_rendered(first))
                elif name == "append" and getattr(
                    getattr(node.func, "value", None), "id", ""
                ) == "errors":
                    # `errors.append(...)` feeds `PreflightError(errors)` verbatim.
                    wordings += [_rendered(argument) for argument in node.args[:1]]
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "error":
                        wordings.append(_rendered(value))
            found |= {w.strip() for w in wordings if w and w.strip() and w.strip() != "{}"}
    return frozenset(found)


# `new Error('...')` or `new Error(`...`)`, the wording possibly on the next
# line. A `${...}` reads as a value, the same `{}` the catalog keys.
THROWN_PATTERN = re.compile(
    r"new Error\(\s*(?:'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`)",
)
INTERPOLATION_PATTERN = re.compile(r"\$\{[^{}]*\}")
# The wording stood in when a rejection carried none of its own:
# `String(error || 'Unknown viewer function error.')`.
FALLBACK_PATTERN = re.compile(r"String\([^()]*\|\|\s*'((?:[^'\\]|\\.)*)'\s*\)")


@functools.lru_cache(maxsize=None)
def _client_messages() -> frozenset[str]:
    """Return every rejection the browser workspace words for itself.

    Only the vendored runtime: the rest of the frontend throws copy it has
    already taken from the language layer, while these two scripts carry the
    English wording of the release they were copied from and are translated
    where they are drawn.
    """
    found: set[str] = set()
    for name, _ in LOCALIZED_MCP_SCRIPTS:
        script = (MCP_ROOT / name).read_text(encoding="utf-8")
        wordings = [quoted or templated for quoted, templated in THROWN_PATTERN.findall(script)]
        wordings += FALLBACK_PATTERN.findall(script)
        for raw in wordings:
            wording = INTERPOLATION_PATTERN.sub("{}", raw).strip()
            if wording and wording != "{}":
                found.add(wording)
    return frozenset(found)


def test_every_thrown_message_has_chinese_wording() -> None:
    """The workspace refuses an action in its own words as often as the server.

    A step that cannot be saved, an argument that will not parse, storage that
    will not open: all of it reaches the expert through the status line, so all
    of it needs wording.
    """
    chinese = _table_keys("zh-CN")
    messages = _client_messages()
    assert len(messages) > 40, "the thrown-message inventory did not load"
    missing = sorted(m for m in messages if f"error.{m}" not in chinese)
    assert not missing, f"these thrown messages reach the expert in English: {missing}"


def test_every_server_message_has_chinese_wording() -> None:
    """A rejection is the moment an operator most needs to read the message.

    Every one of them, not just the ones the gateway words itself: whichever
    module refuses the request, the refusal is what the operator sees. A new
    message added anywhere in the chain fails here until it has wording.
    """
    chinese = _table_keys("zh-CN")
    messages = _server_messages()
    assert len(messages) > 300, "the message inventory did not load"
    missing = sorted(m for m in messages if f"error.{m}" not in chinese)
    assert not missing, f"these server messages reach the user in English: {missing}"


@functools.lru_cache(maxsize=None)
def _viewer_vocabulary() -> dict[str, frozenset[str]]:
    """Return the viewer-declared vocabulary, grouped the way the catalog keys it.

    Only what the panel draws: a function name and its sentence, an argument name
    and its sentence, and the choices in an argument dropdown. The selector
    sub-fields are left out because the panel renders that argument as raw JSON
    and never shows their descriptions.
    """
    snapshot = json.loads(VIEWER_VOCABULARY_PATH.read_text(encoding="utf-8"))
    groups: dict[str, set[str]] = {"tool": set(), "arg": set(), "enum": set(), "help": set()}
    for tools in snapshot["viewers"].values():
        for tool in tools:
            groups["tool"].add(tool["name"])
            if tool.get("description"):
                groups["help"].add(tool["description"])
            for name, schema in (tool["inputSchema"].get("properties") or {}).items():
                groups["arg"].add(name)
                if schema.get("description"):
                    groups["help"].add(schema["description"])
                for value in schema.get("enum") or ():
                    groups["enum"].add(str(value))
    return {kind: frozenset(values) for kind, values in groups.items()}


@functools.lru_cache(maxsize=None)
def _frame_vocabulary() -> frozenset[str]:
    """Return the wording the viewer frame draws itself, panel and page chrome."""
    snapshot = json.loads(VIEWER_FRAME_VOCABULARY_PATH.read_text(encoding="utf-8"))
    return frozenset(snapshot["panel"]["vocabulary"]) | frozenset(snapshot["page"]["vocabulary"])


@functools.lru_cache(maxsize=None)
def _frame_structure_data() -> frozenset[str]:
    """Return the frame text a loaded structure supplied rather than the panel."""
    snapshot = json.loads(VIEWER_FRAME_VOCABULARY_PATH.read_text(encoding="utf-8"))
    return frozenset(snapshot["panel"]["structure_data"])


def test_every_viewer_word_has_chinese_wording() -> None:
    """The function panel is where the expert works, and the viewer names it all.

    Function names, argument names, the choices in a dropdown and the sentence
    explaining each: all of it crosses the iframe boundary in English, so all of
    it needs wording on the way to the screen.
    """
    chinese = _table_keys("zh-CN")
    missing = sorted(
        f"viewer.{kind}.{word}"
        for kind, words in _viewer_vocabulary().items()
        for word in words
        if f"viewer.{kind}.{word}" not in chinese
    )
    assert not missing, f"these viewer words reach the expert in English: {missing}"


def test_no_viewer_key_translates_a_word_no_viewer_declares() -> None:
    """`translateViewer` looks the text up verbatim, as the error catalog does.

    A key that drifts from what the viewer declares falls back to English while
    still reading, in the table, as though it were translated.
    """
    vocabulary = {**_viewer_vocabulary(), "frame": _frame_vocabulary()}
    dead = []
    for key in _table_keys("zh-CN"):
        if not key.startswith("viewer."):
            continue
        kind, _, word = key[len("viewer."):].partition(".")
        if word not in vocabulary.get(kind, frozenset()):
            dead.append(key)
    assert not dead, f"these viewer keys match nothing a viewer declares: {sorted(dead)}"


def test_every_word_the_viewer_frame_draws_has_chinese_wording() -> None:
    """The viewer draws its own panel, and it draws it in English.

    Mol* carries no language layer and its bundle is a vendored release, so the
    workspace glosses that wording from the parent. Every label the expert can
    read in the frame therefore needs an entry, or the panel stays English while
    everything around it is Chinese.
    """
    chinese = _table_keys("zh-CN")
    missing = sorted(
        word for word in _frame_vocabulary() if f"viewer.frame.{word}" not in chinese
    )
    assert not missing, f"these frame labels reach the expert in English: {missing}"


def test_no_frame_key_translates_text_a_structure_supplies() -> None:
    """A chain name or a residue count is data; wording it over would be a lie.

    The gloss matches on text alone, with no idea which element drew it, so a key
    naming something a structure supplied would rewrite that structure's own
    numbers wherever they happen to read the same.
    """
    chinese = _table_keys("zh-CN")
    keyed = {key[len("viewer.frame."):] for key in chinese if key.startswith("viewer.frame.")}
    assert keyed, "the frame vocabulary must be keyed"
    overreach = sorted(keyed & _frame_structure_data())
    assert not overreach, f"these keys word what a structure supplied: {overreach}"


def test_no_error_key_translates_a_message_the_gateway_never_sends() -> None:
    """A key that misspells the wire wording is worse than no key at all.

    `translateError` looks the message up verbatim, so a table entry that drifts
    from the handler silently falls back to English while still reading, in the
    table, as though it were translated. The same holds for the wording the
    workspace throws, which `translateThrown` looks up the same way.
    """
    messages = _server_messages() | _client_messages()
    dead = sorted(
        key for key in _table_keys("zh-CN")
        if key.startswith("error.") and not _keys_something(key[len("error."):], messages)
    )
    assert not dead, f"these error keys match nothing the gateway sends: {dead}"


def _keys_something(wording: str, messages: frozenset[str]) -> bool:
    """Say whether a catalog entry can ever be reached.

    An entry words a whole message, placeholders included: the key and the
    message have to agree on where the interpolated values sit. `_` opens the
    mechanism entries, which key no message of their own. `messages` is both
    inventories, because either side can be the one that refuses.
    """
    if wording.startswith("_"):
        return True
    return wording in messages


def test_login_markup_defaults_to_chinese_and_only_uses_known_keys() -> None:
    entries = _markup_entries(LOGIN_HTML_PATH)
    assert entries, "the login page must declare data-i18n keys"
    chinese = _table_keys("zh-CN")
    for key, default_text in entries:
        assert key in chinese, f"login page references unknown key {key}"
        assert CJK_PATTERN.search(default_text), (
            f"inline default for {key} must be Chinese, got {default_text!r}"
        )


def test_login_script_routes_operator_text_through_the_language_layer() -> None:
    script = LOGIN_JS_PATH.read_text(encoding="utf-8")
    assert "window.SVI18n" in script
    for key in (
        "login.status.signingIn",
        "login.status.activating",
        "login.error.signInFailed",
        "login.error.activationFailed",
        "login.error.sessionLoad",
    ):
        assert f"'{key}'" in script, f"login script must resolve {key}"
    assert "serverMessage(value.error)" in script
    markup = LOGIN_HTML_PATH.read_text(encoding="utf-8")
    assert '<script src="../static/i18n.js"></script>' in markup
    assert markup.index("i18n.js") < markup.index("login.js")


def test_expert_markup_defaults_to_chinese_and_only_uses_known_keys() -> None:
    entries = _markup_entries(EXPERT_HTML_PATH)
    assert entries, "the expert app must declare data-i18n keys"
    chinese = _table_keys("zh-CN")
    for key, default_text in entries:
        assert key in chinese, f"expert app references unknown key {key}"
        assert CJK_PATTERN.search(default_text), (
            f"inline default for {key} must be Chinese, got {default_text!r}"
        )
    markup = EXPERT_HTML_PATH.read_text(encoding="utf-8")
    assert '<html lang="zh-CN">' in markup
    assert '<script src="/i18n.js"></script>' in markup
    assert markup.index("i18n.js") < markup.index("expert-app.js")


def test_expert_script_routes_operator_text_through_the_language_layer() -> None:
    script = EXPERT_JS_PATH.read_text(encoding="utf-8")
    assert "window.SVI18n" in script
    chinese = _table_keys("zh-CN")
    referenced = set(re.findall(r"t\('(expert\.[\w.]+)'", script))
    assert referenced, "the expert app must resolve text through the layer"
    unknown = referenced - chinese
    assert not unknown, f"expert app resolves unknown keys: {sorted(unknown)}"
    # Server wording reaches the expert through the error table, not raw.
    assert "i18n.translateError" in script
    # Redrawing derived text on a language switch is the app's job.
    assert "i18n?.onChange(" in script
    for element in DATA_BEARING_ELEMENTS:
        assert f"{element}.removeAttribute('data-i18n')" in script, (
            f"{element} must drop its placeholder key once it holds data"
        )


def test_manager_markup_defaults_to_chinese_and_only_uses_known_keys() -> None:
    entries = _markup_entries(MANAGER_HTML_PATH)
    assert entries, "the manager app must declare data-i18n keys"
    chinese = _table_keys("zh-CN")
    for key, default_text in entries:
        assert key in chinese, f"manager app references unknown key {key}"
        assert CJK_PATTERN.search(default_text), (
            f"inline default for {key} must be Chinese, got {default_text!r}"
        )
    markup = MANAGER_HTML_PATH.read_text(encoding="utf-8")
    assert '<html lang="zh-CN">' in markup
    assert '<script src="/i18n.js"></script>' in markup
    assert markup.index("i18n.js") < markup.index("/app.js")
    # Attribute keys carry the same obligation as textContent keys.
    attribute_keys = set(
        re.findall(r'data-i18n-(?:placeholder|title|aria-label|value)="([\w.\-]+)"', markup)
    )
    unknown = attribute_keys - chinese
    assert not unknown, f"manager app references unknown keys: {sorted(unknown)}"


def test_manager_script_routes_operator_text_through_the_language_layer() -> None:
    script = MANAGER_JS_PATH.read_text(encoding="utf-8")
    assert "window.SVI18n" in script
    chinese = _table_keys("zh-CN")
    referenced = set(re.findall(r"['\"](manager\.[\w.]+)['\"]", script))
    assert referenced, "the manager app must resolve text through the layer"
    # The view heading pair builds its key from the active tab.
    referenced |= {
        f"manager.view.{tab}.{part}"
        for tab in ("setup", "distribution", "results")
        for part in ("title", "description")
    }
    unknown = referenced - chinese
    assert not unknown, f"manager app resolves unknown keys: {sorted(unknown)}"
    # Server wording reaches the operator through the error table, not raw.
    assert "i18n.translateError" in script
    # Redrawing tables and buttons on a language switch is the app's job.
    assert "i18n?.onChange(" in script
    for element_id, handle in MANAGER_DATA_BEARING_ELEMENTS:
        assert f"'{element_id}'" in script, element_id
        assert f"{handle}.removeAttribute('data-i18n')" in script, (
            f"{element_id} must drop its placeholder key once it holds data"
        )


@pytest.mark.parametrize(
    ("name", "prefix"),
    LOCALIZED_MCP_SCRIPTS,
    ids=[name for name, _ in LOCALIZED_MCP_SCRIPTS],
)
def test_vendored_mcp_runtime_routes_its_copy_through_the_language_layer(
    name: str, prefix: str
) -> None:
    """The vendored browser-MCP runtime is localized in place, and must stay so.

    Upstream builds this runtime with no language layer, so re-vendoring a newer
    copy over this directory would silently return the panel to English. The
    fallback argument keeps a package that ships no ``i18n.js`` on the original
    wording rather than on a bare key.
    """
    script = (MCP_ROOT / name).read_text(encoding="utf-8")
    assert "global.SVI18n" in script, f"{name} must reach the language layer"
    assert "return layer ? layer.t(key, fallback, params) : fallback;" in script, (
        f"{name} must fall back to its original English wording"
    )
    # Keys also reach the layer through status helpers and plural ternaries, so
    # match every literal in the namespace rather than only direct `t(` calls.
    referenced = set(re.findall(rf"'({prefix}\.[\w.]+)'", script))
    assert referenced, f"{name} must resolve text through the layer"
    unknown = referenced - _table_keys("zh-CN")
    assert not unknown, f"{name} resolves unknown keys: {sorted(unknown)}"
    # A language switch has to redraw what is already on screen.
    assert "global.SVI18n?.onChange(" in script, f"{name} must redraw on a switch"


def test_interpolated_keys_declare_the_same_placeholders() -> None:
    source = I18N_PATH.read_text(encoding="utf-8")
    chinese_block = _block(source, "'zh-CN': {")
    english_block = _block(source, "\n    en: {")
    pattern = re.compile(r"'([\w.\- ]+)': '([^']*)'")
    chinese = dict(pattern.findall(chinese_block))
    english = dict(pattern.findall(english_block))
    placeholders = re.compile(r"\{(\w+)\}")
    for key, value in chinese.items():
        if key not in english:
            continue
        assert set(placeholders.findall(value)) == set(placeholders.findall(english[key])), (
            f"{key} uses different placeholders in each language"
        )


def test_server_messages_translate_by_wording_and_never_leak_across_languages() -> None:
    """The catalog keys server wording, which puts three rules on the lookup.

    An exact match wins. A diagnostic that interpolated a value is matched as a
    pattern, so the reader gets a whole Chinese sentence with the value in it
    rather than a Chinese head and an English tail. And English must keep the
    wire wording: the general lookup falls back to the Chinese table when a key
    is missing from the English one, which for server messages would hand an
    English reader Chinese.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        page = browser.new_page()
        page.add_script_tag(path=str(I18N_PATH.resolve()))

        def translated(message: str) -> str:
            return page.evaluate("message => window.SVI18n.translateError(message)", message)

        assert page.evaluate("window.SVI18n.language()") == "zh-CN"
        assert CJK_PATTERN.search(translated("invalid username or password"))
        # A parameterized diagnostic reads as one Chinese sentence, and the
        # value it spelled into itself survives in place.
        parameterized = translated("unsupported audit filter(s): actor, shape")
        assert CJK_PATTERN.search(parameterized)
        assert "actor, shape" in parameterized
        assert "unsupported audit filter" not in parameterized
        # A diagnostic that quotes another one translates all the way down.
        nested = translated("line 4: name is required")
        assert CJK_PATTERN.search(nested)
        assert "4" in nested and "name is required" not in nested
        # An unknown diagnostic is framed, not passed through as if it were copy.
        unknown = translated("some diagnostic nobody has keyed yet")
        assert CJK_PATTERN.search(unknown)
        assert "some diagnostic nobody has keyed yet" in unknown

        page.evaluate("window.SVI18n.setLanguage('en')")
        for message in (
            "invalid username or password",
            "expert role required",
            "unsupported audit filter(s): actor, shape",
            "line 4: name is required",
        ):
            assert translated(message) == message, "English must keep the wire wording"
        assert translated("some diagnostic nobody has keyed yet") == (
            "some diagnostic nobody has keyed yet"
        )
        browser.close()


def test_language_layer_never_assigns_markup() -> None:
    source = I18N_PATH.read_text(encoding="utf-8")
    assert ".innerHTML" not in source
    assert ".outerHTML" not in source
    assert "textContent" in source


def test_stored_language_choice_is_validated() -> None:
    source = I18N_PATH.read_text(encoding="utf-8")
    # An attacker-controlled localStorage value must not select a table.
    assert "LANGUAGES.includes(stored)" in source
    assert "LANGUAGES.includes(language)" in source


@contextlib.contextmanager
def _app_server(root: Path, aliases: dict[str, Path]):
    """Serve one app directory, standing in for the assets the gateway injects."""

    class AppHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:  # pragma: no cover - quiet output
            pass

        def translate_path(self, path: str) -> str:
            alias = aliases.get(path.split("?")[0])
            return str(alias.resolve()) if alias else super().translate_path(path)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = socketserver.ThreadingTCPServer(
        ("127.0.0.1", port),
        functools.partial(AppHandler, directory=str(root.resolve())),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("app_html", "shell"),
    [(EXPERT_HTML_PATH, "expert"), (MANAGER_HTML_PATH, "manager")],
    ids=["expert", "manager"],
)
def test_language_toggle_never_covers_the_header_controls(app_html: Path, shell: str) -> None:
    """The switch joins the header row rather than sitting on top of it."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    controls = HEADER_CONTROL_IDS[shell]
    with _app_server(app_html.parent, {"/i18n.js": I18N_PATH}) as origin:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{origin}/{app_html.name}", wait_until="load")
            page.wait_for_selector(".sv-lang-toggle")
            # Controls a shell reveals only once signed in still have to survive.
            page.evaluate(
                "ids => ids.forEach(id => { const node = document.getElementById(id);"
                " if (node) node.hidden = false; })",
                list(controls),
            )
            assert page.evaluate(
                "() => Boolean(document.querySelector('.topbar .sv-lang-toggle'))"
            ), "the switch belongs inside the header it shares a corner with"
            for control in controls:
                topmost = page.evaluate(
                    "id => {"
                    " const box = document.getElementById(id).getBoundingClientRect();"
                    " const node = document.elementFromPoint("
                    "   box.x + box.width / 2, box.y + box.height / 2);"
                    " const button = node?.closest('button');"
                    " return button ? (button.id || button.className) : (node?.tagName ?? null);"
                    "}",
                    control,
                )
                assert topmost == control, f"{control} is covered by {topmost}"
            browser.close()


def test_offline_expert_package_ships_the_language_layer(imported_workflow: dict) -> None:
    """An offline package has to carry the layer it loads.

    The workspace markup loads ``/i18n.js`` unconditionally; a package without
    that file answers 404, drops every runtime string back to its English
    fallback, and offers no switch at all.
    """
    from tools.offline_task_manager.packager import build_expert_package

    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "i18n-package"
    build_expert_package(store, batch_id="batch-r1", expert_id="expert-a", output=output)

    assert (output / "i18n.js").read_text(encoding="utf-8") == I18N_PATH.read_text(
        encoding="utf-8"
    )
    assert '<script src="/i18n.js"></script>' in (output / "index.html").read_text(
        encoding="utf-8"
    )


def test_offline_expert_workspace_opens_in_chinese_and_can_switch(
    imported_workflow: dict,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.offline_task_manager.test_expert_step_recorder import (
        open_workspace,
        served_package,
    )

    with served_package(imported_workflow, "i18n-served-package") as url:
        with sync_playwright() as playwright:
            browser, page = open_workspace(playwright, url)
            assert page.evaluate("window.SVI18n.language()") == "zh-CN"
            assert CJK_PATTERN.search(page.locator("#step-add").inner_text())
            page.locator(".sv-lang-toggle").click()
            assert page.evaluate("window.SVI18n.language()") == "en"
            assert page.locator("#step-add").inner_text().strip() == "Record step"
            browser.close()


def test_local_manager_console_serves_the_language_layer(tmp_path: Path) -> None:
    """The loopback console loads the layer from the same path as the gateway.

    Its markup loads ``/i18n.js`` unconditionally, but the file lives in the
    online app directory: without an alias the local console answers 404,
    drops every runtime string back to English, and offers no switch.
    """
    from tools.offline_task_manager.server import Handler as ManagerHandler
    from tools.offline_task_manager.server import ManagerServer
    from tools.offline_task_manager.store import ManagerStore

    server = ManagerServer(("127.0.0.1", 0), ManagerHandler)
    server.store = ManagerStore(tmp_path / "manager.db")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/i18n.js", timeout=5) as response:
            assert response.status == 200
            assert "javascript" in response.headers["Content-Type"]
            body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert body == I18N_PATH.read_text(encoding="utf-8")


# A viewer stub that declares one function of each shape the panel renders: a
# required number, an argument with a fixed set of choices, and an optional one
# that needs its include box ticked.
STUB_VIEWER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>stub viewer</title></head><body>
<script>
window.ScienceVisionViewerMCP = {
  version: 1,
  whenReady: () => Promise.resolve(true),
  listTools: () => [
    {
      name: 'viewer_slice',
      description:
        'Show an anatomical slice and move the crosshair by voxel index or normalized fraction.',
      inputSchema: {
        type: 'object',
        properties: {
          axis: {type: 'string', enum: ['axial', 'coronal', 'sagittal']},
          index: {type: 'integer', minimum: 0, description: 'Inclusive zero-based voxel index.'},
        },
        required: ['axis'],
      },
    },
    {name: 'viewer_get_state', description: 'Return camera state, stable structure IDs, visibility, and measurement count.', inputSchema: {type: 'object', properties: {}}},
  ],
  callTool: async () => {
    if (window.__svHold) await window.__svHold;
    return {ok: true, state_after: {}, result_state: {}};
  },
  getState: () => ({}),
};
// A gate so a test can observe the panel mid-run: without it the call resolves
// in the same task and only the finished state is ever visible.
window.__svHold = null;
window.__svHoldCalls = () => {
  window.__svHold = new Promise(resolve => {
    window.__svRelease = () => { window.__svHold = null; resolve(); };
  });
};
</script>
</body></html>
"""

STUB_HOST_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>stub host</title></head><body>
<div class="sv-task-frame-wrap">
  <iframe id="sv-task-frame" src="about:blank"></iframe>
  <details class="sv-mcp-controls" id="sv-mcp-controls" open></details>
</div>
<script src="/i18n.js"></script>
<script src="/task-mcp-controls.js"></script>
</body></html>
"""


@contextlib.contextmanager
def _served_panel(tmp_path: Path):
    """Serve a page that mounts the real function panel against a stub viewer.

    The panel reads its vocabulary from whatever the viewer declares, so the
    wording can only be checked by letting it render.
    """
    root = tmp_path / "panel"
    root.mkdir()
    (root / "index.html").write_text(STUB_HOST_HTML, encoding="utf-8")
    (root / "viewer.html").write_text(STUB_VIEWER_HTML, encoding="utf-8")
    (root / "i18n.js").write_text(I18N_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "task-mcp-controls.js").write_text(
        (MCP_ROOT / "task-mcp-controls.js").read_text(encoding="utf-8"), encoding="utf-8",
    )

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, *args):  # noqa: D102 - keep the test output readable
            return

    server = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# A stand-in for the viewer frame: the panel labels Mol* draws as text and as a
# tooltip, one string a loaded structure supplied, and a label inside a tag whose
# text is code rather than copy. No molstar bundle is vendored here, and the real
# one is four megabytes of minified release, so the mechanism is checked against
# the shapes it has to handle.
STUB_FRAME_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>stub frame</title></head><body>
<div class="msp-plugin">
  <div id="section">Structure Tools</div>
  <button id="selection" title="Toggle Selection Mode" aria-label="Toggle Selection Mode">S</button>
  <div id="supplied">1: HEMOGLOBIN SUBUNIT ALPHA</div>
  <div id="host"></div>
</div>
<script id="code" type="text/plain">Measurements</script>
</body></html>
"""

STUB_FRAME_HOST_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>stub host</title></head><body>
<iframe id="sv-task-frame" src="/frame.html"></iframe>
<script src="/i18n.js"></script>
</body></html>
"""


@contextlib.contextmanager
def _served_frame(tmp_path: Path):
    """Serve a host page with the stub viewer frame inside it, same origin."""
    root = tmp_path / "frame"
    root.mkdir()
    (root / "index.html").write_text(STUB_FRAME_HOST_HTML, encoding="utf-8")
    (root / "frame.html").write_text(STUB_FRAME_HTML, encoding="utf-8")
    (root / "i18n.js").write_text(I18N_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, *args):  # noqa: D102 - keep the test output readable
            return

    server = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_viewer_frame_wording_is_glossed_and_keeps_up_with_the_viewer(tmp_path: Path) -> None:
    """The viewer's own panel is the one surface the layer cannot reach by key.

    Mol* ships no language layer and its bundle may not be edited, so the gloss is
    written from the parent over the frame's own text. It has to survive the
    viewer re-rendering, leave text the structure supplied alone, keep out of
    subtrees whose text is code, and give the viewer's wording back in English.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    with _served_frame(tmp_path) as url:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            page = browser.new_page()
            page.goto(url)
            page.wait_for_function(
                "() => document.getElementById('sv-task-frame').contentDocument"
                ".getElementById('section')"
            )
            page.evaluate(
                "() => { window.stop = window.SVI18n.observeFrame("
                "document.getElementById('sv-task-frame')); }"
            )
            frame = page.frame_locator("#sv-task-frame")

            assert frame.locator("#section").inner_text() == "结构工具"
            assert frame.locator("#selection").get_attribute("title") == "切换选择模式"
            assert frame.locator("#selection").get_attribute("aria-label") == "切换选择模式"
            assert frame.locator("#supplied").inner_text() == "1: HEMOGLOBIN SUBUNIT ALPHA", (
                "text a structure supplied is not the panel's wording"
            )
            code = page.evaluate(
                "() => document.getElementById('sv-task-frame').contentDocument"
                ".getElementById('code').textContent"
            )
            assert code == "Measurements", "a tag whose text is code is left alone"

            # What a re-render looks like: a new subtree, and a label rewritten in
            # place. Both have to be glossed without a second install.
            page.evaluate(
                """() => {
                  const doc = document.getElementById('sv-task-frame').contentDocument;
                  const added = doc.createElement('div');
                  added.id = 'added';
                  added.textContent = 'Measurements';
                  doc.getElementById('host').appendChild(added);
                  doc.getElementById('section').textContent = 'Components';
                }"""
            )
            frame.locator("#added").wait_for()
            page.wait_for_function(
                "() => document.getElementById('sv-task-frame').contentDocument"
                ".getElementById('added').textContent === '测量'"
            )
            page.wait_for_function(
                "() => document.getElementById('sv-task-frame').contentDocument"
                ".getElementById('section').textContent === '组件'"
            )

            # English is the viewer's own wording, not a second translation of it.
            page.evaluate("window.SVI18n.setLanguage('en')")
            page.wait_for_function(
                "() => document.getElementById('sv-task-frame').contentDocument"
                ".getElementById('section').textContent === 'Components'"
            )
            assert frame.locator("#selection").get_attribute("title") == "Toggle Selection Mode"
            assert frame.locator("#added").inner_text() == "Measurements"

            page.evaluate("window.SVI18n.setLanguage('zh-CN')")
            page.wait_for_function(
                "() => document.getElementById('sv-task-frame').contentDocument"
                ".getElementById('section').textContent === '组件'"
            )
            assert frame.locator("#selection").get_attribute("title") == "切换选择模式"

            # Stopping releases the frame: a later re-render is left in English.
            page.evaluate("window.stop()")
            page.evaluate(
                "() => { document.getElementById('sv-task-frame').contentDocument"
                ".getElementById('host').textContent = 'Quick Styles'; }"
            )
            page.wait_for_timeout(200)
            assert frame.locator("#host").inner_text() == "Quick Styles"
            browser.close()


def test_the_expert_workspace_glosses_every_viewer_it_loads() -> None:
    """The hook belongs to the workspace, not to one viewer or one task.

    Every viewer the frame loads draws its own wording, so the gloss is installed
    on the frame's load event and the previous watcher is dropped with the nodes
    it was watching.
    """
    script = EXPERT_JS_PATH.read_text(encoding="utf-8")
    assert "els.svTaskFrame.addEventListener('load'" in script
    assert "i18n.observeFrame(els.svTaskFrame)" in script
    assert "if (stopViewerGloss) stopViewerGloss();" in script


def test_function_panel_reads_the_viewer_vocabulary_in_chinese(tmp_path: Path) -> None:
    """The panel is where the expert works, and the viewer names it all in English.

    Function names, the sentence for each, the choices in a dropdown: the panel
    receives all of it over the iframe boundary. The wire name stays visible,
    because a task names its Ground Truth actions by it, and the value a choice
    sends stays English, because the viewer is the one receiving it. Everything
    read is Chinese, and English restores the viewer's own wording.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    with _served_panel(tmp_path) as url:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            page = browser.new_page()
            page.goto(url)
            page.evaluate(
                """() => {
                  const controller = window.ScienceVisionTaskMCPControls.mountDefault();
                  controller.navigate(
                    {task_id: 't1', tool_policy: {allowed_tools: ['viewer_slice']},
                     asset: {viewer: 'niivue'}},
                    '/viewer.html',
                  );
                }"""
            )
            page.wait_for_function(
                "() => document.querySelectorAll('#sv-mcp-controls .sv-mcp-params .sv-mcp-field')"
                ".length > 0"
            )

            option = page.locator("#sv-mcp-controls select").first.locator(
                "option[value='viewer_slice']"
            )
            assert "viewer_slice" in option.inner_text(), "the wire name must stay readable"
            assert CJK_PATTERN.search(option.inner_text()), "the name needs a gloss"
            description = page.locator("#sv-mcp-controls .sv-mcp-tool-description").inner_text()
            assert CJK_PATTERN.search(description)
            assert "anatomical slice" not in description

            labels = page.locator("#sv-mcp-controls .sv-mcp-params label").all_inner_texts()
            assert any(CJK_PATTERN.search(text) for text in labels)
            axis = page.locator("#sv-mcp-controls .sv-mcp-params select").first
            assert CJK_PATTERN.search(axis.locator("option").first.inner_text())
            assert axis.locator("option").first.get_attribute("value") == "axial", (
                "the viewer receives its own vocabulary, not the translation"
            )

            page.evaluate("window.SVI18n.setLanguage('en')")
            page.wait_for_function(
                "() => document.querySelector(\"#sv-mcp-controls option[value='viewer_slice']\")"
                ".textContent.includes('viewer_slice')"
            )
            english = page.locator(
                "#sv-mcp-controls .sv-mcp-tool-description"
            ).inner_text()
            assert "anatomical slice" in english, "English keeps what the viewer declared"
            assert not CJK_PATTERN.search(english)
            browser.close()


def test_run_feedback_line_reports_each_stage_and_survives_a_language_switch(
    tmp_path: Path,
) -> None:
    """The line beside Run is the only place the panel says what a click did.

    Its wording is the reason the button was renamed: an expert pressed Preview,
    saw the viewport blank while the reset ran, and read that as a crash. So the
    line has to speak through the run — idle, running, then what to do next — and
    it has to be Chinese while the interface is. It also has to redraw: an outcome
    drawn once and left alone would still be in the old language after a switch,
    which is exactly what the status line above it already avoids.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    with _served_panel(tmp_path) as url:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            page = browser.new_page()
            page.goto(url)
            page.evaluate(
                """() => {
                  const controller = window.ScienceVisionTaskMCPControls.mountDefault();
                  controller.navigate(
                    {task_id: 't1', tool_policy: {allowed_tools: ['viewer_slice']},
                     asset: {viewer: 'niivue'}},
                    '/viewer.html',
                  );
                }"""
            )
            feedback = page.locator("#sv-mcp-controls .sv-mcp-feedback")
            run = page.locator("#sv-mcp-controls .sv-mcp-run")
            page.wait_for_function(
                "() => document.querySelector('#sv-mcp-controls .sv-mcp-feedback')"
                ".dataset.state === 'idle'"
                " && !document.querySelector('#sv-mcp-controls .sv-mcp-run').disabled"
            )
            assert CJK_PATTERN.search(feedback.inner_text()), "the idle prompt needs a gloss"
            assert CJK_PATTERN.search(run.inner_text()), "so does the button"

            page.evaluate(
                "() => document.getElementById('sv-task-frame').contentWindow.__svHoldCalls()"
            )
            run.click()
            page.wait_for_function(
                "() => document.querySelector('#sv-mcp-controls .sv-mcp-feedback')"
                ".dataset.state === 'running'"
            )
            running = feedback.inner_text()
            assert "viewer_slice" in running, "the expert has to see which function is running"
            assert CJK_PATTERN.search(running)
            assert run.get_attribute("data-busy") == "true"
            assert CJK_PATTERN.search(run.inner_text()), "the busy label is glossed too"

            page.evaluate(
                "() => document.getElementById('sv-task-frame').contentWindow.__svRelease()"
            )
            page.wait_for_function(
                "() => document.querySelector('#sv-mcp-controls .sv-mcp-feedback')"
                ".dataset.state === 'success'"
            )
            done = feedback.inner_text()
            assert "viewer_slice" in done
            assert CJK_PATTERN.search(done), "the outcome is read, so it is glossed"
            assert run.get_attribute("data-busy") == "false"

            page.evaluate("window.SVI18n.setLanguage('en')")
            page.wait_for_function(
                "() => !/[\\u4e00-\\u9fff]/.test("
                "document.querySelector('#sv-mcp-controls .sv-mcp-feedback').textContent)"
            )
            english = feedback.inner_text()
            assert feedback.get_attribute("data-state") == "success", (
                "a language switch reports the same outcome, it does not clear it"
            )
            assert "Save step" in english, (
                "the outcome is redrawn from the key, not left as the sentence it was"
            )
            assert "viewer_slice" in english
            browser.close()
