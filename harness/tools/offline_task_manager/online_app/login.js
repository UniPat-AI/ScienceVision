(function scienceVisionLogin() {
  'use strict';
  const loginForm = document.getElementById('login-form');
  const passwordForm = document.getElementById('password-form');
  const loginStatus = document.getElementById('login-status');
  const passwordStatus = document.getElementById('password-status');
  const base = location.pathname.replace(/\/login\/?$/, '');
  const i18n = window.SVI18n;
  const t = (key, fallback) => (i18n ? i18n.t(key, fallback) : fallback);
  const serverMessage = message => (i18n ? i18n.translateError(message) : message);
  let session = null;

  const destination = role => `${base}/${role === 'admin' ? 'admin' : 'expert'}/`;

  async function loadSession() {
    const response = await fetch(`${base}/api/session`, {cache: 'no-store'});
    if (!response.ok) return null;
    return response.json();
  }

  function showPasswordActivation(value) {
    session = value;
    loginForm.hidden = true;
    passwordForm.hidden = false;
    passwordForm.new_password.focus();
  }

  loginForm.addEventListener('submit', async event => {
    event.preventDefault();
    loginStatus.textContent = t('login.status.signingIn', 'Signing in...');
    const button = loginForm.querySelector('button');
    button.disabled = true;
    try {
      const response = await fetch(`${base}/api/login`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          username: loginForm.username.value.trim(),
          password: loginForm.password.value,
        }),
      });
      const value = await response.json();
      if (!response.ok) {
        throw new Error(serverMessage(value.error) || t('login.error.signInFailed', 'Sign in failed'));
      }
      loginForm.password.value = '';
      if (value.password_change_required) {
        const restrictedSession = await loadSession();
        if (!restrictedSession?.password_change_required) {
          throw new Error(
            t('login.error.sessionLoad', 'Password activation session could not be loaded'),
          );
        }
        showPasswordActivation(restrictedSession);
        return;
      }
      location.replace(destination(value.role));
    } catch (error) {
      loginStatus.textContent = error.message || String(error);
      button.disabled = false;
    }
  });

  passwordForm.addEventListener('submit', async event => {
    event.preventDefault();
    passwordStatus.textContent = t('login.status.activating', 'Activating account...');
    const button = passwordForm.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const response = await fetch(`${base}/api/password-change`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': session.csrf,
        },
        body: JSON.stringify({
          new_password: passwordForm.new_password.value,
          confirm_password: passwordForm.confirm_password.value,
        }),
      });
      const value = await response.json();
      if (!response.ok) {
        throw new Error(
          serverMessage(value.error) || t('login.error.activationFailed', 'Account activation failed'),
        );
      }
      passwordForm.reset();
      session = null;
      location.replace(destination(value.role));
    } catch (error) {
      passwordStatus.textContent = error.message || String(error);
      button.disabled = false;
    }
  });

  document.getElementById('activation-logout').addEventListener('click', async () => {
    if (!session) return;
    await fetch(`${base}/api/logout`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf},
      body: '{}',
    });
    passwordForm.reset();
    session = null;
    location.reload();
  });

  (async () => {
    const existing = await loadSession();
    if (!existing) return;
    if (existing.password_change_required) {
      showPasswordActivation(existing);
      return;
    }
    location.replace(destination(existing.role));
  })().catch(error => {
    loginStatus.textContent = error.message || String(error);
  });
}());
