// Runs the n8n Code node sources outside n8n, with n8n's globals faked, so they can be tested quickly.
// Run with: node --test n8n/test/
import assert from 'node:assert/strict'
import { createHmac } from 'node:crypto'
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const require = createRequire(import.meta.url)
const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor
const SECRET = 'test-secret'

function runNode(file, { input, env = {}, helpers = {} }) {
  const source = readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8')
  const fn = new AsyncFunction('$input', '$env', 'require', source)
  return fn.call({ helpers }, { first: () => input }, env, require)
}

const sign = (timestamp, body) => 'sha256=' + createHmac('sha256', SECRET).update(`${timestamp}.${body}`).digest('hex')

function webhookItem(body, { timestamp = String(Math.floor(Date.now() / 1000)), signature } = {}) {
  return {
    input: {
      json: { headers: { 'x-inkfold-timestamp': timestamp, 'x-inkfold-signature': signature ?? sign(timestamp, body) } },
    },
    helpers: { getBinaryDataBuffer: async () => Buffer.from(body) },
  }
}

const payload = {
  document_id: 'doc-1',
  callback_url: 'http://localhost:8020/api/sync/callback',
  existing: {},
  record: {
    contact: { email: 'sara@brightpath.example', firstname: 'Sara', lastname: 'Malik', phone: '+14155550123', jobtitle: 'Founder' },
    company: { name: 'Brightpath Studio', domain: 'brightpath.example' },
    deal: { dealname: 'Invoice INV-1 — Brightpath Studio', amount: '1249.19', currency: 'USD', closedate: '2026-10-01', invoice_number: 'INV-1' },
    note: 'Imported by Inkfold from invoice.pdf.',
  },
}

test('a correctly signed request is accepted and parsed', async () => {
  const body = JSON.stringify(payload)
  const { input, helpers } = webhookItem(body)
  const [out] = await runNode('verify-signature.js', { input, helpers, env: { INKFOLD_SHARED_SECRET: SECRET } })
  assert.equal(out.json.valid, true)
  assert.equal(out.json.payload.document_id, 'doc-1')
})

test('a wrong signature, an old timestamp or a missing secret is rejected', async () => {
  const body = JSON.stringify(payload)
  const cases = [
    [webhookItem(body, { signature: sign('1', body) }), SECRET],
    [webhookItem(body, { timestamp: String(Math.floor(Date.now() / 1000) - 301) }), SECRET],
    [webhookItem(body, { signature: 'sha256=abc' }), SECRET],
    [webhookItem(body), ''],
  ]
  for (const [{ input, helpers }, secret] of cases) {
    const [out] = await runNode('verify-signature.js', { input, helpers, env: { INKFOLD_SHARED_SECRET: secret } })
    assert.equal(out.json.valid, false)
    assert.equal(out.json.payload, null)
  }
})

test('a changed body fails the check', async () => {
  const body = JSON.stringify(payload)
  const { input, helpers } = webhookItem(body)
  helpers.getBinaryDataBuffer = async () => Buffer.from(body.replace('doc-1', 'doc-2'))
  const [out] = await runNode('verify-signature.js', { input, helpers, env: { INKFOLD_SHARED_SECRET: SECRET } })
  assert.equal(out.json.valid, false)
})

function fakeHubSpot(overrides = {}) {
  const calls = []
  let next = 100
  const httpRequest = async ({ method, url, body }) => {
    const path = url.replace('https://hub.test', '')
    calls.push({ method, path, body })
    const override = overrides[`${method} ${path}`]
    if (override) return typeof override === 'function' ? override() : override
    if (path.endsWith('/batch/upsert')) return { statusCode: 200, headers: {}, body: { results: [{ id: '501' }] } }
    if (path.endsWith('/companies/search')) return { statusCode: 200, headers: {}, body: { results: [] } }
    if (method === 'PUT' || method === 'GET') return { statusCode: 200, headers: {}, body: {} }
    return { statusCode: 201, headers: {}, body: { id: String(next++) } }
  }
  return { calls, helpers: { httpRequest } }
}

const env = { HUBSPOT_BASE_URL: 'https://hub.test', HUBSPOT_TOKEN: 'tok', HUBSPOT_PORTAL_ID: '42', INKFOLD_SHARED_SECRET: SECRET }

test('syncing creates the contact, company, deal and note and links them', async () => {
  const { calls, helpers } = fakeHubSpot()
  const [out] = await runNode('sync-to-hubspot.js', { input: { json: { payload } }, env, helpers })
  const { result } = out.json
  assert.equal(out.json.callback_url, payload.callback_url)
  assert.equal(result.status, 'synced')
  assert.deepEqual(result.crm, {
    contact_id: '501',
    company_id: '100',
    deal_id: '101',
    note_id: '102',
    contact_url: 'https://app.hubspot.com/contacts/42/record/0-1/501',
    deal_url: 'https://app.hubspot.com/contacts/42/record/0-3/101',
  })
  const deal = calls.find((c) => c.method === 'POST' && c.path === '/crm/v3/objects/deals')
  assert.deepEqual(deal.body.properties, {
    dealname: 'Invoice INV-1 — Brightpath Studio',
    amount: '1249.19',
    dealstage: 'closedwon',
    pipeline: 'default',
    deal_currency_code: 'USD',
    closedate: '2026-10-01T00:00:00Z',
  })
  const links = calls.filter((c) => c.method === 'PUT').map((c) => c.path)
  assert.deepEqual(links, [
    '/crm/v4/objects/contacts/501/associations/default/companies/100',
    '/crm/v4/objects/deals/101/associations/default/contacts/501',
    '/crm/v4/objects/deals/101/associations/default/companies/100',
    '/crm/v4/objects/notes/102/associations/default/contacts/501',
  ])
})

test('a stage name becomes the account\'s numeric stage ID', async () => {
  const stages = [
    { id: '4561146603', label: 'Appointment scheduled', metadata: { isClosed: 'false', probability: '0.2' } },
    { id: '4561146608', label: 'Closed won', metadata: { isClosed: 'true', probability: '1.0' } },
  ]
  const { calls, helpers } = fakeHubSpot({ 'GET /crm/v3/pipelines/deals/default': { statusCode: 200, headers: {}, body: { stages } } })
  await runNode('sync-to-hubspot.js', { input: { json: { payload } }, env, helpers })
  const deal = calls.find((c) => c.method === 'POST' && c.path === '/crm/v3/objects/deals')
  assert.equal(deal.body.properties.dealstage, '4561146608')
})

test('an existing company found by domain is updated, and a deleted deal is made again', async () => {
  const { calls, helpers } = fakeHubSpot({
    'POST /crm/v3/objects/companies/search': { statusCode: 200, headers: {}, body: { results: [{ id: '900' }] } },
    'PATCH /crm/v3/objects/deals/77': { statusCode: 404, headers: {}, body: { message: 'not found' } },
  })
  const input = { json: { payload: { ...payload, existing: { deal_id: '77' } } } }
  const [out] = await runNode('sync-to-hubspot.js', { input, env, helpers })
  assert.equal(out.json.result.status, 'synced')
  assert.equal(out.json.result.crm.company_id, '900')
  assert.ok(calls.some((c) => c.method === 'PATCH' && c.path === '/crm/v3/objects/companies/900'))
  assert.ok(calls.some((c) => c.method === 'POST' && c.path === '/crm/v3/objects/deals'))
})

test('a rejected token becomes a failed result instead of an error', async () => {
  const { helpers } = fakeHubSpot({
    'POST /crm/v3/objects/contacts/batch/upsert': { statusCode: 401, headers: {}, body: {} },
  })
  const [out] = await runNode('sync-to-hubspot.js', { input: { json: { payload } }, env, helpers })
  assert.deepEqual(out.json.result, {
    document_id: 'doc-1',
    status: 'failed',
    crm: {},
    detail: 'HubSpot rejected the access token. Check HUBSPOT_TOKEN.',
  })
})

test('a busy HubSpot is retried once', async () => {
  let tries = 0
  const { helpers } = fakeHubSpot({
    'POST /crm/v3/objects/contacts/batch/upsert': () =>
      ++tries === 1
        ? { statusCode: 429, headers: { 'retry-after': '0' }, body: {} }
        : { statusCode: 200, headers: {}, body: { results: [{ id: '501' }] } },
  })
  const [out] = await runNode('sync-to-hubspot.js', { input: { json: { payload } }, env, helpers })
  assert.equal(tries, 2)
  assert.equal(out.json.result.status, 'synced')
})

test('a missing token fails without calling HubSpot', async () => {
  const { calls, helpers } = fakeHubSpot()
  const [out] = await runNode('sync-to-hubspot.js', { input: { json: { payload } }, env: { ...env, HUBSPOT_TOKEN: '' }, helpers })
  assert.equal(out.json.result.status, 'failed')
  assert.equal(calls.length, 0)
})

test('the result is signed the way the API checks it', async () => {
  const result = { document_id: 'doc-1', status: 'synced', crm: { contact_id: '1' }, detail: '' }
  const [out] = await runNode('sign-result.js', {
    input: { json: { callback_url: 'http://cb', result } },
    env: { INKFOLD_SHARED_SECRET: SECRET },
  })
  assert.equal(out.json.body, JSON.stringify(result))
  assert.equal(out.json.signature, sign(out.json.timestamp, out.json.body))
})
