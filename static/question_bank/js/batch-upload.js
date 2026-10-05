(() => {
  const root = document.querySelector('[data-batch-upload]');
  if (!root) return;
  const form = root.querySelector('[data-native-upload]');
  const input = root.querySelector('[data-image-queue]');
  const queue = root.querySelector('[data-queue]');
  const submit = root.querySelector('[data-upload-all]');
  const savedLink = root.querySelector('[data-saved-link]');
  const items = [];
  const selections = [];
  const maxSize = 10 * 1024 * 1024;
  input.multiple = true;
  input.removeAttribute('name');

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (window.crypto && crypto.getRandomValues) crypto.getRandomValues(bytes);
    else for (let index = 0; index < 16; index++) bytes[index] = Math.floor(Math.random() * 256);
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  async function sameContent(first, second) {
    if (first.size !== second.size) return false;
    const [left, right] = await Promise.all([first.arrayBuffer(), second.arrayBuffer()]);
    const a = new Uint8Array(left);
    const b = new Uint8Array(right);
    return a.every((value, index) => value === b[index]);
  }

  function render(item) {
    const row = document.createElement('div');
    row.className = 'batch-queue__item';
    const preview = document.createElement('img');
    preview.src = URL.createObjectURL(item.file);
    preview.alt = '';
    const meta = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = item.file.name;
    const size = document.createElement('small');
    size.textContent = `${(item.file.size / 1024 / 1024).toFixed(2)} MB`;
    const status = document.createElement('span');
    status.setAttribute('role', 'status');
    status.textContent = item.error || '待上传';
    const action = document.createElement('button');
    action.type = 'button';
    action.textContent = '移除';
    action.addEventListener('click', () => {
      if (item.state === 'uploading') return;
      if (item.state === 'failed') {
        upload(item);
        return;
      }
      items.splice(items.indexOf(item), 1);
      URL.revokeObjectURL(preview.src);
      row.remove();
    });
    meta.append(name, size, status);
    row.append(preview, meta, action);
    queue.appendChild(row);
    item.row = row;
    item.status = status;
    item.action = action;
  }

  input.addEventListener('change', () => {
    const selected = [...input.files];
    input.value = '';
    const selection = (async () => {
      for (const file of selected) {
        let duplicate = false;
        for (const item of items) {
          if (await sameContent(file, item.file)) {
            duplicate = true;
            break;
          }
        }
        if (duplicate && !window.confirm('同一批次中已有相同图片，仍要保留两份吗？')) continue;
        const item = {file, id: uuid(), state: 'pending'};
        if (file.size > maxSize) item.error = '图片大小不能超过 10 MB。';
        items.push(item);
        render(item);
      }
    })();
    selections.push(selection);
  });

  async function upload(item) {
    item.state = 'uploading';
    item.status.textContent = '上传中';
    const data = new FormData();
    data.append('batch_id', root.dataset.batchId);
    data.append('client_upload_id', item.id);
    data.append('image', item.file);
    try {
      const response = await fetch(root.dataset.uploadUrl, {
        method: 'POST', body: data,
        headers: {'Accept': 'application/json', 'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value},
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(response.status === 413 ? '请求过大，请上传较小图片。' : result.error || '上传失败，请重试。');
      item.state = 'saved';
      item.status.textContent = '已保存';
      item.action.disabled = true;
      savedLink.hidden = false;
    } catch (error) {
      item.state = 'failed';
      item.status.textContent = error.message;
      item.action.textContent = '重试';
    }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    submit.disabled = true;
    await Promise.all(selections);
    if (!items.length) {
      submit.disabled = false;
      return;
    }
    for (const item of items) {
      if (item.state !== 'saved' && !item.error) await upload(item);
    }
    submit.disabled = false;
    if (items.every(item => item.state === 'saved')) window.location.href = root.dataset.detailUrl;
  });
})();
