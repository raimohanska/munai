(() => {
  const script = document.currentScript;
  document.querySelectorAll('.reference-remove').forEach(button => {
    button.addEventListener('click', async () => {
      if (!window.confirm('Remove reference ' + button.dataset.path + '? This cannot be undone.')) return;
      button.disabled = true;
      try {
        const response = await fetch(script.dataset.api, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-Munai-Review': script.dataset.token},
          body: JSON.stringify({source: button.dataset.path, references: script.dataset.revision})
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Reference could not be removed.');
        location.reload();
      } catch (error) {
        window.alert(error.message);
        button.disabled = false;
      }
    });
  });
})();
