// Creates or updates the HubSpot records, the same calls Inkfold makes when it talks to HubSpot directly
// (Backend/app/crm.py). Never throws: any problem becomes a "failed" result sent back to Inkfold.
const BASE = ($env.HUBSPOT_BASE_URL || 'https://api.hubapi.com').replace(/\/+$/, '');
const TOKEN = $env.HUBSPOT_TOKEN || '';
const PORTAL = $env.HUBSPOT_PORTAL_ID || '';
const DEAL_STAGE = $env.HUBSPOT_DEAL_STAGE || 'closedwon';
const OBJECT_TYPE_IDS = { contacts: '0-1', deals: '0-3' };

const { document_id, record, existing } = $input.first().json.payload;
const callbackUrl = $input.first().json.payload.callback_url;
const helpers = this.helpers;

class CrmError extends Error {}
class NotFound extends Error {}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function request(method, path, body) {
  for (const attempt of [1, 2]) {
    let response;
    try {
      response = await helpers.httpRequest({
        method,
        url: BASE + path,
        headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
        body,
        json: true,
        timeout: 10000,
        returnFullResponse: true,
        ignoreHttpStatusErrors: true,
      });
    } catch (error) {
      if (attempt === 2) throw new CrmError("We couldn't reach HubSpot. Please try again.");
      continue;
    }
    const status = response.statusCode;
    if (status === 429 || status >= 500) {
      if (attempt === 2) throw new CrmError('HubSpot is busy right now. Please try again in a minute.');
      const header = Number(response.headers?.['retry-after']);
      const wait = Number.isFinite(header) ? Math.min(2, Math.max(0, header)) : 1;
      await sleep(wait * 1000);
      continue;
    }
    if (status === 401 || status === 403) throw new CrmError('HubSpot rejected the access token. Check HUBSPOT_TOKEN.');
    if (status === 404) throw new NotFound(path);
    if (status >= 400) {
      const message = (response.body && response.body.message) || `status ${status}`;
      throw new CrmError(`HubSpot didn't accept the record: ${message}`);
    }
    return response.body || {};
  }
}

async function createOrUpdate(objectType, existingId, properties) {
  // A record deleted in HubSpot since the last sync is simply made again.
  if (existingId) {
    try {
      return (await request('PATCH', `/crm/v3/objects/${objectType}/${existingId}`, { properties })).id;
    } catch (error) {
      if (!(error instanceof NotFound)) throw error;
    }
  }
  return (await request('POST', `/crm/v3/objects/${objectType}`, { properties })).id;
}

async function associate(fromType, fromId, toType, toId) {
  await request('PUT', `/crm/v4/objects/${fromType}/${fromId}/associations/default/${toType}/${toId}`);
}

async function saveCompany(company, existingId) {
  if (existingId) return createOrUpdate('companies', existingId, company);
  const [prop, value] = company.domain ? ['domain', company.domain] : ['name', company.name];
  const found = await request('POST', '/crm/v3/objects/companies/search', {
    filterGroups: [{ filters: [{ propertyName: prop, operator: 'EQ', value }] }],
    limit: 1,
  });
  if (found.results && found.results.length) {
    const id = found.results[0].id;
    await request('PATCH', `/crm/v3/objects/companies/${id}`, { properties: company });
    return id;
  }
  return createOrUpdate('companies', null, company);
}

function dealProperties(deal) {
  const properties = { dealname: deal.dealname, amount: deal.amount, dealstage: DEAL_STAGE, pipeline: 'default' };
  if (deal.currency) properties.deal_currency_code = deal.currency;
  if (deal.closedate) properties.closedate = `${deal.closedate}T00:00:00Z`;
  return properties;
}

const url = (type, id) => (PORTAL && id ? `https://app.hubspot.com/contacts/${PORTAL}/record/${OBJECT_TYPE_IDS[type]}/${id}` : null);

let result;
try {
  if (!TOKEN) throw new CrmError('The n8n workflow has no HubSpot token. Set HUBSPOT_TOKEN in n8n.');
  const contact = record.contact;
  const upserted = await request('POST', '/crm/v3/objects/contacts/batch/upsert', {
    inputs: [{ idProperty: 'email', id: contact.email, properties: contact }],
  });
  const contactId = upserted.results[0].id;
  const companyId = record.company ? await saveCompany(record.company, existing.company_id) : null;
  const dealId = record.deal ? await createOrUpdate('deals', existing.deal_id, dealProperties(record.deal)) : null;
  const noteId = record.note
    ? await createOrUpdate('notes', existing.note_id, {
        hs_note_body: record.note,
        hs_timestamp: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
      })
    : null;

  if (companyId) await associate('contacts', contactId, 'companies', companyId);
  if (dealId) {
    await associate('deals', dealId, 'contacts', contactId);
    if (companyId) await associate('deals', dealId, 'companies', companyId);
  }
  if (noteId) await associate('notes', noteId, 'contacts', contactId);

  result = {
    document_id,
    status: 'synced',
    crm: {
      contact_id: contactId,
      company_id: companyId,
      deal_id: dealId,
      note_id: noteId,
      contact_url: url('contacts', contactId),
      deal_url: url('deals', dealId),
    },
    detail: '',
  };
} catch (error) {
  const detail = error instanceof CrmError ? error.message : error instanceof NotFound ? `HubSpot couldn't find ${error.message}.` : 'The n8n workflow hit an unexpected error.';
  result = { document_id, status: 'failed', crm: {}, detail };
}

return [{ json: { callback_url: callbackUrl, result } }];
