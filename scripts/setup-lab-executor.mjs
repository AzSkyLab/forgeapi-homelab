// Explicitly approved home-lab exception: one seven-day certificate, no password.
// Azure CLI is used only by this bootstrap administrator, never by the worker.
import { execFileSync } from 'node:child_process';
import { createHash, createPrivateKey, randomUUID, X509Certificate } from 'node:crypto';
import { appendFileSync, existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

process.chdir(resolve(dirname(fileURLToPath(import.meta.url)), '..'));
process.umask(0o077);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const journal = '.local/lab-executor/setup.json';
const certPath = resolve('.local/lab-executor/client.pem');
const publicPath = resolve('.local/lab-executor/certificate.crt');
const configPath = 'config/terraform-executor.local.json';
const marker = 'ForgeAPI home-lab Terraform executor; seven-day certificate; no human CLI runtime fallback.';
const role = 'f25e0fa2-a7c8-4377-a976-54943a77a395';
function requireThat(ok, message) { if (!ok) throw new Error(message); }
function save(path, value) {
  const temporary = path + '.tmp';
  writeFileSync(temporary, JSON.stringify(value, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  renameSync(temporary, path);
}
function az(args) {
  try {
    const raw = execFileSync('az', [...args, '--only-show-errors', '--output', 'json'], {
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 60000,
    });
    return raw.trim() ? JSON.parse(raw) : null;
  } catch {
    throw new Error('Azure bootstrap operation failed; raw diagnostics suppressed. Inspect the recorded pending operation before retrying writes.');
  }
}
function graph(method, path, body) {
  const args = ['rest', '--method', method, '--url', 'https://graph.microsoft.com/v1.0' + path];
  if (body) args.push('--headers', 'Content-Type=application/json', '--body', JSON.stringify(body));
  return az(args);
}
function main() {
  const args = process.argv.slice(2);
  requireThat(args.length === 6 && args[0] === '--tenant' && args[2] === '--subscription' && args[4] === '--resource-group', 'Usage: node scripts/setup-lab-executor.mjs --tenant <approved-UUID> --subscription <approved-UUID> --resource-group <existing-RG>');
  const [tenant, subscription, rg] = [args[1], args[3], args[5]];
  requireThat(uuid.test(tenant) && uuid.test(subscription) && /^[A-Za-z0-9_()-]{1,90}$/.test(rg), 'Invalid approved target');
  const account = az(['account', 'show', '--subscription', subscription]);
  requireThat(account.id === subscription && account.tenantId === tenant && account.user?.type === 'user' && account.state === 'Enabled', 'Expected confirmed tenant/subscription and bootstrap human login');
  requireThat(az(['cloud', 'show']).name === 'AzureCloud', 'Only commercial Azure is supported');
  const scope = `/subscriptions/${subscription}/resourceGroups/${rg}`;
  const group = az(['group', 'show', '--subscription', subscription, '--name', rg]);
  requireThat(group.id.toLowerCase() === scope.toLowerCase(), 'Existing resource group does not match');
  mkdirSync('.local/lab-executor', { recursive: true, mode: 0o700 });
  const directory = resolve('.local/lab-executor');
  requireThat(realpathSync(directory) === directory && (lstatSync(directory).mode & 0o077) === 0, 'Lab credential directory must be private and symlink-free');
  let state;
  if (existsSync(journal)) {
    state = JSON.parse(readFileSync(journal, 'utf8'));
    requireThat(state.tenant_id === tenant && state.scope === scope && !state.pending, 'Journal target differs or an Azure write is uncertain; inspect it, do not recreate objects');
  } else {
    requireThat(!existsSync(certPath) && !existsSync(publicPath) && !existsSync(configPath), 'Existing unjournaled lab identity files; refusing overwrite');
    state = { tenant_id: tenant, scope, key_id: randomUUID(), assignment_id: randomUUID() };
    save(journal, state);
    try {
      execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:3072', '-sha256', '-nodes', '-days', '7', '-subj', '/CN=ForgeAPI Lab Terraform', '-keyout', certPath, '-out', publicPath], { stdio: ['ignore', 'pipe', 'pipe'], timeout: 60000 });
      appendFileSync(certPath, readFileSync(publicPath));
    } catch { throw new Error('Certificate generation failed; retain and inspect local files before retrying'); }
  }
  for (const path of [certPath, publicPath]) {
    const info = lstatSync(path);
    requireThat(info.isFile() && (info.mode & 0o077) === 0 && realpathSync(path) === path, 'Lab certificate files must be private, regular and symlink-free');
  }
  const cert = new X509Certificate(readFileSync(publicPath));
  requireThat(cert.checkPrivateKey(createPrivateKey(readFileSync(certPath))), 'Certificate/key mismatch');
  requireThat(Date.parse(cert.validTo) > Date.now() && Date.parse(cert.validTo) - Date.parse(cert.validFrom) <= 7 * 86400000, 'Lab certificate expired or exceeds seven days; rotation requires explicit review');
  const fingerprint = createHash('sha256').update(cert.raw).digest('hex');
  function write(kind, fn) {
    state.pending = kind;
    save(journal, state);
    const result = fn();
    delete state.pending;
    return result;
  }
  if (!state.application_id) {
    const app = write('create_application', () => graph('POST', '/applications', {
      displayName: 'ForgeAPI Lab Terraform', description: marker, signInAudience: 'AzureADMyOrg',
      isFallbackPublicClient: false, requiredResourceAccess: [], passwordCredentials: [],
      keyCredentials: [{ keyId: state.key_id, type: 'AsymmetricX509Cert', usage: 'Verify', displayName: 'Seven-day home-lab certificate', key: cert.raw.toString('base64'), startDateTime: new Date(cert.validFrom).toISOString(), endDateTime: new Date(cert.validTo).toISOString() }],
    }));
    state.application_id = app.id;
    state.client_id = app.appId;
    save(journal, state);
    console.log('Created dedicated lab application; identifiers journaled. No password created.');
  }
  const app = graph('GET', `/applications/${state.application_id}?$select=id,appId,description,signInAudience,passwordCredentials,keyCredentials,requiredResourceAccess`);
  requireThat(app.appId === state.client_id && app.description === marker && app.signInAudience === 'AzureADMyOrg' && !app.passwordCredentials.length && !app.requiredResourceAccess.length && app.keyCredentials.length === 1 && app.keyCredentials[0].keyId === state.key_id && app.keyCredentials[0].key === cert.raw.toString('base64'), 'Recorded application/certificate does not match; no overwrite');
  if (!state.principal_id) {
    const sp = write('create_service_principal', () => graph('POST', '/servicePrincipals', { appId: state.client_id }));
    state.principal_id = sp.id;
    save(journal, state);
  }
  const sp = graph('GET', `/servicePrincipals/${state.principal_id}`);
  requireThat(sp.appId === state.client_id && sp.accountEnabled && sp.servicePrincipalType === 'Application', 'Recorded service principal does not match');
  const assignmentURL = `https://management.azure.com${scope}/providers/Microsoft.Authorization/roleAssignments/${state.assignment_id}?api-version=2022-04-01`;
  if (!state.role_created) {
    write('assign_rg_key_vault_contributor', () => az(['role', 'assignment', 'create', '--name', state.assignment_id, '--assignee-object-id', state.principal_id, '--assignee-principal-type', 'ServicePrincipal', '--role', role, '--scope', scope, '--subscription', subscription]));
    state.role_created = true;
    save(journal, state);
  }
  const assignment = az(['rest', '--method', 'get', '--url', assignmentURL]).properties;
  requireThat(assignment.principalId === state.principal_id && assignment.scope.toLowerCase() === scope.toLowerCase() && assignment.roleDefinitionId.endsWith('/' + role), 'Role assignment verification failed');
  const config = { mode: 'lab_certificate', tenant_id: tenant, client_id: state.client_id, principal_id: state.principal_id, certificate_sha256: fingerprint, certificate_path: certPath };
  if (existsSync(configPath)) requireThat(JSON.stringify(JSON.parse(readFileSync(configPath, 'utf8'))) === JSON.stringify(config), 'Runtime config differs; refusing overwrite');
  else save(configPath, config);
  console.log(`PASS: lab service principal and RG-only Key Vault Contributor verified. Certificate expires ${new Date(cert.validTo).toISOString()}.`);
  console.log('Runtime identifiers/path saved in ignored config/terraform-executor.local.json. Private key never printed or uploaded.');
}
try { main(); } catch (error) { console.error(`SETUP STOPPED: ${error.message}`); process.exitCode = 1; }
