'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const source = fs.readFileSync(path.join(__dirname, '../ha_service/settings.js'), 'utf8');

async function scenario(confirm, response, networkError) {
  let click;
  const button = {disabled: false, dataset: {
    endpoint: '../settings/reset', token: 'synthetic-token', date: '2000-01-01'
  }, addEventListener: (event, callback) => { assert.strictEqual(event, 'click'); click = callback; }};
  const message = {textContent: ''};
  const counts = [{textContent: '4'}, {textContent: '1'}];
  const requests = [];
  const context = {
    document: {
      addEventListener: (event, callback) => { assert.strictEqual(event, 'DOMContentLoaded'); callback(); },
      getElementById: id => id === 'reset-state' ? button : message,
      querySelectorAll: selector => { assert.strictEqual(selector, '.states dd'); return counts; }
    },
    window: {confirm: text => { assert(text.includes('totals to zero')); return confirm; }},
    fetch: async (url, request) => {
      requests.push({url, request});
      if (networkError) throw new Error('Synthetic connection failure');
      return {ok: response.ok, json: async () => response.body};
    }
  };
  vm.runInNewContext(source, context);
  await click();
  return {button, message, counts, requests};
}

(async () => {
  let result = await scenario(false);
  assert.strictEqual(result.requests.length, 0);
  assert.strictEqual(result.button.disabled, false);
  result = await scenario(true, {ok: true, body: {reset: true, mqtt_pending: false}});
  assert.strictEqual(result.requests.length, 1);
  assert.strictEqual(result.requests[0].url, '../settings/reset');
  const request = result.requests[0].request;
  assert.strictEqual(request.method, 'POST');
  assert.strictEqual(request.credentials, 'same-origin');
  assert.strictEqual(request.headers['X-Munai-Settings'], 'synthetic-token');
  assert.deepStrictEqual(JSON.parse(request.body), {confirm: true, date: '2000-01-01'});
  assert(result.counts.every(item => item.textContent === '0'));
  assert.strictEqual(result.message.textContent, 'State reset.');
  assert.strictEqual(result.button.disabled, true);
  result = await scenario(true, {ok: true, body: {reset: true, mqtt_pending: true}});
  assert.strictEqual(result.message.textContent, 'State reset. MQTT update pending.');
  result = await scenario(true, {ok: false, body: {error: 'Capture processing is busy. Try again shortly.'}});
  assert.strictEqual(result.button.disabled, false);
  assert.strictEqual(result.message.textContent, 'Capture processing is busy. Try again shortly.');
  assert.deepStrictEqual(result.counts.map(item => item.textContent), ['4', '1']);
  result = await scenario(true, null, true);
  assert.strictEqual(result.button.disabled, false);
  assert.strictEqual(result.message.textContent, 'Synthetic connection failure');
  console.log('Settings UI checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
