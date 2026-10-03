(function () {
  const root = document.querySelector('[data-knowledge-card-form]');
  if (!root) return;
  const editor = root.querySelector('[data-kc-editor]');
  const rendered = root.querySelector('[data-kc-rendered]');
  const status = root.querySelector('[data-kc-status]');
  if (!editor || !rendered) return;

  let timer;
  let controller;
  let sequence = 0;
  const render = async (source, currentSequence) => {
    controller = new AbortController();
    const csrf = root.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
    try {
      const response = await fetch(root.dataset.previewUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrf},
        body: new URLSearchParams({source}),
        signal: controller.signal
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (currentSequence !== sequence) return;
      rendered.innerHTML = payload.html;
      if (status) status.textContent = '已更新预览';
      if (window.MathJax?.typesetPromise) window.MathJax.typesetPromise([rendered]).catch(() => {});
    } catch (error) {
      if (error.name !== 'AbortError' && currentSequence === sequence && status) {
        status.textContent = '预览失败，当前内容仍可编辑。';
      }
    }
  };
  const scheduleRender = () => {
    clearTimeout(timer);
    controller?.abort();
    const currentSequence = ++sequence;
    const source = editor.value;
    if (!source.trim()) {
      rendered.textContent = '开始输入内容';
      if (status) status.textContent = '';
      return;
    }
    timer = setTimeout(() => render(source, currentSequence), 250);
  };
  const insert = (kind) => {
    const snippets = { heading: '## 新标题\n\n', strong: '**重点：** ', formula: '\n$$\\frac{f(b)-f(a)}{b-a}$$\n', list: '\n- ' };
    const text = snippets[kind];
    const start = editor.selectionStart;
    editor.value = editor.value.slice(0, start) + text + editor.value.slice(editor.selectionEnd);
    editor.focus();
    editor.selectionStart = editor.selectionEnd = start + text.length;
    scheduleRender();
  };
  root.querySelectorAll('[data-kc-insert]').forEach((button) => button.addEventListener('click', () => insert(button.dataset.kcInsert)));
  editor.addEventListener('input', scheduleRender);
  scheduleRender();
}());
