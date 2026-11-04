// Signs the result the same way Inkfold signs its requests, so the API knows it came from this workflow.
const crypto = require('crypto');

const { callback_url, result } = $input.first().json;
const body = JSON.stringify(result);
const timestamp = String(Math.floor(Date.now() / 1000));
const signature = crypto.createHmac('sha256', $env.INKFOLD_SHARED_SECRET).update(`${timestamp}.${body}`).digest('hex');

return [{ json: { callback_url, body, timestamp, signature: `sha256=${signature}` } }];
