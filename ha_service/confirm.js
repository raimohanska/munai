/* Confirm the signed, displayed snapshot, never the newest prediction. */
(function () {
  'use strict';
  const script = document.querySelector('script[data-confirm-api]');
  if (!script) return;
  let pending = 0;
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-confirm]');
    if (!button || button.disabled) return;
    const message = button.nextElementSibling;
    button.disabled = true;
    button.textContent = 'Saving…';
    message.textContent = '';
    pending += 1;
    document.documentElement.dataset.confirmBusy = 'true';
    try {
      const response = await fetch(script.dataset.confirmApi, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: button.dataset.confirm
      });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || 'Confirmation failed. Try again.');
      button.textContent = 'Confirmed';
    } catch (error) {
      button.disabled = false;
      button.textContent = 'Confirm';
      message.textContent = error.message;
    } finally {
      pending -= 1;
      if (!pending) delete document.documentElement.dataset.confirmBusy;
    }
  });
}());
