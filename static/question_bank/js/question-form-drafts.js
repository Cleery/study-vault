const DATABASE_NAME = 'math-question-bank';
const STORE_NAME = 'drafts';
const EDITOR_KEY = 'math-question-bank:editor-id';
const PENDING_KEY = 'math-question-bank:pending-submit';
const REVISION_PREFIX = 'math-question-bank:revision:';
const LEASE_PREFIX = 'math-question-bank:editor-lease:';
const LEASE_MS = 12000;
const FIELDS = [
  'subject', 'section', 'title', 'statement', 'personal_solution',
  'reference_solution', 'error_note', 'mastery', 'draft', 'tags', 'knowledge_cards'
];

const identifier = () => crypto.randomUUID();

export function serverBaseline(root) {
  const attachments = [...root.querySelectorAll('[data-attachment-id]')].map(row => ({
    id: row.dataset.attachmentId,
    type: row.dataset.fileKind,
    sort_order: Number(row.dataset.attachmentSortOrder),
    filename: row.dataset.attachmentFilename.split(/[\\/]/).pop(),
    updated_at: row.dataset.attachmentUpdatedAt
  }));
  attachments.sort((first, second) => first.sort_order - second.sort_order || first.id.localeCompare(second.id));
  return JSON.stringify({question_updated_at: root.dataset.questionUpdatedAt || '', attachments});
}

export function createDraftRepository() {
  let opening;

  function database() {
    if (!opening) {
      opening = new Promise((resolve, reject) => {
        const request = indexedDB.open(DATABASE_NAME, 1);
        request.onupgradeneeded = () => {
          const db = request.result;
          const store = db.objectStoreNames.contains(STORE_NAME)
            ? request.transaction.objectStore(STORE_NAME)
            : db.createObjectStore(STORE_NAME, {keyPath: 'key'});
          if (!store.indexNames.contains('target_key')) store.createIndex('target_key', 'target_key');
          if (!store.indexNames.contains('updated_at')) store.createIndex('updated_at', 'updated_at');
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
        request.onblocked = () => reject(new Error('Draft database is blocked'));
      }).catch(error => {
        opening = null;
        throw error;
      });
    }
    return opening;
  }

  async function transact(mode, operation) {
    const db = await database();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(STORE_NAME, mode);
      let result;
      transaction.oncomplete = () => resolve(result);
      transaction.onabort = () => reject(transaction.error || new Error('Draft transaction aborted'));
      transaction.onerror = () => reject(transaction.error || new Error('Draft transaction failed'));
      try {
        const request = operation(transaction.objectStore(STORE_NAME));
        request.onsuccess = () => { result = request.result; };
      } catch (error) {
        transaction.abort();
        reject(error);
      }
    });
  }

  return {
    list: targetKey => transact('readonly', store => store.index('target_key').getAll(targetKey)),
    get: key => transact('readonly', store => store.get(key)),
    put: draft => transact('readwrite', store => store.put(draft)),
    delete: key => transact('readwrite', store => store.delete(key))
  };
}

function storageValue(storage, key) {
  try { return storage.getItem(key); } catch { return null; }
}

function setStorage(storage, key, value) {
  try { storage.setItem(key, value); return true; } catch { return false; }
}

function removeStorage(storage, key) {
  try { storage.removeItem(key); } catch { /* Storage may be unavailable. */ }
}

async function claimEditor(repository, targetKey) {
  const owner = identifier();
  const storedId = storageValue(sessionStorage, EDITOR_KEY);
  let editorId = storedId || identifier();
  let copying = null;
  let pendingClaimResolve = null;
  let announced = false;
  let leaseAvailable = true;
  let channel;
  try { channel = new BroadcastChannel('math-question-bank:draft-editors'); } catch { /* Lease remains available. */ }

  function leaseKey(id = editorId) { return `${LEASE_PREFIX}${id}`; }
  function cleanExpiredLeases() {
    try {
      for (let index = localStorage.length - 1; index >= 0; index--) {
        const key = localStorage.key(index);
        if (!key?.startsWith(LEASE_PREFIX)) continue;
        let lease;
        try { lease = JSON.parse(localStorage.getItem(key)); } catch { /* Expired malformed lease. */ }
        if (!lease || lease.expires <= Date.now()) localStorage.removeItem(key);
      }
    } catch { /* Private browsing may deny local storage. */ }
  }
  function readLease(id = editorId) {
    try {
      return JSON.parse(localStorage.getItem(leaseKey(id)) || 'null');
    } catch { leaseAvailable = false; return null; }
  }
  function hasOtherLease() {
    const lease = readLease();
    return Boolean(lease && lease.owner !== owner && lease.expires > Date.now());
  }
  function acquireLease() {
    const existing = readLease();
    if (!leaseAvailable) return false;
    if (existing && existing.owner !== owner && existing.expires > Date.now()) return false;
    if (!setStorage(localStorage, leaseKey(), JSON.stringify({owner, expires: Date.now() + LEASE_MS}))) {
      leaseAvailable = false;
      return false;
    }
    return readLease()?.owner === owner;
  }
  function releaseLease(id = editorId) {
    try {
      if (readLease(id)?.owner === owner) localStorage.removeItem(leaseKey(id));
    } catch { /* Storage may be unavailable. */ }
  }
  function release() {
    releaseLease();
    channel?.close();
  }
  function copyOnCollision() {
    if (copying) return copying;
    copying = (async () => {
      const oldId = editorId;
      editorId = identifier();
      setStorage(sessionStorage, EDITOR_KEY, editorId);
      try {
        const previous = await repository.get(`${targetKey}:${oldId}`);
        if (previous) await repository.put({
          ...previous, key: `${targetKey}:${editorId}`, revision: 1,
          updated_at: new Date().toISOString()
        });
      } catch { /* The original draft remains available for manual recovery. */ }
      releaseLease(oldId);
      if (leaseAvailable) acquireLease();
      if (announced) channel?.postMessage({type: 'claim', editorId, owner});
    })();
    copying.finally(() => { copying = null; });
    return copying;
  }

  if (channel) {
    channel.onmessage = event => {
      const message = event.data;
      if (!message || message.owner === owner || message.editorId !== editorId) return;
      if (message.type === 'claim') channel.postMessage({type: 'occupied', editorId, owner});
      if (message.type === 'occupied') {
        if (pendingClaimResolve) pendingClaimResolve(true);
        else copyOnCollision();
      }
    };
  }
  cleanExpiredLeases();
  acquireLease();
  setStorage(sessionStorage, EDITOR_KEY, editorId);
  if (hasOtherLease()) await copyOnCollision();
  if (channel) {
    const collided = await new Promise(resolve => {
      pendingClaimResolve = resolve;
      announced = true;
      channel.postMessage({type: 'claim', editorId, owner});
      window.setTimeout(() => resolve(false), 120);
    });
    pendingClaimResolve = null;
    if (collided) await copyOnCollision();
  }
  if (hasOtherLease()) await copyOnCollision();
  const renewal = window.setInterval(() => {
    if (hasOtherLease()) copyOnCollision();
    else if (leaseAvailable) acquireLease();
  }, LEASE_MS / 3);
  window.addEventListener('pagehide', () => {
    window.clearInterval(renewal);
    release();
  }, {once: true});
  return {
    async getKey() {
      if (copying) await copying;
      if (hasOtherLease()) await copyOnCollision();
      return `${targetKey}:${editorId}`;
    },
    async startBlank() {
      if (copying) await copying;
      releaseLease();
      editorId = identifier();
      setStorage(sessionStorage, EDITOR_KEY, editorId);
      if (leaseAvailable) acquireLease();
      channel?.postMessage({type: 'claim', editorId, owner});
      return `${targetKey}:${editorId}`;
    }
  };
}

function captureFields(root) {
  const fields = {};
  for (const name of FIELDS) {
    const control = root.elements.namedItem(name);
    if (!control) continue;
    if (control instanceof HTMLSelectElement && control.multiple) {
      fields[name] = [...control.selectedOptions].map(option => option.value);
    } else if (control instanceof HTMLInputElement && control.type === 'checkbox') {
      fields[name] = control.checked;
    } else {
      fields[name] = control.value;
    }
  }
  return fields;
}

function restoreFields(root, fields, editor) {
  for (const name of FIELDS) {
    const control = root.elements.namedItem(name);
    if (!control || !Object.hasOwn(fields, name)) continue;
    const value = fields[name];
    if (control instanceof HTMLSelectElement && control.multiple) {
      const selected = new Set(value);
      [...control.options].forEach(option => { option.selected = selected.has(option.value); });
    } else if (control instanceof HTMLInputElement && control.type === 'checkbox') {
      control.checked = Boolean(value);
    } else {
      control.value = value;
    }
    if (name === 'subject') editor.refreshSections();
    control.dispatchEvent(new Event('input', {bubbles: true}));
  }
  editor.refreshSections();
}

function captureAttachments(root, attachments) {
  const items = attachments.getItems();
  return {
    files: items.filter(item => item.kind === 'new').map(item => item.file),
    order: [...root.querySelectorAll('input[name="attachment_order"]')].map(field => field.value),
    removed: [...root.querySelectorAll('input[name="removed_attachment"]')].map(field => field.value)
  };
}

function restoreAttachments(root, attachments, draft, baselineMatches) {
  const state = draft.attachments;
  attachments.restoreState({
    files: state.files,
    removed: baselineMatches ? state.removed : [],
    order: baselineMatches ? state.order : []
  });
}

function validateDraft(draft) {
  if (!draft || typeof draft !== 'object' || Array.isArray(draft)) throw new Error('Invalid draft');
  if (!draft.fields || typeof draft.fields !== 'object' || Array.isArray(draft.fields)) throw new Error('Invalid draft fields');
  const attachments = draft.attachments;
  if (!attachments || typeof attachments !== 'object' || Array.isArray(attachments)) throw new Error('Invalid draft attachments');
  if (!Array.isArray(attachments.files) || !attachments.files.every(file => file instanceof Blob)) throw new Error('Invalid draft files');
  if (!Array.isArray(attachments.removed) || !attachments.removed.every(id => typeof id === 'string')) throw new Error('Invalid draft removed state');
  if (!Array.isArray(attachments.order) || !attachments.order.every(token => typeof token === 'string')) throw new Error('Invalid draft order');
  return draft;
}

export async function initializeDrafts(root, editor, attachments, baseline) {
  const repository = createDraftRepository();
  const targetKey = root.dataset.questionId ? `question:${root.dataset.questionId}` : 'question:create';
  const getKeyPromise = claimEditor(repository, targetKey);
  const recovery = document.querySelector('[data-draft-recovery]');
  const list = recovery?.querySelector('[data-draft-list]');
  const draftStatus = document.querySelector('[data-draft-status]');
  const recoveryStatus = recovery?.querySelector('[data-recovery-status]');
  const saveStatus = root.querySelector('[data-save-status]');
  const hasServerErrors = Boolean(root.querySelector('[data-error-summary]'));
  let restoring = false;
  let saveTimer = null;
  let saveChain = Promise.resolve();
  let lastRevision = 0;
  let changeSerial = 0;
  let savedSerial = 0;
  let submitting = false;
  let nativeSubmission = false;
  let switchingBlank = false;

  function setSubmitting(value) {
    submitting = value;
    root.querySelectorAll('button[name="save_intent"]').forEach(button => {
      button.disabled = value;
    });
    if (!value) root.querySelector('[data-submission-intent]')?.remove();
  }

  function preserveSubmitIntent(submitter) {
    const selected = submitter?.name === 'save_intent'
      ? submitter
      : root.querySelector('button[name="save_intent"]:not([disabled])');
    if (!selected) return;
    let field = root.querySelector('[data-submission-intent]');
    if (!field) {
      field = document.createElement('input');
      field.type = 'hidden';
      field.name = 'save_intent';
      field.dataset.submissionIntent = '';
      root.append(field);
    }
    field.value = selected.value;
  }

  function reportFailure() {
    draftStatus.textContent = '无法确保本地草稿可恢复。';
    saveStatus.textContent = '本地草稿保存或提交记录失败。服务器校验失败后可能需要重新选择新图片。';
  }
  function capture() {
    return {
      target_key: targetKey,
      updated_at: new Date().toISOString(), form_version: root.dataset.formVersion,
      baseline,
      fields: captureFields(root),
      ui: {mode: root.dataset.mode, tab: root.querySelector('[role="tab"][aria-selected="true"]')?.dataset.tab || 'question'},
      attachments: captureAttachments(root, attachments)
    };
  }
  function save() {
    const draft = capture();
    const serial = changeSerial;
    const operation = saveChain.catch(() => {}).then(async () => {
      const identity = await getKeyPromise;
      draft.key = await identity.getKey();
      const previous = await repository.get(draft.key);
      const previousRevision = Number.isSafeInteger(previous?.revision) ? previous.revision : 0;
      const storedRevision = Number(storageValue(sessionStorage, `${REVISION_PREFIX}${draft.key}`));
      draft.revision = Math.max(
        lastRevision, previousRevision, Number.isSafeInteger(storedRevision) ? storedRevision : 0
      ) + 1;
      await repository.put(draft);
      lastRevision = draft.revision;
      setStorage(sessionStorage, `${REVISION_PREFIX}${draft.key}`, String(draft.revision));
      savedSerial = Math.max(savedSerial, serial);
      return draft;
    });
    saveChain = operation;
    return operation.then(savedDraft => {
      draftStatus.textContent = '本地草稿已保存';
      showDrafts().catch(reportFailure);
      return savedDraft;
    });
  }
  function scheduleSave() {
    if (restoring || submitting) return;
    changeSerial++;
    clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      saveTimer = null;
      save().catch(reportFailure);
    }, 700);
  }
  async function restore(draft) {
    validateDraft(draft);
    restoring = true;
    try {
      if (!hasServerErrors) restoreFields(root, draft.fields || {}, editor);
      editor.setMode(draft.ui?.mode || 'quick');
      editor.activateTab(draft.ui?.tab || 'question');
      const compatible = draft.form_version === root.dataset.formVersion && draft.baseline === baseline;
      restoreAttachments(root, attachments, draft, compatible);
      recoveryStatus.textContent = compatible ? '已恢复本地草稿。' : '已恢复内容。草稿格式或服务器版本已变更，请重新检查附件顺序和删除状态。';
    } finally { restoring = false; }
  }
  async function showDrafts() {
    if (!list) return;
    const drafts = await repository.list(targetKey);
    drafts.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    list.replaceChildren();
    recovery.hidden = !drafts.length;
    for (const draft of drafts) {
      const row = document.createElement('div');
      row.setAttribute('role', 'listitem');
      const label = document.createElement('span');
      label.textContent = `${draft.fields?.title || '未命名草稿'} ${new Date(draft.updated_at).toLocaleString()}`;
      const restoreButton = document.createElement('button');
      restoreButton.type = 'button';
      restoreButton.textContent = '恢复草稿';
      restoreButton.addEventListener('click', () => restore(draft).catch(reportFailure));
      const deleteButton = document.createElement('button');
      deleteButton.type = 'button';
      deleteButton.textContent = '删除草稿';
      deleteButton.addEventListener('click', async () => {
        try { await repository.delete(draft.key); await showDrafts(); }
        catch { reportFailure(); }
      });
      row.append(label, restoreButton, deleteButton);
      list.append(row);
    }
  }

  root.addEventListener('input', scheduleSave);
  root.addEventListener('change', scheduleSave);
  root.querySelector('[data-attachment-queue]')?.addEventListener('click', scheduleSave);
  root.querySelector('[data-attachment-queue]')?.addEventListener('drop', scheduleSave);
  root.querySelector('[data-attachment-queue]')?.addEventListener('keydown', scheduleSave);
  root.querySelector('[data-attachment-dropzone]')?.addEventListener('drop', scheduleSave);
  root.querySelector('[data-solution-image-input]')?.addEventListener('change', scheduleSave);
  root.querySelector('[data-solution-preview]')?.addEventListener('click', scheduleSave);
  root.addEventListener('paste', scheduleSave);
  document.querySelectorAll('[data-form-mode], [data-form-tabs] [role="tab"]').forEach(button => button.addEventListener('click', scheduleSave));
  root.querySelector('[data-form-tabs]')?.addEventListener('keydown', scheduleSave);
  document.querySelector('[data-preserve-local-draft]')?.addEventListener('click', () => {
    clearTimeout(saveTimer);
    save().then(() => { saveStatus.textContent = '当前内容已保存为本地草稿。'; }).catch(reportFailure);
  });
  root.querySelector('[data-cancel-edit]')?.addEventListener('click', async event => {
    if (changeSerial <= savedSerial) return;
    event.preventDefault();
    clearTimeout(saveTimer);
    const destination = event.currentTarget.href;
    try {
      await save();
      location.assign(destination);
    } catch {
      reportFailure();
      if (window.confirm('本地草稿保存失败，取消后可能需要重新选择新图片。仍要离开吗？')) {
        location.assign(destination);
      }
    }
  });
  recovery?.querySelector('[data-start-blank-draft]')?.addEventListener('click', async () => {
    if (switchingBlank) return;
    switchingBlank = true;
    const mustSaveOldDraft = changeSerial > savedSerial || saveTimer !== null;
    clearTimeout(saveTimer);
    saveTimer = null;
    restoring = true;
    root.inert = true;
    try {
      if (mustSaveOldDraft) await save();
      else await saveChain;
      const identity = await getKeyPromise;
      await identity.startBlank();
      lastRevision = 0;
      const queue = root.querySelector('[data-attachment-queue]');
      [...queue.querySelectorAll('[data-attachment-token^="new:"]')].forEach(
        row => row.querySelector('[data-attachment-remove]')?.click()
      );
      root.reset();
      editor.setMode('quick');
      editor.activateTab('question');
      editor.refreshSections();
      root.querySelector('#id_statement')?.dispatchEvent(new Event('input', {bubbles: true}));
      recoveryStatus.textContent = '已开始空白草稿。';
      await save();
    } catch {
      reportFailure();
    } finally {
      restoring = false;
      root.inert = false;
      switchingBlank = false;
    }
  });
  root.addEventListener('submit', async event => {
    if (nativeSubmission) return;
    if (submitting) {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    preserveSubmitIntent(event.submitter);
    setSubmitting(true);
    clearTimeout(saveTimer);
    const submitter = event.submitter;
    try {
      const draft = await save();
      if (!setStorage(sessionStorage, PENDING_KEY, JSON.stringify({key: draft.key, revision: draft.revision}))) {
        throw new Error('Pending draft marker unavailable');
      }
    } catch {
      removeStorage(sessionStorage, PENDING_KEY);
      reportFailure();
      if (!window.confirm('本地草稿无法确保恢复。若服务器校验失败，新图片可能需要重新选择。仍要提交吗？')) {
        setSubmitting(false);
        return;
      }
    }
    nativeSubmission = true;
    root.requestSubmit(submitter || undefined);
  });
  await getKeyPromise;
  const url = new URL(location.href);
  if (url.searchParams.get('saved') === '1') {
    const pending = storageValue(sessionStorage, PENDING_KEY);
    try {
      const saved = pending && JSON.parse(pending);
      if (saved?.key && Number.isSafeInteger(saved?.revision) && saved.revision > 0) {
        const draft = await repository.get(saved.key);
        if (draft?.revision === saved.revision) await repository.delete(saved.key);
      }
    } catch { reportFailure(); }
    removeStorage(sessionStorage, PENDING_KEY);
    url.searchParams.delete('saved');
    history.replaceState(history.state, '', `${url.pathname}${url.search}${url.hash}`);
  }
  try {
    await showDrafts();
    if (hasServerErrors) {
      const pending = JSON.parse(storageValue(sessionStorage, PENDING_KEY) || 'null');
      const draft = pending?.key && await repository.get(pending.key);
      if (draft?.revision === pending.revision) await restore(draft);
      removeStorage(sessionStorage, PENDING_KEY);
    }
  } catch { reportFailure(); }
  return {save, showDrafts};
}
