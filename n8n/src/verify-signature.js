// Checks the HMAC Inkfold sends, over the exact bytes received, so nobody else can trigger this workflow.
const crypto = require('crypto');

const TOLERANCE_SECONDS = 300;
const item = $input.first();
const headers = item.json.headers || {};
const timestamp = headers['x-inkfold-timestamp'] || '';
const signature = headers['x-inkfold-signature'] || '';
const secret = $env.INKFOLD_SHARED_SECRET || '';
const raw = await this.helpers.getBinaryDataBuffer(0, 'data');

let valid = Boolean(secret) && /^\d+$/.test(timestamp) && signature.startsWith('sha256=');
if (valid) {
  valid = Math.abs(Date.now() / 1000 - Number(timestamp)) <= TOLERANCE_SECONDS;
}
if (valid) {
  const expected = crypto.createHmac('sha256', secret).update(`${timestamp}.`).update(raw).digest('hex');
  const given = signature.slice('sha256='.length);
  valid = given.length === expected.length && crypto.timingSafeEqual(Buffer.from(given), Buffer.from(expected));
}

return [{ json: { valid, payload: valid ? JSON.parse(raw.toString('utf8')) : null } }];
