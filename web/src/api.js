// Calls the WatchFox API from the dashboard. The browser is signed in with a cookie, so no key is needed here.
async function call(method, path, body) {
  const res = await fetch(path, {
    method, credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  let data = {}
  try { data = await res.json() } catch {}
  if (!res.ok) {
    const err = new Error(data.detail || res.statusText)
    err.status = res.status
    throw err
  }
  return data
}

export const api = {
  me: () => call('GET', '/web/me'),
  signInOptions: () => call('GET', '/web/providers'),
  forgot: (email) => call('POST', '/web/forgot', { email }),
  reset: (b) => call('POST', '/web/reset', b),
  changePassword: (b) => call('POST', '/web/password', b),
  resendVerify: () => call('POST', '/web/verify/resend'),
  inviteInfo: (token) => call('GET', `/web/invite?token=${encodeURIComponent(token)}`),
  join: (b) => call('POST', '/web/join', b),
  plans: () => call('GET', '/v1/plans'),
  plan: () => call('GET', '/v1/plan'),
  billing: () => call('GET', '/v1/billing'),
  billingCheckout: (b) => call('POST', '/v1/billing/checkout', b),
  billingConfirm: (b) => call('POST', '/v1/billing/confirm', b),
  billingPayments: () => call('GET', '/v1/billing/payments'),
  billingCancel: () => call('POST', '/v1/billing/cancel'),
  upgrade: (b) => call('POST', '/v1/plan/upgrade', b),
  team: () => call('GET', '/v1/team'),
  invite: (email) => call('POST', '/v1/team/invites', { email }),
  inviteLink: (id) => call('GET', `/v1/team/invites/${id}/link`),
  cancelInvite: (id) => call('DELETE', `/v1/team/invites/${id}`),
  removeMember: (id) => call('DELETE', `/v1/team/members/${id}`),
  org: () => call('GET', '/v1/org'),
  renameOrg: (name) => call('PATCH', '/v1/org', { name }),
  signup: (b) => call('POST', '/web/signup', b),
  login: (b) => call('POST', '/web/login', b),
  logout: () => call('POST', '/web/logout'),
  health: () => call('GET', '/v1/health'),
  presets: () => call('GET', '/v1/presets'),
  types: () => call('GET', '/v1/types'),
  typeKinds: () => call('GET', '/v1/types/kinds'),
  tuneType: (b) => call('POST', '/v1/types/tune', b),
  saveType: (b) => call('POST', '/v1/types', b),
  deleteType: (id) => call('DELETE', `/v1/types/${id}`),
  suggestType: (sentence) => call('POST', '/v1/types/suggest', { sentence }),
  moreExamples: (b) => call('POST', '/v1/types/examples', b),
  updateType: (id, b) => call('PUT', `/v1/types/${id}`, b),
  feedback: (b) => call('POST', '/v1/feedback', b),
  corrections: () => call('GET', '/v1/corrections'),
  clearCorrections: (what) => call('DELETE', `/v1/corrections/${what}`),
  retune: (what) => call('POST', `/v1/corrections/${what}/retune`),
  providers: () => call('GET', '/v1/providers'),
  keys: () => call('GET', '/v1/keys'),
  newKey: (name) => call('POST', '/v1/keys', { name }),
  revokeKey: (id) => call('DELETE', `/v1/keys/${id}`),
  connections: () => call('GET', '/v1/connections'),
  addConnection: (b) => call('POST', '/v1/connections', b),
  removeConnection: (id) => call('DELETE', `/v1/connections/${id}`),
  connectLink: (b) => call('POST', '/v1/connect-links', b),
  sandboxEmail: (id, b) => call('POST', `/v1/connections/${id}/sandbox-emails`, b),
  watches: () => call('GET', '/v1/watches'),
  addWatch: (b) => call('POST', '/v1/watches', b),
  editWatch: (id, b) => call('PATCH', `/v1/watches/${id}`, b),
  getType: (id) => call('GET', `/v1/types/${id}`),
  classify: (b) => call('POST', '/v1/classify', b),
  removeWatch: (id) => call('DELETE', `/v1/watches/${id}`),
  testWatch: (id) => call('POST', `/v1/watches/${id}/test`),
  deliveries: (id) => call('GET', `/v1/watches/${id}/deliveries`),
  log: (params) => call('GET', `/v1/log?${new URLSearchParams(params)}`),
  matches: (limit = 50) => call('GET', `/v1/matches?limit=${limit}`),
  demo: (text, what) => call('POST', '/v1/demo', { text, what }),
}

export const BASE = window.location.origin
