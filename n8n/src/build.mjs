// Builds ../document-to-hubspot.json from the Code node sources here, so the JavaScript stays readable and testable.
import { readFileSync, writeFileSync } from 'node:fs'

const code = (name) => readFileSync(new URL(name, import.meta.url), 'utf8')

const node = (id, name, type, typeVersion, position, parameters, extra = {}) => ({ id, name, type, typeVersion, position, parameters, ...extra })

const nodes = [
  node('b1d0c1a0-0001-4c5e-9a10-000000000001', 'Inkfold webhook', 'n8n-nodes-base.webhook', 2, [0, 300], {
    httpMethod: 'POST',
    path: 'inkfold',
    responseMode: 'responseNode',
    options: { rawBody: true },
  }, { webhookId: '6f3c2a10-5b1e-4d7a-9c2e-8a1f0e4b7d21' }),
  node('b1d0c1a0-0002-4c5e-9a10-000000000002', 'Verify signature', 'n8n-nodes-base.code', 2, [240, 300], {
    mode: 'runOnceForAllItems',
    jsCode: code('verify-signature.js'),
  }),
  node('b1d0c1a0-0003-4c5e-9a10-000000000003', 'Signature valid?', 'n8n-nodes-base.if', 2.2, [480, 300], {
    conditions: {
      options: { caseSensitive: true, leftValue: '', typeValidation: 'strict', version: 2 },
      conditions: [
        {
          id: '2b7e9a52-1c3d-4f60-8e91-0d5a7b3c4e10',
          leftValue: '={{ $json.valid }}',
          rightValue: '',
          operator: { type: 'boolean', operation: 'true', singleValue: true },
        },
      ],
      combinator: 'and',
    },
    options: {},
  }),
  node('b1d0c1a0-0004-4c5e-9a10-000000000004', 'Accept (202)', 'n8n-nodes-base.respondToWebhook', 1.1, [720, 200], {
    respondWith: 'json',
    responseBody: '={{ { "accepted": true } }}',
    options: { responseCode: 202 },
  }),
  node('b1d0c1a0-0005-4c5e-9a10-000000000005', 'Reject (401)', 'n8n-nodes-base.respondToWebhook', 1.1, [720, 420], {
    respondWith: 'json',
    responseBody: '={{ { "detail": "Invalid signature." } }}',
    options: { responseCode: 401 },
  }),
  node('b1d0c1a0-0006-4c5e-9a10-000000000006', 'Sync to HubSpot', 'n8n-nodes-base.code', 2, [960, 200], {
    mode: 'runOnceForAllItems',
    jsCode: code('sync-to-hubspot.js'),
  }),
  node('b1d0c1a0-0007-4c5e-9a10-000000000007', 'Sign result', 'n8n-nodes-base.code', 2, [1200, 200], {
    mode: 'runOnceForAllItems',
    jsCode: code('sign-result.js'),
  }),
  node('b1d0c1a0-0008-4c5e-9a10-000000000008', 'Send result to Inkfold', 'n8n-nodes-base.httpRequest', 4.2, [1440, 200], {
    method: 'POST',
    url: '={{ $json.callback_url }}',
    sendHeaders: true,
    headerParameters: {
      parameters: [
        { name: 'X-Inkfold-Timestamp', value: '={{ $json.timestamp }}' },
        { name: 'X-Inkfold-Signature', value: '={{ $json.signature }}' },
      ],
    },
    sendBody: true,
    contentType: 'raw',
    rawContentType: 'application/json',
    body: '={{ $json.body }}',
    options: { timeout: 10000 },
  }),
]

const link = (to) => [{ node: to, type: 'main', index: 0 }]
const workflow = {
  name: 'Inkfold: document to HubSpot',
  nodes,
  connections: {
    'Inkfold webhook': { main: [link('Verify signature')] },
    'Verify signature': { main: [link('Signature valid?')] },
    'Signature valid?': { main: [link('Accept (202)'), link('Reject (401)')] },
    'Accept (202)': { main: [link('Sync to HubSpot')] },
    'Sync to HubSpot': { main: [link('Sign result')] },
    'Sign result': { main: [link('Send result to Inkfold')] },
  },
  settings: { executionOrder: 'v1' },
  pinData: {},
  active: false,
  id: 'fwDocToHubspot01',
}

writeFileSync(new URL('../document-to-hubspot.json', import.meta.url), JSON.stringify(workflow, null, 2) + '\n')
console.log('Wrote n8n/document-to-hubspot.json')
