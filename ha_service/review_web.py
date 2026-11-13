"""Shared Review & teach button/dialog for any managed full-frame photo."""
from html import escape


def button(source, region='whole'):
    return ('<button type="button" class="review-button" data-review-source="{}" '
            'data-review-region="{}" title="Review &amp; teach" aria-label="Review &amp; teach">'
            '✎</button>').format(escape(source, quote=True), escape(region, quote=True))


def dialog(base=''):
    return '''<dialog id="review-dialog" data-api="{0}review">
<div class="review-heading"><h2>Review &amp; teach</h2>
<button type="button" id="review-close" aria-label="Close review">×</button></div>
<div class="frame review-preview"><img id="review-image" alt="Frame being reviewed">
<span id="review-zone" class="zone" hidden></span></div>
<p class="muted" id="review-filename"></p>
<div class="review-fields"><label>Region<select id="review-region"></select></label>
<label>Mark as<select id="review-label"></select></label></div>
<label id="review-new-field" hidden>New hen name
<input id="review-new" maxlength="48" placeholder="e.g. hen_c" autocomplete="off">
<small>Lowercase letters, digits, hyphens or underscores.</small></label>
<label><input type="checkbox" id="review-reference"> Add as Reference</label>
<p id="review-message" role="status" aria-live="polite"></p>
<div class="review-actions"><button type="button" id="review-undo" hidden>Undo review</button>
<button type="button" id="review-save">Confirm</button></div>
</dialog>'''.format(escape(base, quote=True))


CSS = '''
.review-photo { position: relative; }
.review-button { position: absolute; top: 8px; right: 8px; min-width: 36px; min-height: 36px;
 border: 1px solid var(--ink); border-radius: 50%; background: var(--shell);
 color: inherit; cursor: pointer; }
.review-button[data-reviewed] { background: var(--success); }
#review-dialog { width: min(640px,90vw); max-height: 90vh; box-sizing: border-box; overflow: auto;
 border: 1px solid var(--ink); border-radius: 18px; padding: 20px;
 background: var(--shell); color: var(--ink); }
#review-dialog::backdrop { background: #0009; }
.review-heading,.review-fields,.review-actions { display: flex; gap: 16px; justify-content: space-between; }
.review-heading { align-items: center; margin-bottom: 12px; }
.review-fields label { flex: 1; min-width: 0; }
#review-dialog select, #review-new { display: block; width: 100%; box-sizing: border-box;
 margin: 6px 0 12px; padding: 10px; font: inherit; }
#review-dialog button { padding: 10px 16px; font: inherit; cursor: pointer; }
.review-actions { margin-top: 16px; }
.review-preview img { max-height: 42vh; object-fit: contain; }
.review-preview { width: fit-content; max-width: 100%; margin: auto; }
.review-preview img { width: auto; max-width: 100%; }
#review-message { min-height: 1.5em; }
#review-feedback { position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%);
 z-index: 5; max-width: 80vw; padding: 12px 20px; border-radius: 12px;
 border: 1px solid var(--ink); background: var(--success); color: var(--ink); }
'''
