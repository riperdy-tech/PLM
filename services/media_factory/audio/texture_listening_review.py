"""Source-bound, score-blind P6 listening reviews for a loopback-only operator UI.

This module does not grant listening rights, authenticate human reviewers, or
persist WAVs/reviews. The UI must be served only on the PC's loopback interface.
"""
from __future__ import annotations

import csv
import html
import io
from collections.abc import Mapping, Sequence

from ..artifacts import ArtifactStore
from ..texture_sample_audit import SampleSource
from .texture_label_evaluation import REFERENCE, RELATIONSHIPS, REVIEW_COLUMNS

MAX_WAV_BYTES = 64 * 1024 * 1024
Pair = tuple[SampleSource, SampleSource]


def render_listening_page(pairs: Sequence[Pair], *, token: str) -> bytes:
    """Show only A/B tracks; do not expose model scores or release identities."""
    if not 1 <= len(pairs) <= 12 or not token:
        raise ValueError("invalid_review_session")
    sections: list[str] = []
    for i, (_left, _right) in enumerate(pairs):
        audio_a = html.escape(f"/s/{token}/audio/{i}/a", quote=True)
        audio_b = html.escape(f"/s/{token}/audio/{i}/b", quote=True)
        sections.append(
            f'<fieldset><legend>Pair {i + 1}</legend>'
            f'<label>Recording A <audio controls preload="none" src="{audio_a}">'
            f'</audio></label>'
            f'<label>Recording B <audio controls preload="none" src="{audio_b}">'
            f'</audio></label>'
            f'<label>Comparison <select name="r{i}">'
            f'<option value="">Not reviewed</option>'
            f'<option value="same_recording">Same recording / derivative</option>'
            f'<option value="different_music">Different music (confirmed)</option>'
            f'<option value="uncertain">Uncertain</option></select></label>'
            f'<label>Evidence reference (opaque code; required for decisive labels)'
            f'<input name="e{i}" maxlength="96" placeholder="review-note-reference">'
            f'</label></fieldset>'
        )
    export_path = html.escape(f"/s/{token}/export", quote=True)
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>P6 local listening only</title>'
        '<style>body{font:16px system-ui;max-width:800px;margin:2em auto;padding:1em}'
        'fieldset{margin:1.5em 0;padding:1em}label{display:block;margin:1em 0}'
        'audio{display:block;width:100%}input,select{font:inherit;padding:.35em}'
        'button{font:inherit;padding:.7em}</style></head><body>'
        '<h1>Local-only P6 listening review</h1>'
        '<p>Audio A and B are checksum-verified source recordings; no score, '
        'model prediction or album name is shown. You determine the relationship. '
        'Different SHA256 bytes do not imply different music.</p>'
        '<p>No answers are saved on this server. Export a CSV only when ready; '
        'keep reviewer pseudonyms separate for independent judgments. '
        'Do not use this screen as copyright or publishing approval.</p>'
        f'<form method="POST" action="{export_path}">'
        '<label>Reviewer pseudonym <input name="reviewer_id" maxlength="96" '
        'autocomplete="off" required></label>'
        + "".join(sections)
        + '<button type="submit">Export my reviews as CSV</button></form>'
        '</body></html>'
    )
    return page.encode("utf-8")


def export_review_csv(pairs: Sequence[Pair], fields: Mapping[str, list[str]]) -> bytes:
    """Require deliberate reviewer choices and refs; never invent missing labels."""
    if not 1 <= len(pairs) <= 12:
        raise ValueError("invalid_review_pairs")
    expected = {"reviewer_id"}
    expected.update(f"r{i}" for i in range(len(pairs)))
    expected.update(f"e{i}" for i in range(len(pairs)))
    if set(fields) != expected or any(len(values) != 1 for values in fields.values()):
        raise ValueError("malformed_or_duplicate_review_fields")
    reviewer = fields["reviewer_id"][0]
    if not REFERENCE.fullmatch(reviewer):
        raise ValueError("invalid_reviewer_pseudonym")
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(REVIEW_COLUMNS)
    completed = 0
    for i, (left, right) in enumerate(pairs):
        label = fields[f"r{i}"][0]
        evidence = fields[f"e{i}"][0]
        if not label:
            if evidence:
                raise ValueError("unreviewed_case_has_evidence")
            continue
        if label not in RELATIONSHIPS:
            raise ValueError("invalid_review_label")
        if (
            evidence and not REFERENCE.fullmatch(evidence)
            or label != "uncertain" and not REFERENCE.fullmatch(evidence)
        ):
            raise ValueError("missing_or_invalid_evidence_ref")
        writer.writerow([left.sha256, right.sha256, reviewer, label, evidence])
        completed += 1
    if completed == 0:
        raise ValueError("no_review_choices_entered")
    return out.getvalue().encode("utf-8")


def verified_review_audio(
    pairs: Sequence[Pair], store: ArtifactStore, *, index: int, side: str,
) -> bytes:
    """Fetch selected original bytes on demand; refuse oversized/non-WAV sources."""
    if index < 0 or index >= len(pairs) or side not in ("a", "b"):
        raise ValueError("invalid_review_audio_selector")
    source = pairs[index][0 if side == "a" else 1]
    data = store.fetch_verified(source.audio_uri, source.sha256)
    if not 44 <= len(data) <= MAX_WAV_BYTES or (
        data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE"
    ):
        raise ValueError("source_is_not_bounded_wav")
    return data
