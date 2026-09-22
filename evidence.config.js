// Recording configuration for the webapp-evidence skill.
//
// The webapp sits behind nginx Basic Auth (webapp/nginx.conf), which is not a
// form login, so there is no `login` hook here: the credentials ride on the
// base URL and Chrome then sends them for the rest of the origin. They come
// from the environment and are never written to this file — it is committed,
// and a password in it would be a password in the repository's history.
//
//   WEBAPP_BASIC_USER=admin WEBAPP_BASIC_PASS=... \
//     OUT_DIR=<dir> node <skill>/scripts/record.js <steps.js>
//
// Without those variables the base URL is the plain one, which nginx answers
// with 401 — which is the right failure: it says what is missing.

const HOST = process.env.WEBAPP_HOST || '127.0.0.1:2705';

function baseUrl() {
  const user = process.env.WEBAPP_BASIC_USER || '';
  const pass = process.env.WEBAPP_BASIC_PASS || '';
  if (!user || !pass) return `http://${HOST}`;
  return `http://${encodeURIComponent(user)}:${encodeURIComponent(pass)}@${HOST}`;
}

module.exports = {
  defaultApp: 'webapp',

  recording: {
    locale: 'vi-VN',
    // The flow waits on real network calls — a TikTok keyword scan opens a
    // headless browser of its own and takes tens of seconds — so the actions
    // themselves run at normal speed and the waiting is done by the steps.
    speed: 'normal',
    viewport: { width: 1440, height: 900 },
  },

  output: {
    overwrite: false,
    // Unused: Basic Auth needs no stored account. Kept pointing at an already
    // ignored path so nothing can land in the repository if a later flow does
    // grow a form login.
    accountStore: 'data/.evidence-accounts.json',
  },

  apps: {
    webapp: {
      get baseUrl() {
        return baseUrl();
      },

      // The stack is the deployment: there is no separate dev environment, so
      // "prepare" only checks it is up rather than starting anything. Starting
      // it here would recreate containers behind the user's back.
      async prepare() {
        // Node's fetch refuses a URL carrying credentials, so the check sends
        // the header itself. Playwright accepts the URL form, which is what
        // baseUrl above is for.
        const user = process.env.WEBAPP_BASIC_USER || '';
        const pass = process.env.WEBAPP_BASIC_PASS || '';
        const headers = user && pass
          ? { Authorization: `Basic ${Buffer.from(`${user}:${pass}`).toString('base64')}` }
          : {};
        const res = await fetch(`http://${HOST}/`, { headers, redirect: 'manual' }).catch((e) => {
          throw new Error(
            `Webapp không phản hồi ở http://${HOST} (${e.message}). Chạy \`make start\` trước.`,
          );
        });
        if (res.status === 401) {
          throw new Error(
            'Webapp trả 401: thiếu hoặc sai WEBAPP_BASIC_USER / WEBAPP_BASIC_PASS.',
          );
        }
        return [];
      },
    },
  },
};
