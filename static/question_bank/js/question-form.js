import {initializeEditor} from './question-form-editor.js';
import {initializeAttachmentQueue} from './question-form-attachments.js';
import {initializeDrafts, serverBaseline} from './question-form-drafts.js';

const root = document.querySelector('[data-question-form]');
if (root) {
  root.addEventListener('click', event => {
    const submitter = event.target.closest('button[data-confirm-draft]');
    if (!submitter || !root.contains(submitter)) return;
    if (!window.confirm('确定将此题目转为草稿吗？')) event.preventDefault();
  });
  const baseline = serverBaseline(root);
  const attachments = initializeAttachmentQueue(root);
  if (attachments) {
    const editor = initializeEditor(root);
    root.classList.add('is-enhanced');
    window.QuestionFormWorkbench = Object.freeze({editor, attachments});
    initializeDrafts(root, editor, attachments, baseline).then(drafts => {
      window.QuestionFormWorkbench = Object.freeze({editor, attachments, drafts});
    }).catch(() => {
      const status = document.querySelector('[data-draft-status]');
      if (status) status.textContent = '本地草稿不可用，可继续提交表单。';
    });
  } else {
    const status = root.querySelector('[data-attachment-count]');
    if (status) status.textContent = '当前浏览器使用原生附件控件。';
  }
}
