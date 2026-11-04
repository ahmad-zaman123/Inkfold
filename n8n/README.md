# n8n workflow

Inkfold can save records to HubSpot in two ways:

- **Directly** (the live demo does this). The API calls HubSpot itself (`Backend/app/crm.py`).
- **Through n8n.** The API signs the reviewed record and posts it to this workflow. The workflow saves it to HubSpot and posts a signed result back. Use this when a team wants to add its own steps, such as a Slack message or a second CRM, without changing the app.

```
Inkfold API ──signed POST──▶ n8n webhook ──▶ verify signature ──▶ 202 Accepted
                                                     │ bad signature ──▶ 401, nothing else runs
                                                     ▼
                                     HubSpot: contact, company, deal, note, links
                                                     ▼
Inkfold API ◀──signed callback── sign result ◀─────┘   (a HubSpot error comes back as "failed")
```

## Files

| File | What it is |
| --- | --- |
| `document-to-hubspot.json` | The workflow. Import it into any n8n 2.x. |
| `src/*.js` | The three Code nodes as plain files. `node src/build.mjs` writes them into the workflow JSON. Edit these, not the JSON. |
| `run-local.sh` | Installs n8n 2.46.4 into `n8n/.data/runtime` (git-ignored), imports the workflow, switches it on and starts n8n on port 5690. |
| `test/code-nodes.test.mjs` | Unit tests for the Code nodes, run outside n8n with a fake HubSpot. |
| `test/e2e.py` | Runs the real API, real n8n and a fake HubSpot together, each with throwaway data, and checks the whole round trip. |

## Signing

Both directions use the same scheme. The sender puts the Unix time in `X-Inkfold-Timestamp` and
`sha256=<hex HMAC-SHA256 of "timestamp.body">` in `X-Inkfold-Signature`, using the shared secret.
The receiver checks the signature over the exact bytes it got, in constant time, and rejects anything
more than 5 minutes old. Inkfold only accepts a callback for a document that's still waiting, so a
repeated callback changes nothing.

## Run it locally

You need Node 24 or newer (n8n 2.46 needs it).

1. Copy `n8n/.env.example` to `n8n/.env` and fill in `INKFOLD_SHARED_SECRET`, `HUBSPOT_TOKEN` and `HUBSPOT_PORTAL_ID`.
2. In `Backend/.env`, set `N8N_WEBHOOK_URL=http://localhost:5690/webhook/inkfold`, the same secret as `N8N_SHARED_SECRET`, and `PUBLIC_API_URL=http://localhost:8020`.
3. Start n8n with `n8n/run-local.sh`, then start the API as usual. "Send to HubSpot" now goes through n8n. You can watch each run in the n8n editor at http://localhost:5690.

The HubSpot token is read from the environment (`$env`) instead of an n8n credential, so the workflow
imports and runs with no clicks. On a shared n8n server, move it to a HubSpot credential and keep
`N8N_BLOCK_ENV_ACCESS_IN_NODE` on.

## Tests

```bash
node --test n8n/test/*.test.mjs            # Code nodes, a second or two
Backend/.venv/bin/python n8n/test/e2e.py   # whole round trip, about a minute; uses ports 8021 and 5691
```

Neither test talks to the real HubSpot.
