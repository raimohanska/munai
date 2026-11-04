/* Reusable, progressively loaded review controller. No predictions are labels. */
(function () {
  'use strict';
  const dialog = document.getElementById('review-dialog');
  if (!dialog) return;
  const get = name => document.getElementById('review-' + name);
  let context, source, currentButton, busy = false, sequence = 0;
  let lastRegion = '', lastLabel = '';
  const api = dialog.dataset.api;
  const region = get('region'), label = get('label'), message = get('message');
  const option = (value, text) => new Option(text, value);
  const record = () => context && context.records[context.layout + '-' + region.value];
  function markButtons(checksum, records) {
    document.querySelectorAll('[data-review-source]').forEach(button => {
      if (button.dataset.reviewImage === checksum) {
        const reviewed = Object.keys(records).length > 0;
        button.toggleAttribute('data-reviewed', reviewed);
        button.textContent = reviewed ? '✓' : '✎';
        button.setAttribute('aria-label', reviewed ? 'Reviewed — review & teach' : 'Review & teach');
      }
    });
  }
  function help() {
    get('new-field').hidden = label.value !== '__new';
    const hen = !['empty', 'obscured'].includes(label.value);
    const promotable = context && context.layout === context.current_layout &&
      label.value !== 'obscured' && ((label.value === 'empty' && region.value === 'whole') ||
      (hen && ((context.layout === 'none' && region.value === 'whole') ||
               (['vertical', 'horizontal'].includes(context.layout) && region.value !== 'whole'))));
    get('help').textContent = promotable
      ? (label.value === 'empty' ? 'Adds full-frame empty calibration. No hens anywhere; eggs are allowed.'
        : 'Adds this view as a shared hen reference. Check that one hen is clearly identifiable; position is not identity.')
      : label.value === 'obscured' ? 'Annotation only. Saves a reviewed obscured example; it does not train the detector yet.'
      : label.value === 'empty' && region.value !== 'whole' ? 'Annotation only. Empty calibration requires the entire frame to be hen-free.'
      : context.layout === 'unknown' ? 'Annotation only. This image’s capture-time layout is unavailable.'
      : context.layout !== context.current_layout ? 'Annotation only. This image uses a different recognition layout.'
      : 'Annotation only. Select a half-frame or center view to teach a hen in split mode.';
    get('save').textContent = promotable ? 'Save & use' : 'Save annotation';
    get('confirm').parentElement.lastChild.textContent = label.value === 'obscured'
      ? ' I checked this view and want to record it as obscured (annotation only).'
      : ' I checked this view: no hens for Empty, or one clearly identifiable hen for a hen reference.';
    get('undo').hidden = !record();
    get('save').disabled = busy || !!record();
    const zone = get('zone');
    zone.className = 'zone ' + context.layout + '-' + region.value;
    zone.hidden = region.value === 'whole';
  }
  function fill(next, preferred) {
    context = next;
    region.disabled = false; label.disabled = false;
    region.replaceChildren(...next.regions.map(value => option(value, value === 'whole' ? 'Whole frame' : value[0].toUpperCase() + value.slice(1))));
    region.value = next.regions.includes(preferred) ? preferred : 'whole';
    label.replaceChildren(option('empty', 'Empty (no hens)'), option('obscured', 'Obscured'),
      ...next.identities.map(value => option(value, value)), option('__new', '＋ New hen…'));
    const saved = record();
    if (saved && !Array.from(label.options).some(item => item.value === saved.label))
      label.add(option(saved.label, saved.label), label.options.length - 1);
    label.value = saved ? saved.label : (next.identities.includes(lastLabel) ? lastLabel : 'obscured');
    get('confirm').checked = false;
    currentButton.dataset.reviewImage = next.image;
    markButtons(next.image, next.records);
    help();
    if (saved) message.textContent = saved.status === 'active' ? 'Reviewed · reference activated. Undo before changing this region.'
      : saved.status === 'pending' ? 'Activation interrupted. Undo this review before retrying.' : 'Reviewed · annotation only.';
  }
  async function load(button, quiet) {
    const response = await fetch(api + '?source=' + encodeURIComponent(button.dataset.reviewSource), {cache: 'no-store'});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'Review unavailable.');
    button.dataset.reviewImage = value.image;
    markButtons(value.image, value.records);
    return value;
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-review-source]');
    if (!button || busy) return;
    const ticket = ++sequence;
    currentButton = button;
    context = null;
    region.disabled = true; label.disabled = true;
    get('new-field').hidden = true; get('new').value = '';
    get('confirm').checked = false;
    source = button.dataset.reviewSource;
    get('image').src = button.parentElement.querySelector('img').src;
    get('filename').textContent = source.split('/').pop();
    message.textContent = 'Loading review…';
    get('save').disabled = true;
    get('undo').hidden = true;
    get('zone').hidden = true;
    region.replaceChildren(); label.replaceChildren();
    dialog.showModal();
    try {
      const next = await load(button);
      if (ticket !== sequence || !dialog.open) return;
      message.textContent = '';
      fill(next, button.dataset.reviewRegion !== 'whole' ? button.dataset.reviewRegion : lastRegion);
    } catch (error) { if (ticket === sequence) message.textContent = error.message; }
  });
  region.addEventListener('change', () => {
    const saved = record();
    if (saved) {
      if (!Array.from(label.options).some(item => item.value === saved.label)) label.add(option(saved.label, saved.label));
      label.value = saved.label;
    }
    message.textContent = saved ? 'Reviewed · ' + saved.status + '. Undo before changing this region.' : '';
    get('confirm').checked = false;
    help();
  });
  label.addEventListener('change', () => { get('confirm').checked = false; help(); });
  async function submit(action) {
    if (!context || busy) return;
    if (action === 'save' && !get('confirm').checked) {
      message.textContent = 'Check the view and tick the confirmation first.'; return;
    }
    const chosen = label.value === '__new' ? get('new').value.trim().toLowerCase() : label.value;
    if (label.value === '__new' && (!/^[a-z0-9][a-z0-9_-]{0,47}$/.test(chosen) ||
      ['empty', 'obscured', 'unknown', 'unreviewed', 'eggs', 'pending', 'no_chicken',
       'unavailable', 'versions', 'staging', 'state'].includes(chosen))) {
      message.textContent = 'Choose a hen name: 1–48 lowercase letters, digits, hyphens or underscores; not a reserved label.';
      get('new').focus(); return;
    }
    busy = true; help(); get('undo').disabled = true;
    message.textContent = action === 'undo' ? 'Undoing…' : 'Saving…';
    try {
      const response = await fetch(api, {method: 'POST', headers: {
        'Content-Type': 'application/json', 'X-Munai-Review': context.token
      }, body: JSON.stringify({source, image: context.image, layout: context.layout,
        revision: context.revision, references: context.references, region: region.value,
        label: chosen, action, confirmed: get('confirm').checked})});
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || 'Review failed; reopen and try again.');
      lastRegion = region.value; lastLabel = chosen;
      if (action === 'undo') {
        fill(value, lastRegion); message.textContent = 'Review undone. Original prediction and journal are unchanged.';
      } else {
        fill(value, lastRegion);
        const saved = record();
        const text = saved.status === 'active' ? 'Reference saved and activated for the next detection.' : 'Annotation saved (not a training reference).';
        message.textContent = text;
        currentButton.title = text;
        let toast = document.getElementById('review-feedback');
        if (!toast) {
          toast = document.createElement('p'); toast.id = 'review-feedback';
          toast.setAttribute('role', 'status'); document.body.appendChild(toast);
        }
        toast.textContent = text;
        window.setTimeout(() => { if (toast.textContent === text) toast.remove(); }, 6000);
        dialog.close(); currentButton.focus();
      }
    } catch (error) { message.textContent = error.message; }
    finally { busy = false; help(); get('undo').disabled = false; }
  }
  get('save').addEventListener('click', () => submit('save'));
  get('undo').addEventListener('click', () => submit('undo'));
  get('close').addEventListener('click', () => { if (!busy) { ++sequence; dialog.close(); } });
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); else ++sequence; });
  // Fetch status only for visible photos, so long galleries do not read every
  // stored image on page load. The same hash shares decisions across both pages.
  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    observer.unobserve(entry.target);
    load(entry.target, true).catch(() => {});
  }));
  document.querySelectorAll('[data-review-source]').forEach(button => observer.observe(button));
}());
