export function initializeAttachmentQueue(root) {
  const input = root.querySelector('[data-image-input]');
  const queue = root.querySelector('[data-attachment-queue]');
  const dropzone = root.querySelector('[data-attachment-dropzone]');
  const protocol = root.querySelector('[data-attachment-protocol]');
  const counter = root.querySelector('[data-attachment-count]');
  const dialog = document.querySelector('[data-image-dialog]');
  const dialogImage = dialog?.querySelector('[data-image-dialog-image]');
  if (!input || !queue || !protocol || typeof DataTransfer === 'undefined') return null;

  const items = [...queue.querySelectorAll(':scope > [data-attachment-id]')].map(row => ({
    kind: 'existing',
    id: row.dataset.attachmentId,
    row,
    removed: Boolean(row.querySelector('input[name="remove_attachment"]:checked'))
  }));
  let draggedItem = null;

  function button(label, attribute, value) {
    const element = document.createElement('button');
    element.type = 'button';
    element.setAttribute(attribute, value);
    element.setAttribute('aria-label', label);
    element.title = label;
    element.textContent = value === 'up' ? '↑' : value === 'down' ? '↓' : '删除';
    return element;
  }

  function newRow(file) {
    const row = document.createElement('li');
    row.className = 'attachment-row';
    const previewable = ['image/png', 'image/jpeg', 'image/webp'].includes(file.type);
    row.dataset.fileKind = previewable ? 'image' : 'other';
    let objectUrl = null;
    if (previewable) {
      objectUrl = URL.createObjectURL(file);
      const media = document.createElement('div');
      media.className = 'attachment-row__media';
      const image = document.createElement('img');
      image.src = objectUrl;
      image.alt = file.name;
      const zoom = button(`放大查看 ${file.name}`, 'data-image-zoom', 'true');
      zoom.textContent = '放大';
      zoom.className = 'attachment-row__zoom';
      zoom.dataset.imageSrc = objectUrl;
      media.append(image, zoom);
      row.append(media);
    } else {
      const icon = document.createElement('span');
      icon.className = 'attachment-row__file-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = '◇';
      row.append(icon);
    }
    const details = document.createElement('div');
    details.className = 'attachment-row__details';
    const name = document.createElement('span');
    name.className = 'attachment-row__name';
    name.textContent = file.name;
    const kind = document.createElement('span');
    kind.className = 'attachment-row__kind';
    kind.textContent = previewable ? '图片' : '文件';
    const controls = document.createElement('div');
    controls.className = 'attachment-row__controls';
    const handle = button(`拖动排序 ${file.name}`, 'data-attachment-drag-handle', 'true');
    handle.textContent = '⋮⋮';
    handle.draggable = true;
    controls.append(
      handle,
      button(`上移 ${file.name}`, 'data-attachment-move', 'up'),
      button(`下移 ${file.name}`, 'data-attachment-move', 'down'),
      button(`删除 ${file.name}`, 'data-attachment-remove', 'true')
    );
    details.append(name, kind, controls);
    row.append(details);
    return {kind: 'new', file, row, objectUrl, removed: false};
  }

  function hidden(name, value) {
    const field = document.createElement('input');
    field.type = 'hidden';
    field.name = name;
    field.value = value;
    field.dataset.attachmentGenerated = '';
    root.append(field);
  }

  function sync() {
    root.querySelectorAll('[data-attachment-generated]').forEach(field => field.remove());
    const transfer = new DataTransfer();
    let uploadIndex = 0;
    items.forEach(item => {
      if (item.removed) {
        if (item.kind === 'existing') hidden('removed_attachment', item.id);
        item.row.remove();
        return;
      }
      if (item.kind === 'new') {
        transfer.items.add(item.file);
        item.row.dataset.attachmentToken = `new:${uploadIndex++}`;
      } else {
        item.row.dataset.attachmentToken = `existing:${item.id}`;
      }
      hidden('attachment_order', item.row.dataset.attachmentToken);
      queue.append(item.row);
    });
    input.files = transfer.files;
    counter.textContent = `${items.filter(item => !item.removed).length} 个附件`;
  }

  function addFiles(files) {
    [...files].forEach(file => items.push(newRow(file)));
    sync();
  }

  function move(item, direction) {
    const active = items.filter(candidate => !candidate.removed);
    const neighbor = active[active.indexOf(item) + direction];
    if (!neighbor) return;
    const current = items.indexOf(item);
    const next = items.indexOf(neighbor);
    [items[current], items[next]] = [items[next], items[current]];
    sync();
    item.row.querySelector(`[data-attachment-move="${direction < 0 ? 'up' : 'down'}"]`)?.focus();
  }

  queue.addEventListener('click', event => {
    const row = event.target.closest('.attachment-row');
    const item = items.find(candidate => candidate.row === row);
    if (!item) return;
    const moveButton = event.target.closest('[data-attachment-move]');
    if (moveButton) move(item, moveButton.dataset.attachmentMove === 'up' ? -1 : 1);
    if (event.target.closest('[data-attachment-remove]')) {
      const active = items.filter(candidate => !candidate.removed);
      const index = active.indexOf(item);
      const neighbor = active[index + 1] || active[index - 1];
      if (item.kind === 'new') items.splice(items.indexOf(item), 1);
      else item.removed = true;
      if (item.objectUrl) URL.revokeObjectURL(item.objectUrl);
      sync();
      if (neighbor) neighbor.row.querySelector('[data-attachment-remove]')?.focus();
      else input.focus();
    }
    const zoom = event.target.closest('[data-image-zoom]');
    if (zoom && dialog?.showModal) {
      dialogImage.src = zoom.dataset.imageSrc;
      dialog.showModal();
    }
  });

  queue.addEventListener('keydown', event => {
    if (!event.target.matches('[data-attachment-drag-handle]')) return;
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    const item = items.find(candidate => candidate.row === event.target.closest('.attachment-row'));
    if (item) {
      move(item, event.key === 'ArrowUp' ? -1 : 1);
      item.row.querySelector('[data-attachment-drag-handle]')?.focus();
    }
  });

  queue.addEventListener('dragstart', event => {
    if (!event.target.matches('[data-attachment-drag-handle]')) return;
    draggedItem = items.find(item => item.row === event.target.closest('.attachment-row'));
    if (draggedItem) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', draggedItem.row.dataset.attachmentToken);
    }
  });
  queue.addEventListener('dragover', event => {
    if (draggedItem && event.target.closest('.attachment-row')) event.preventDefault();
  });
  queue.addEventListener('drop', event => {
    const target = items.find(item => item.row === event.target.closest('.attachment-row'));
    if (!draggedItem || !target || target === draggedItem) return;
    event.preventDefault();
    const targetIndex = items.indexOf(target);
    items.splice(items.indexOf(draggedItem), 1);
    items.splice(targetIndex, 0, draggedItem);
    sync();
    draggedItem = null;
  });
  queue.addEventListener('dragend', () => { draggedItem = null; });

  input.addEventListener('change', () => {
    const selected = [...input.files];
    addFiles(selected);
  });
  dropzone?.addEventListener('dragover', event => {
    event.preventDefault();
    dropzone.classList.add('is-dragover');
  });
  dropzone?.addEventListener('dragleave', () => dropzone.classList.remove('is-dragover'));
  dropzone?.addEventListener('drop', event => {
    event.preventDefault();
    dropzone.classList.remove('is-dragover');
    addFiles(event.dataTransfer.files);
  });
  root.addEventListener('paste', event => {
    const files = [...(event.clipboardData?.files || [])];
    if (!files.length) return;
    event.preventDefault();
    addFiles(files);
  });

  dialog?.querySelector('[data-image-dialog-close]')?.addEventListener('click', () => dialog.close());
  dialog?.addEventListener('close', () => dialogImage.removeAttribute('src'));
  root.querySelectorAll('[data-ocr]').forEach(control => { control.disabled = true; });
  items.forEach(item => {
    const handle = item.row.querySelector('[data-attachment-drag-handle]');
    if (handle) handle.draggable = true;
    item.row.querySelectorAll('input[name="remove_attachment"], input[name^="attachment_position_"]').forEach(field => {
      field.disabled = true;
      field.closest('label').hidden = true;
    });
    item.row.querySelector('.attachment-row__controls')?.append(button(`删除 ${item.row.querySelector('.attachment-row__name')?.textContent || '附件'}`, 'data-attachment-remove', 'true'));
  });
  protocol.disabled = false;
  sync();
  function restoreState(state) {
    const existing = items.filter(item => item.kind === 'existing');
    items.filter(item => item.kind === 'new').forEach(item => {
      if (item.objectUrl) URL.revokeObjectURL(item.objectUrl);
    });
    const newItems = state.files.map(file => newRow(file));
    const removed = new Set(state.removed);
    existing.forEach(item => { item.removed = removed.has(item.id); });
    const byToken = new Map(existing.map(item => [`existing:${item.id}`, item]));
    newItems.forEach((item, index) => byToken.set(`new:${index}`, item));
    const ordered = state.order.map(token => byToken.get(token)).filter(Boolean);
    const included = new Set(ordered);
    items.splice(0, items.length, ...ordered, ...[...existing, ...newItems].filter(item => !included.has(item)));
    sync();
  }

  return {
    addFiles,
    getItems: () => items.filter(item => !item.removed).map(item => ({kind: item.kind, id: item.id, file: item.file})),
    restoreState
  };
}

export function initializeSolutionPreview(root) {
  const input = root.querySelector('[data-solution-image-input]');
  const preview = root.querySelector('[data-solution-preview][data-solution-new-preview]');
  if (!input || !preview || typeof DataTransfer === 'undefined') return null;
  let files = [];

  function sync() {
    const transfer = new DataTransfer();
    files.forEach(file => transfer.items.add(file));
    input.files = transfer.files;
    preview.replaceChildren();
    files.forEach((file, index) => {
      const row = document.createElement('li');
      row.className = 'attachment-row';
      const media = document.createElement('div');
      media.className = 'attachment-row__media';
      const image = document.createElement('img');
      image.src = URL.createObjectURL(file);
      image.alt = `待上传解答图 ${index + 1}`;
      image.loading = 'lazy';
      media.append(image);
      const details = document.createElement('div');
      details.className = 'attachment-row__details';
      const name = document.createElement('span');
      name.className = 'attachment-row__name';
      name.textContent = file.name;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'attachment-row__remove-button';
      remove.dataset.solutionRemove = String(index);
      remove.textContent = '移除';
      remove.setAttribute('aria-label', `移除 ${file.name}`);
      details.append(name, remove);
      row.append(media, details);
      preview.append(row);
    });
  }

  input.addEventListener('change', () => {
    files = [...files, ...input.files];
    sync();
  });
  preview.addEventListener('click', event => {
    const button = event.target.closest('[data-solution-remove]');
    if (!button) return;
    files.splice(Number(button.dataset.solutionRemove), 1);
    sync();
  });
  return {getFiles: () => [...files]};
}
