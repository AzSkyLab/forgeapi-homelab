// Identity-only bootstrap. Node built-ins + an already authenticated Azure CLI.
// No resource groups, Azure roles, credentials, tenant policies or admin consent.
import { execFileSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(root);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const scope = 'executions.access';
const statePath = 'config/entra.local.json';
const prefix = 'ForgeAPI Local';
const marker = 'ForgeAPI identity-only local Docker setup v1; synthetic workloads; no cloud compute.';

function requireThat(condition, message) {
  if (!condition) throw new Error(message);
}

function az(args) {
  try {
    const raw = execFileSync('az', [...args, '--only-show-errors', '--output', 'json'], {
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 60000,
    });
    return raw.trim() ? JSON.parse(raw) : null;
  } catch (error) {
    // Do not dump arguments, raw CLI responses, tokens or authentication caches.
    const status = String(error.stderr ?? '').match(/(?:AADSTS\d+|Authorization_RequestDenied|InvalidAuthenticationToken|Request_BadRequest)/)?.[0];
    throw new Error(`Azure CLI ${args[0]} failed${status ? ` (${status})` : ''}. Check the current login/Entra permissions. No automatic retry of writes; inspect recorded objects before retrying.`);
  }
}

function graph(method, path, body) {
  requireThat(path.startsWith('/applications') || path.startsWith('/servicePrincipals'), 'Unsupported Graph operation');
  const args = ['rest', '--method', method, '--url', `https://graph.microsoft.com/v1.0${path}`];
  if (body) args.push('--headers', 'Content-Type=application/json', '--body', JSON.stringify(body));
  return az(args);
}

function saveJSON(path, value, mode = 0o600) {
  const temporary = `${path}.tmp.local.json`;
  writeFileSync(temporary, JSON.stringify(value, null, 2) + '\n', { mode, flag: 'wx' });
  renameSync(temporary, path);
}

function noCredentials(app) {
  requireThat(!app.passwordCredentials?.length && !app.keyCredentials?.length, 'Registration has credentials; refusing to modify it');
  requireThat(app.signInAudience === 'AzureADMyOrg' && app.description === marker, 'Registration identity/type does not match this setup');
  requireThat(!app.web?.implicitGrantSettings?.enableAccessTokenIssuance && !app.web?.implicitGrantSettings?.enableIdTokenIssuance, 'Implicit token issuance is enabled; stop and review');
}

function main() {
  requireThat(process.argv.length === 4 && process.argv[2] === '--tenant' && uuid.test(process.argv[3]), 'Usage: node scripts/setup-entra.mjs --tenant <confirmed-lowercase-tenant-UUID>');
  const tenant = process.argv[3];
  const account = az(['account', 'show']);
  requireThat(account.tenantId === tenant && account.user?.type === 'user', 'CLI tenant/user does not match the confirmed human login; no changes made');
  requireThat(az(['cloud', 'show']).name === 'AzureCloud', 'Only commercial Azure is supported');
  const user = az(['ad', 'signed-in-user', 'show']);
  requireThat(uuid.test(user.id), 'Cannot resolve the signed-in user object ID');

  mkdirSync('config', { recursive: true });
  let state;
  if (existsSync(statePath)) {
    state = JSON.parse(readFileSync(statePath, 'utf8'));
    requireThat(state.version === 1 && state.tenant_id === tenant && state.owner_object_id === user.id && uuid.test(state.scope_id), 'Local setup state belongs to a different tenant/user or is invalid; refusing to reuse it');
  } else {
    requireThat(!existsSync('.env') && !existsSync('config/grants.local.json'), 'Existing local configuration has no setup journal; inspect it instead of overwriting');
    const found = az(['ad', 'app', 'list', '--filter', `startswith(displayName,'${prefix}')`]);
    requireThat(found.length === 0, 'ForgeAPI Local registrations already exist without a local journal. Inspect/recover their IDs; do not create duplicates');
    state = { version: 1, tenant_id: tenant, owner_object_id: user.id, scope_id: randomUUID() };
    saveJSON(statePath, state);
  }
  const save = () => saveJSON(statePath, state);
  const common = { signInAudience: 'AzureADMyOrg', description: marker, passwordCredentials: [], keyCredentials: [], isFallbackPublicClient: false, web: { implicitGrantSettings: { enableAccessTokenIssuance: false, enableIdTokenIssuance: false } } };

  function application(kind, manifest) {
    let app;
    if (state[kind]) {
      requireThat(uuid.test(state[kind].object_id) && uuid.test(state[kind].client_id), 'Invalid journal object ID');
      app = graph('GET', `/applications/${state[kind].object_id}`);
      requireThat(app.appId === state[kind].client_id && app.displayName === manifest.displayName, 'Recorded registration does not match; refusing to change it');
    } else {
      const found = az(['ad', 'app', 'list', '--filter', `displayName eq '${manifest.displayName}'`]);
      requireThat(found.length === 0, 'A same-name registration exists without a recorded ID. Inspect it before retrying an uncertain create');
      app = graph('POST', '/applications', { ...common, ...manifest });
      state[kind] = { object_id: app.id, client_id: app.appId };
      save();
      console.log(`Created ${manifest.displayName}; IDs recorded in ${statePath}.`);
    }
    noCredentials(app);
    return app;
  }

  let api = application('api', {
    displayName: `${prefix} API`, requiredResourceAccess: [],
    api: { requestedAccessTokenVersion: 2, oauth2PermissionScopes: [{
      id: state.scope_id, value: scope, type: 'User', isEnabled: true,
      adminConsentDisplayName: 'Access ForgeAPI local executions',
      adminConsentDescription: 'Call the local ForgeAPI as the signed-in user; application grants separately control access to synthetic executions.',
      userConsentDisplayName: 'Access your ForgeAPI local executions',
      userConsentDescription: 'Allow this client to call your local ForgeAPI. ForgeAPI separately checks your current application grants.',
    }] },
  });
  const permission = api.api?.oauth2PermissionScopes;
  requireThat(api.api?.requestedAccessTokenVersion === 2 && permission?.length === 1 && permission[0].id === state.scope_id && permission[0].value === scope && permission[0].type === 'User' && permission[0].isEnabled && !api.requiredResourceAccess?.length && !api.api.preAuthorizedApplications?.length, 'API permission configuration changed; refusing to overwrite');
  const uri = `api://${api.appId}`;
  if (!api.identifierUris?.length) {
    graph('PATCH', `/applications/${api.id}`, { identifierUris: [uri] });
    api = graph('GET', `/applications/${api.id}`);
  }
  requireThat(api.identifierUris?.length === 1 && api.identifierUris[0] === uri, 'API identifier URI does not match');

  const native = application('native_client', {
    displayName: `${prefix} Client`, publicClient: { redirectUris: ['http://localhost'] },
    requiredResourceAccess: [{ resourceAppId: api.appId, resourceAccess: [{ id: state.scope_id, type: 'Scope' }] }],
  });
  const requested = native.requiredResourceAccess;
  requireThat(native.publicClient?.redirectUris?.length === 1 && native.publicClient.redirectUris[0] === 'http://localhost' && native.isFallbackPublicClient === false && !native.web?.redirectUris?.length && !native.spa?.redirectUris?.length, 'Native-client redirect/authentication configuration changed; refusing to overwrite');
  requireThat(requested?.length === 1 && requested[0].resourceAppId === api.appId && requested[0].resourceAccess?.length === 1 && requested[0].resourceAccess[0].id === state.scope_id && requested[0].resourceAccess[0].type === 'Scope', 'Native client requests unexpected permissions; refusing to overwrite');

  for (const [kind, app] of [['api', api], ['native_client', native]]) {
    const list = graph('GET', `/servicePrincipals?$filter=${encodeURIComponent(`appId eq '${app.appId}'`)}`);
    requireThat(list.value.length <= 1, 'Unexpected duplicate service principals');
    const sp = list.value[0] ?? graph('POST', '/servicePrincipals', { appId: app.appId });
    requireThat(sp.appId === app.appId && sp.accountEnabled, 'Service principal is disabled or mismatched');
    state[kind].service_principal_id = sp.id;
    save();
  }

  const identifiers = { AUTH_TENANT_ID: tenant, AUTH_AUDIENCE: api.appId, AUTH_CLIENT_ID: native.appId };
  if (existsSync('.env')) {
    const lines = readFileSync('.env', 'utf8').split(/\r?\n/).filter(line => line && !line.startsWith('#'));
    const current = Object.fromEntries(lines.map(line => { const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)]; }));
    requireThat(Object.entries(identifiers).every(([k, v]) => current[k] === v) && /^[a-f0-9]{64}$/.test(current.CURSOR_SIGNING_KEY), 'Existing .env does not match setup; it was not overwritten');
  } else {
    const content = Object.entries(identifiers).map(([k, v]) => `${k}=${v}`).join('\n') + `\nCURSOR_SIGNING_KEY=${randomBytes(32).toString('hex')}\n`;
    writeFileSync('.env', content, { flag: 'wx', mode: 0o600 });
  }
  if (!existsSync('config/grants.local.json')) {
    saveJSON('config/grants.local.json', { grants: [{ tenant_id: tenant, object_id: user.id, principal_kind: 'human', application_id: 'software-factory', environment: 'development', classification: 'synthetic', role: 'developer' }] }, 0o644);
  } else {
    console.log('Existing grant file preserved; current grants remain the authority.');
  }
  console.log('Verified: single tenant, v2 API tokens, native localhost redirect, one delegated permission, no client credentials.');
  console.log('Local configuration is ready. No admin consent or tenant policy changed. Next: docker compose up --build -d --wait; sh scripts/demo.sh');
}

try { main(); } catch (error) { console.error(`SETUP STOPPED: ${error.message}`); process.exitCode = 1; }
