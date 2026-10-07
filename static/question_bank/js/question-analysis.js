(() => {
  const root = document.querySelector("[data-question-analysis]");
  if (!root) return;
  root.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector("button[type='submit']");
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
      }
    });
  });
})();
