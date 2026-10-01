(function () {
  const toggle = document.querySelector('[data-assistant-toggle]');
  const panel = document.querySelector('[data-assistant-panel]');
  if (toggle && panel) toggle.addEventListener('click', function () { const collapsed = panel.classList.toggle('is-collapsed'); toggle.setAttribute('aria-expanded', String(!collapsed)); });
  document.querySelectorAll('[data-reveal]').forEach(function (button) { button.addEventListener('click', function () { const target = document.getElementById(button.dataset.reveal); if (target) { target.hidden = false; button.hidden = true; } }); });
  document.querySelectorAll('[data-confirm]').forEach(function (element) { element.addEventListener('click', function (event) { if (!window.confirm(element.dataset.confirm)) event.preventDefault(); }); });
  document.querySelectorAll('input[type=file][data-preview]').forEach(function (input) { input.addEventListener('change', function () { const target = document.getElementById(input.dataset.preview); if (!target) return; target.replaceChildren(...Array.from(input.files).map(function (file) { const image = document.createElement('img'); image.alt = file.name; image.style.maxWidth = '160px'; image.src = URL.createObjectURL(file); return image; })); }); });
})();
