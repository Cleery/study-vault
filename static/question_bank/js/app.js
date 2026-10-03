(function () {
  const assistantToggle = document.querySelector('[data-assistant-toggle]');
  const assistantPanel = document.querySelector('[data-assistant-panel]');
  if (assistantToggle && assistantPanel) {
    assistantToggle.addEventListener('click', function () {
      const collapsed = assistantPanel.classList.toggle('is-collapsed');
      assistantToggle.setAttribute('aria-expanded', String(!collapsed));
    });
  }

  document.querySelectorAll('[data-reveal]').forEach(function (button) {
    button.addEventListener('click', function () {
      const target = document.getElementById(button.dataset.reveal);
      if (target) {
        target.hidden = false;
        button.hidden = true;
      }
    });
  });

  document.querySelectorAll('[data-confirm]').forEach(function (element) {
    element.addEventListener('click', function (event) {
      if (!window.confirm(element.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll('input[type=file][data-preview]').forEach(function (input) {
    if (input.closest('[data-question-form]')) return;
    input.addEventListener('change', function () {
      const target = document.getElementById(input.dataset.preview);
      if (!target) return;
      const previews = Array.from(input.files).map(function (file) {
        const image = document.createElement('img');
        image.alt = file.name;
        image.style.maxWidth = '160px';
        image.src = URL.createObjectURL(file);
        return image;
      });
      target.replaceChildren(...previews);
    });
  });

  document.querySelectorAll('[data-filter-key]').forEach(function (button) {
    button.addEventListener('click', function () {
      const url = new URL(window.location.href);
      const key = button.dataset.filterKey;
      const value = button.dataset.filterValue;
      const aliases = (button.dataset.filterAliases || key).split(',').filter(Boolean);
      const retainedValues = [];

      if (key === 'q') {
        aliases.forEach(function (alias) {
          url.searchParams.delete(alias);
        });
      } else {
        aliases.forEach(function (alias) {
          url.searchParams.getAll(alias).forEach(function (rawValue) {
            rawValue.split(',').forEach(function (item) {
              const normalized = item.trim();
              if (normalized && normalized !== value && !retainedValues.includes(normalized)) {
                retainedValues.push(normalized);
              }
            });
          });
          url.searchParams.delete(alias);
        });
      }

      retainedValues.forEach(function (item) {
        url.searchParams.append(key, item);
      });
      url.searchParams.delete('page');
      url.hash = 'question-results';
      window.location.assign(url.toString());
    });
  });

  const storageKey = 'math-question-bank:saved-filters';
  const saveButton = document.getElementById('save-filter');
  const saveStatus = document.getElementById('save-filter-status');
  const filterForm = document.getElementById('question-filter-form');
  if (saveButton && saveStatus && filterForm) {
    saveButton.addEventListener('click', function () {
      try {
        let saved;
        try {
          saved = JSON.parse(window.localStorage.getItem(storageKey) || '[]');
        } catch (error) {
          saved = [];
        }
        if (!Array.isArray(saved)) saved = [];

        const params = new URLSearchParams(new FormData(filterForm));
        saved.push({
          name: new Date().toLocaleString(),
          query: params.toString(),
          updated_at: new Date().toISOString()
        });
        window.localStorage.setItem(storageKey, JSON.stringify(saved.slice(-20)));
        saveStatus.textContent = '已保存';
      } catch (error) {
        saveStatus.textContent = '保存失败';
      }
    });
  }

  document.querySelectorAll('[data-tag-picker]').forEach(function (picker) {
    const searchInput = picker.querySelector('input[name="tag_picker_q"]');
    const status = picker.querySelector('#tag-picker-search-status');
    const form = picker.closest('form');
    let results = picker.querySelector('.tag-picker__search-results');
    let debounceTimer = null;
    let suggestionController = null;
    let focusedResult = -1;

    function selectedIds() {
      const values = (picker.getAttribute('data-selected-tags') || picker.dataset.selectedTags || '').split(',').map(function (value) {
        return value.trim();
      }).filter(Boolean);
      picker.querySelectorAll('input[name="tag"]:checked').forEach(function (input) {
        if (!values.includes(input.value)) values.push(input.value);
      });
      return values;
    }

    function updateSelectedId(input) {
      const values = selectedIds().filter(function (value) { return value !== input.value; });
      if (input.checked) values.push(input.value);
      picker.dataset.selectedTags = values.join(',');
    }

    function ensureResults() {
      if (results) return results;
      results = document.createElement('div');
      results.className = 'tag-picker__search-results';
      results.setAttribute('aria-label', '标签搜索结果');
      const search = picker.querySelector('.tag-picker__search');
      if (search) search.appendChild(results);
      return results;
    }

    function setStatus(message) {
      if (status) status.textContent = message;
    }

    function renderSuggestions(items) {
      const target = ensureResults();
      target.replaceChildren();
      focusedResult = -1;
      const bounded = Array.isArray(items) ? items.slice(0, Math.min(items.length, 20)) : [];
      if (!bounded.length) {
        const empty = document.createElement('span');
        empty.className = 'filter-empty';
        empty.textContent = '没有匹配的标签';
        target.appendChild(empty);
        return;
      }
      bounded.forEach(function (item, index) {
        const label = document.createElement('label');
        label.className = 'tag-picker__option';
        label.dataset.tagId = item.canonical_id || item.id || '';
        label.tabIndex = 0;
        label.setAttribute('role', 'option');
        label.setAttribute('aria-selected', 'false');
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.name = 'tag';
        input.value = item.canonical_id || item.id || '';
        const name = document.createElement('span');
        name.textContent = item.name || '';
        name.style.paddingInlineStart = 'calc(' + String(item.depth || 0) + ' * 18px)';
        const count = document.createElement('span');
        count.className = 'tag-picker__count';
        count.dataset.tagCount = String(item.count || 0);
        count.setAttribute('aria-label', String(item.count || 0) + ' 道题目');
        count.textContent = String(item.count || 0);
        label.appendChild(input);
        label.appendChild(name);
        label.appendChild(count);
        input.addEventListener('change', function () {
          label.setAttribute('aria-selected', String(input.checked));
          updateSelectedId(input);
        });
        label.addEventListener('keydown', function (event) {
          const options = Array.from(target.querySelectorAll('[role="option"]'));
          const current = options.indexOf(label);
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            const offset = event.key === 'ArrowDown' ? 1 : -1;
            const next = (current + offset + options.length) % options.length;
            options[next].focus();
            focusedResult = next;
          } else if (event.key === 'Home' || event.key === 'End') {
            event.preventDefault();
            const next = event.key === 'Home' ? 0 : options.length - 1;
            options[next].focus();
            focusedResult = next;
          } else if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            input.checked = !input.checked;
            input.dispatchEvent(new Event('change', { bubbles: true }));
          }
        });
        target.appendChild(label);
        if (index === focusedResult) label.focus();
      });
    }

    function requestSuggestions() {
      if (!searchInput) return;
      const prefix = searchInput.value.trim();
      if (!prefix) {
        if (suggestionController) suggestionController.abort();
        if (results) results.replaceChildren();
        setStatus('');
        return;
      }
      if (suggestionController) suggestionController.abort();
      suggestionController = new AbortController();
      const endpoint = picker.dataset.tagSuggestionsUrl;
      const url = new URL(endpoint, window.location.origin);
      url.searchParams.set('q', prefix);
      selectedIds().forEach(function (value) { url.searchParams.append('selected_tag', value); });
      if (form) {
        form.querySelectorAll('input[name="subject"]:checked').forEach(function (subject) {
          url.searchParams.append('subject', subject.value);
        });
      }
      setStatus('加载中...');
      fetch(url.toString(), {
        credentials: "same-origin",
        signal: suggestionController.signal
      }).then(function (response) {
        if (!response.ok) throw new Error('suggestion request failed');
        return response.json();
      }).then(function (payload) {
        const items = Array.isArray(payload.items) ? payload.items : [];
        renderSuggestions(items);
        setStatus(items.length ? '找到 ' + items.length + ' 个标签' : '没有匹配的标签');
      }).catch(function (error) {
        if (error && error.name === 'AbortError') return;
        if (results) results.replaceChildren();
        setStatus('标签搜索失败');
      });
    }

    function bindSelectedControls() {
      picker.querySelectorAll('.tag-picker__option--selected').forEach(function (label) {
        if (label.dataset.removeBound === 'true') return;
        const input = label.querySelector('input[name="tag"]');
        if (!input) return;
        const row = document.createElement('div');
        row.className = 'tag-picker__selected-row';
        label.parentNode.insertBefore(row, label);
        row.appendChild(label);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'tag-picker__remove';
        remove.dataset.tagRemove = input.value;
        remove.setAttribute('data-tag-remove', input.value);
        remove.setAttribute('aria-label', '移除 ' + (label.querySelector('.tag-picker__chip')?.textContent || '标签'));
        remove.textContent = '移除';
        remove.addEventListener('click', function (event) {
          event.preventDefault();
          event.stopPropagation();
          input.checked = false;
          updateSelectedId(input);
          row.remove();
        });
        row.appendChild(remove);
        label.dataset.removeBound = 'true';
      });
    }

    function bindCategoryLinks() {
      picker.querySelectorAll('.tag-picker__category-panel').forEach(function (panel) {
        panel.querySelectorAll('.tag-picker__pagination a').forEach(function (link) {
          if (link.dataset.enhanced === 'true') return;
          link.dataset.enhanced = 'true';
          link.addEventListener('click', function (event) {
            const href = link.href;
            const group = panel.dataset.tagCategory;
            if (!href || !group) return;
            event.preventDefault();
            fetch(href, { credentials: "same-origin" }).then(function (response) {
              if (!response.ok) throw new Error('category request failed');
              return response.text();
            }).then(function (html) {
              const parsed = new DOMParser().parseFromString(html, 'text/html');
              const replacement = Array.from(parsed.querySelectorAll('[data-tag-category-panel]')).find(function (candidate) {
                return candidate.dataset.tagCategory === group;
              });
              if (!replacement) throw new Error('category fragment missing');
              const currentPanel = picker.querySelector('[data-tag-category-panel][data-tag-category="' + group + '"]');
              if (!currentPanel) throw new Error('current category missing');
              currentPanel.replaceWith(replacement);
              const details = replacement.closest('details');
              const summary = details ? details.querySelector('[data-category-expand]') : null;
              if (summary) {
                summary.setAttribute('aria-controls', replacement.id || 'tag-category-panel-' + group);
                summary.setAttribute('aria-expanded', String(details.open));
              }
              bindCategoryLinks();
              bindSelectedControls();
            }).catch(function () {
              window.location.assign(href);
            });
          });
        });
      });
    }

    if (searchInput) {
      searchInput.addEventListener('input', function () {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(requestSuggestions, 200);
      });
    }
    picker.querySelectorAll('input[name="tag"]').forEach(function (input) {
      input.addEventListener('change', function () { updateSelectedId(input); });
    });
    picker.querySelectorAll('[data-category-expand]').forEach(function (summary) {
      const details = summary.closest('details');
      if (!details) return;
      details.addEventListener('toggle', function () {
        summary.setAttribute('aria-expanded', String(details.open));
      });
    });
    bindSelectedControls();
    bindCategoryLinks();
  });
})();
