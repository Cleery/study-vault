const INSERTIONS = {
  bold: ['**', '**', 'text'],
  'inline-math': ['$', '$', 'x'],
  'block-math': ['\n$$\n', '\n$$\n', 'x'],
  sum: ['\\sum_{i=1}^{n} ', '', 'a_i'],
  limit: ['\\lim_{x \\to 0} ', '', 'f(x)'],
  fraction: ['\\frac{', '}{b}', 'a'],
  superscript: ['^{', '}', '2'],
  subscript: ['_{', '}', 'i']
};

export function initializeEditor(root) {
  const modeButtons = [...document.querySelectorAll('[data-form-mode]')];
  const tabs = [...root.querySelectorAll('[data-form-tabs] [role="tab"]')];
  const panels = [...root.querySelectorAll('[data-tab-panel]')];
  const statement = root.querySelector('#id_statement');
  const preview = root.querySelector('[data-markdown-preview]');
  const previewStatus = root.querySelector('[data-preview-status]');
  let activeTab = 'question';
  let previewTimer;
  let previewController;
  let previewSequence = 0;

  function activateTab(name, focus = false) {
    if (!tabs.some(tab => tab.dataset.tab === name)) return;
    activeTab = name;
    tabs.forEach(tab => {
      const selected = tab.dataset.tab === name;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focus) tab.focus();
    });
    panels.forEach(panel => {
      panel.hidden = root.dataset.mode === 'complete' && panel.dataset.tabPanel !== name;
    });
  }

  function setMode(mode) {
    if (mode !== 'quick' && mode !== 'complete') return;
    root.dataset.mode = mode;
    modeButtons.forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.formMode === mode));
    });
    activateTab(activeTab);
  }

  modeButtons.forEach(button => button.addEventListener('click', () => setMode(button.dataset.formMode)));
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activateTab(tab.dataset.tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next === undefined) return;
      event.preventDefault();
      activateTab(tabs[next].dataset.tab, true);
    });
  });

  const firstErrorPanel = panels.find(panel => panel.querySelector('.errorlist'));
  if (firstErrorPanel) {
    if (firstErrorPanel.dataset.tabPanel !== 'question' || firstErrorPanel.querySelector('.complete-only .errorlist')) {
      setMode('complete');
    }
    activateTab(firstErrorPanel.dataset.tabPanel);
    const summary = root.querySelector('[data-error-summary]');
    if (summary) summary.focus();
  } else {
    setMode('quick');
  }

  root.querySelectorAll('[data-error-summary] a[href^="#"]').forEach(link => {
    link.addEventListener('click', event => {
      const field = document.getElementById(link.hash.slice(1));
      if (!field) return;
      const panel = field.closest('[data-tab-panel]');
      if ((panel && panel.dataset.tabPanel !== 'question') || field.closest('.complete-only')) {
        setMode('complete');
      }
      if (panel) activateTab(panel.dataset.tabPanel);
      event.preventDefault();
      field.focus();
      field.scrollIntoView({block: 'center'});
    });
  });

  root.querySelectorAll('[data-markdown-toolbar]').forEach(toolbar => {
    toolbar.addEventListener('click', event => {
      const button = event.target.closest('[data-insert]');
      if (!button || !toolbar.contains(button)) return;
      const field = document.getElementById(toolbar.dataset.editorTarget);
      const insertion = INSERTIONS[button.dataset.insert];
      if (!field || !insertion) return;
      const [before, after, placeholder] = insertion;
      const start = field.selectionStart;
      const end = field.selectionEnd;
      const selected = field.value.slice(start, end) || placeholder;
      field.setRangeText(before + selected + after, start, end, 'select');
      field.setSelectionRange(start + before.length, start + before.length + selected.length);
      field.focus();
      field.dispatchEvent(new Event('input', {bubbles: true}));
    });
  });

  async function renderPreview(source, sequence) {
    previewController = new AbortController();
    const csrf = root.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
    try {
      const response = await fetch(root.dataset.previewUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrf},
        body: new URLSearchParams({source}),
        signal: previewController.signal
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (sequence !== previewSequence) return;
      preview.innerHTML = payload.html;
      previewStatus.textContent = '';
      if (window.MathJax?.typesetPromise) {
        window.MathJax.typesetPromise([preview]).catch(() => {});
      }
    } catch (error) {
      if (error.name !== 'AbortError' && sequence === previewSequence) {
        previewStatus.textContent = '预览失败，当前内容仍可编辑。';
      }
    }
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewController?.abort();
    const sequence = ++previewSequence;
    const source = statement.value;
    if (!source) {
      preview.replaceChildren();
      previewStatus.textContent = '';
      return;
    }
    previewTimer = setTimeout(() => renderPreview(source, sequence), 300);
  }
  statement?.addEventListener('input', schedulePreview);
  if (statement?.value) schedulePreview();

  root.querySelectorAll('[data-relation-search]').forEach(search => {
    const select = root.querySelector(`select[name="${search.dataset.relationSearch}"]`);
    if (!select) return;
    search.addEventListener('input', () => {
      const query = search.value.trim().toLocaleLowerCase();
      [...select.options].forEach(option => {
        option.hidden = !option.selected && !option.text.toLocaleLowerCase().includes(query);
      });
    });
  });

  const subject = root.querySelector('[data-subject-select] input');
  const section = root.querySelector('[data-section-select] input');
  const sectionOptions = [...document.querySelectorAll('#question-section-options option')];
  let lastSubjectName = subject?.value.trim() || '';
  function refreshSections() {
    if (!subject || !section) return;
    const subjectName = subject.value.trim();
    const selectedOption = sectionOptions.find(option => option.value === section.value);
    const subjectChanged = subjectName !== lastSubjectName;
    if (
      section.value &&
      subjectChanged &&
      (selectedOption?.dataset.subjectName !== subjectName || !selectedOption)
    ) {
      section.value = '';
    }
    lastSubjectName = subjectName;
  }
  subject?.addEventListener('input', refreshSections);
  subject?.addEventListener('change', refreshSections);
  section?.addEventListener('change', refreshSections);
  refreshSections();

  return {setMode, activateTab, refreshSections};
}
