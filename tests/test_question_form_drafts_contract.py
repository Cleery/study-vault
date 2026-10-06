from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "static/question_bank/js/question-form-drafts.js").read_text(encoding="utf-8")
ATTACHMENT_SOURCE = (Path(__file__).resolve().parents[1] / "static/question_bank/js/question-form-attachments.js").read_text(encoding="utf-8")


def test_editor_key_forks_only_on_observed_collision():
    claim_editor = SOURCE.split("async function claimEditor(", 1)[1].split("function captureFields(", 1)[0]

    assert "if (collided) await copyOnCollision();" in claim_editor
    assert "if (hasOtherLease()) await copyOnCollision();" in claim_editor
    assert "if (!leaseAvailable && storedId) await copyOnCollision();" not in claim_editor


def test_restore_reconciles_existing_attachment_state_and_validates_shape():
    assert "function validateDraft(draft)" in SOURCE
    assert "attachments.restoreState" in SOURCE
    assert "removed: baselineMatches ? state.removed : []" in SOURCE
    assert "order: baselineMatches ? state.order : []" in SOURCE
    assert "function restoreState(state)" in ATTACHMENT_SOURCE
    assert "item.removed = removed.has(item.id)" in ATTACHMENT_SOURCE


def test_broadcast_channel_path_checks_local_storage_collision_before_key_use():
    claim_editor = SOURCE.split("async function claimEditor(", 1)[1].split("function captureFields(", 1)[0]
    channel_path = claim_editor.split("if (channel) {", 1)[1].split("const renewal", 1)[0]

    assert "if (hasOtherLease()) await copyOnCollision();" in channel_path


def test_manual_restore_handles_rejected_restore_without_unhandled_rejection():
    assert "restore(draft).catch(reportFailure)" in SOURCE


def test_solution_image_preview_is_wired_separately_from_question_queue():
    assert "data-solution-image-input" in ATTACHMENT_SOURCE
    assert "data-solution-preview" in ATTACHMENT_SOURCE
