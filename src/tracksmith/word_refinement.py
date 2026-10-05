"""Conservative acoustic refinement of early word starts using existing Whisper weights."""

from copy import deepcopy

import numpy as np


def refine_starts(words, offset, score_audio, audio, token_boundaries, context):
    """Trim early starts only when masking that audio preserves token support.

    The first word and every end stay fixed, preserving the line's outer bounds.
    Each word is evaluated independently; an edit cannot silently move neighbours.
    ``score_audio`` returns teacher-forced probabilities for the supplied text.
    """
    result = deepcopy(words)
    context.check()
    original = score_audio([audio])[0]
    eligible = [
        i for i, word in enumerate(words)
        if i and word.confidence >= 0.5 and word.end - word.start > 0.12
        and original[token_boundaries[i]] >= 0.5
    ]
    for block in range(0, len(eligible), 4):
        indexes = eligible[block:block + 4]
        low = np.array([words[i].start - offset for i in indexes])
        high = np.array([words[i].end - offset - 0.06 for i in indexes])
        best = low.copy()
        for _ in range(6):
            context.check()
            if np.max(high - low) < 0.04:
                break
            mid = (low + high) / 2
            batch = []
            for i, cut in zip(indexes, mid):
                masked = audio.copy()
                start_sample = max(0, round((words[i].start - offset) * 16000))
                masked[start_sample:max(0, round(cut * 16000))] = 0
                batch.append(masked)
            scores = score_audio(batch)
            for k, i in enumerate(indexes):
                base = original[token_boundaries[i]]
                probability = scores[k, token_boundaries[i]]
                if probability >= max(0.5, base * 0.97, base - 0.05):
                    best[k] = mid[k]
                    low[k] = mid[k]
                else:
                    high[k] = mid[k]
        for i, cut in zip(indexes, best):
            if cut + offset - words[i].start >= 0.04:
                result[i].start = cut + offset
    return result


def refine_whisper_starts(model, tokenizer, audio, words, offset, context):
    import torch
    import whisper

    word_tokens = [tokenizer.encode(" " + word.text.strip()) for word in words]
    tokens = [token for group in word_tokens for token in group]
    if not tokens or len(tokens) + len(tokenizer.sot_sequence) + 2 > model.dims.n_text_ctx:
        return deepcopy(words)
    boundaries = np.cumsum([0] + [len(group) for group in word_tokens])
    if any(not group for group in word_tokens):
        return deepcopy(words)
    input_tokens = torch.tensor(
        [*tokenizer.sot_sequence, tokenizer.no_timestamps, *tokens, tokenizer.eot],
        device=model.device,
    )

    def probabilities(batch):
        context.check()
        with torch.inference_mode():
            waveform = torch.tensor(np.stack(batch), device=model.device)
            mel = whisper.pad_or_trim(whisper.log_mel_spectrogram(waveform, model.dims.n_mels), 3000)
            logits = model(mel, input_tokens.unsqueeze(0).expand(len(batch), -1))
            start = len(tokenizer.sot_sequence)
            probs = logits[:, start:start + len(tokens)].float().softmax(-1)
            return probs[:, torch.arange(len(tokens), device=model.device), tokens].cpu().numpy()

    return refine_starts(words, offset, probabilities, audio, boundaries, context)
