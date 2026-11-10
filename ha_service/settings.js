'use strict';

document.addEventListener('DOMContentLoaded', () => {
  const button = document.getElementById('reset-state');
  if (!button) return;
  const message = document.getElementById('reset-message');
  button.addEventListener('click', async () => {
    if (!window.confirm('Reset live tracking and today’s totals to zero? References, photos and journal will be kept.')) return;
    button.disabled = true;
    message.textContent = 'Resetting…';
    try {
      const response = await fetch(button.dataset.endpoint, {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-Munai-Settings': button.dataset.token},
        body: JSON.stringify({confirm: true, date: button.dataset.date})
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Reset failed. Refresh Settings and try again.');
      document.querySelectorAll('.states dd').forEach(value => { value.textContent = '0'; });
      message.textContent = result.mqtt_pending ? 'State reset. MQTT update pending.' : 'State reset.';
    } catch (error) {
      message.textContent = error.message || 'Reset failed. Refresh Settings and try again.';
      button.disabled = false;
    }
  });
});
