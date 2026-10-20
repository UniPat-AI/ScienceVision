from __future__ import annotations

from pathlib import Path

from tools.offline_task_manager.results_export import csv_safe


def test_csv_export_escapes_formula_cells() -> None:
    assert csv_safe("=HYPERLINK(\"bad\")") == "'=HYPERLINK(\"bad\")"
    assert csv_safe("+cmd") == "'+cmd"
    assert csv_safe("  =1+1") == "'  =1+1"
    assert csv_safe("\n@SUM(1,1)") == "'\n@SUM(1,1)"
    assert csv_safe("normal") == "normal"


def test_frontends_do_not_render_imported_values_as_html() -> None:
    manager = open("tools/offline_task_manager/manager_app/app.js", encoding="utf-8").read()
    expert = open("tools/offline_task_manager/expert_app/expert-app.js", encoding="utf-8").read()
    assert ".innerHTML" not in manager
    assert ".innerHTML" not in expert
    assert "textContent" in manager
    assert "textContent" in expert


def test_password_reset_ui_keeps_secrets_out_of_generic_state_and_storage() -> None:
    manager_path = Path("tools/offline_task_manager/manager_app/app.js")
    manager = manager_path.read_text(encoding="utf-8")
    markup = Path("tools/offline_task_manager/manager_app/index.html").read_text(
        encoding="utf-8"
    )
    reset_region = manager[
        manager.index("function beginPasswordReset") : manager.index(
            "async function openReviewBundle"
        )
    ]
    assert "/api/admin/accounts/reset" in reset_region
    assert "run(" not in reset_region
    assert "log(" not in reset_region
    assert "localStorage" not in manager
    assert "sessionStorage" not in manager
    assert ".innerHTML" not in manager
    assert "navigator.clipboard.writeText(oneTimePassword)" in reset_region
    assert "passwordResetInFlight" in reset_region
    assert "oneTimePassword = ''" in reset_region
    assert "directory_sync_unconfirmed" in reset_region
    assert "password-reset-confirm-dialog" in markup
    assert "password-reset-secret-dialog" in markup
    assert "password-reset-durability-warning" in markup
    assert 'id="password-reset-value" type="password" readonly' in markup


def test_password_reset_ui_only_asks_the_server_for_a_temporary_password() -> None:
    """Experts are invited, so an administrator never picks their password.

    The server accepts exactly {"expert_id"} on /api/admin/accounts/reset, so a
    manual-password affordance would build a request it rejects with 400.
    """
    manager = Path("tools/offline_task_manager/manager_app/app.js").read_text(
        encoding="utf-8"
    )
    markup = Path("tools/offline_task_manager/manager_app/index.html").read_text(
        encoding="utf-8"
    )
    reset_region = manager[
        manager.index("function beginPasswordReset") : manager.index(
            "async function openReviewBundle"
        )
    ]
    assert "body: JSON.stringify({ expert_id: expertId })" in reset_region
    for token in (
        "password-reset-mode-random",
        "password-reset-mode-manual",
        "password-reset-manual-row",
        "password-reset-manual-value",
    ):
        assert token not in manager, token
        assert token not in markup, token


def test_caddy_keeps_authenticated_gateway_private_and_before_static_fallback() -> None:
    configs = (
        "deploy/Caddyfile",
        "deploy/Caddyfile.https.example",
        "deploy/Caddyfile.https.staged",
        "deploy/Caddyfile.live-precompressed",
    )
    for config in configs:
        value = Path(config).read_text(encoding="utf-8")
        auth_start = value.index("handle @sv_annotate {")
        static_start = value.index("\thandle {", auth_start)
        auth_branch = value[auth_start:static_start]
        static_branch = value[static_start:]
        assert "@sv_annotate path /sv-annotate /sv-annotate/*" in value
        assert 'header Cache-Control "private, no-store"' in auth_branch
        assert "reverse_proxy 127.0.0.1:4174" in auth_branch
        assert "try_files {path} /viewer{path}" in static_branch
        assert value.index("handle @sv_annotate {") < static_start
        if 'Cache-Control "public, max-age=3600"' in value:
            assert value.index('Cache-Control "public, max-age=3600"') > static_start


def test_expert_app_supports_v2_bootstrap_and_legacy_exports() -> None:
    expert = open("tools/offline_task_manager/expert_app/expert-app.js", encoding="utf-8").read()
    for token in (
        "/api/expert/bootstrap",
        "state.binding.tasks_url",
        "state.binding?.content_base",
        "state.binding?.viewer_base",
        "state.binding?.runtime_manifest?.runtime_id",
        "sciencevision.expert_bundle_submission.v2",
        "sciencevision.expert_bundle_result.v2",
        "state.manifest.legacy_package?.package_id",
        "/package_manifest.json",
    ):
        assert token in expert


def test_password_activation_ui_does_not_persist_or_log_credentials() -> None:
    login = open(
        "tools/offline_task_manager/online_app/login.js", encoding="utf-8"
    ).read()
    login_html = open(
        "tools/offline_task_manager/online_app/login.html", encoding="utf-8"
    ).read()
    manager = open(
        "tools/offline_task_manager/manager_app/app.js", encoding="utf-8"
    ).read()
    manager_html = open(
        "tools/offline_task_manager/manager_app/index.html", encoding="utf-8"
    ).read()

    for source in (login, manager):
        assert "localStorage" not in source
        assert "sessionStorage" not in source
    for token in (
        'id="password-form"',
        'autocomplete="new-password"',
        'minlength="16"',
        'maxlength="256"',
    ):
        assert token in login_html
    assert "/api/password-change" in login
    assert "passwordForm.reset()" in login
    assert "register" not in login.lower()
    assert "register" not in login_html.lower()

    credential_request = manager.split(
        "async function accountCredentialRequest", 1
    )[1].split("async function refresh", 1)[0]
    assert "operation-log" not in credential_request
    assert "log(" not in credential_request
    assert "value.password" not in credential_request
    assert 'id="credential-password"' in manager_html
    assert "accountCredentialRequest(action, body)" in manager
    assert "action.dataset.expertId = item.expert_id" in manager
    assert "const body = {expert_id: button.dataset.expertId}" in manager
    assert "showCredentialResult(value)" in manager
    assert "log(operatorResult(value))" not in manager


def test_expert_selection_opens_assignment_before_changing_ui_state() -> None:
    expert = Path("tools/offline_task_manager/expert_app/expert-app.js").read_text(
        encoding="utf-8"
    )
    region = expert[
        expert.index("async function openOnlineAssignment") : expert.index(
            "function saveCurrent"
        )
    ]
    assert "/api/expert/assignments/open" in region
    assert "'X-CSRF-Token': onlineSession.csrf" in region
    assert "await openOnlineAssignment(assignmentId)" in region
    assert region.index("await openOnlineAssignment(assignmentId)") < region.index(
        "state.selected = assignmentId"
    )
    assert "Task selection failed:" in region
    assert "return false" in region


def test_admin_assignment_history_exposes_status_and_review_actions() -> None:
    manager = Path("tools/offline_task_manager/manager_app/app.js").read_text(encoding="utf-8")
    markup = Path("tools/offline_task_manager/manager_app/index.html").read_text(
        encoding="utf-8"
    )
    assert "/api/assignments/review" in manager
    assert "onlineBase && item.status === 'pending_review'" in manager
    assert "reviewAssignment(item, 'approved')" in manager
    assert "reviewAssignment(item, 'returned')" in manager
    # The prompt wording now lives in the language table; the English text stays
    # as the fallback for a page whose language asset failed to load.
    assert "manager.assignment.returnReasonPrompt" in manager
    assert "Reason for returning this assignment:" in manager
    assert 'data-i18n="manager.assignmentsPanel.col.status"' in markup
    assert 'data-i18n="manager.assignmentsPanel.col.review"' in markup
