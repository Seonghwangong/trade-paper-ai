"""Browser draft state machine, supplied with account-scoped storage by ui.py."""
INVOICE_DRAFT_SCRIPT = r"""
function invoiceDraftStore(storage, context, revision) {
  const key = 'trade-paper-ai-invoice-draft-v2:' + encodeURIComponent(context);
  let observed;
  function read() {
    try {
      observed = storage.getItem(key);
      if (!observed) return {status:'empty'};
      let draft;
      try { draft = JSON.parse(observed); } catch (_) { return {status:'invalid'}; }
      if (!draft || draft.version !== 2 || draft.context !== context || !draft.values ||
          typeof draft.values !== 'object' || Array.isArray(draft.values) ||
          !Object.values(draft.values).every(values => Array.isArray(values) && values.every(value => typeof value === 'string' || typeof value === 'boolean'))) {
        return {status:'invalid'};
      }
      return {status:draft.revision === revision ? 'ready' : 'stale', draft};
    } catch (_) { return {status:'unavailable'}; }
  }
  function write(values) {
    try {
      if (storage.getItem(key) !== observed) return 'conflict';
      const next = JSON.stringify({version:2, context, revision, updated:Date.now(), values});
      storage.setItem(key, next);
      observed = next;
      return 'saved';
    } catch (_) { return 'unavailable'; }
  }
  function clear() {
    try {
      if (storage.getItem(key) !== observed) return 'conflict';
      storage.removeItem(key); observed = null;
      return 'cleared';
    } catch (_) { return 'unavailable'; }
  }
  return {read, write, clear};
}
"""
