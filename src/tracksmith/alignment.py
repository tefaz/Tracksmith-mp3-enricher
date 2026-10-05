"""Evidence-gated known-lyric alignment. No inferred timestamp for a missing line."""

from __future__ import annotations

import importlib.metadata
import logging
import sys
import tempfile
import wave
from collections import Counter
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

import numpy as np

from .cache import Cache, file_hash
from .config import Settings
from .jobs import Cancelled, JobContext
from .lyrics import alignment_reference, alignment_text, normalize_text
from .model import LyricLine, Track, Word

log = logging.getLogger(__name__)
CTC_MODELS = {
    "en": "facebook/wav2vec2-base-960h",
    "de": "jonatasgrosman/wav2vec2-large-xlsr-53-german",
    "fr": "jonatasgrosman/wav2vec2-large-xlsr-53-french",
    "es": "jonatasgrosman/wav2vec2-large-xlsr-53-spanish",
}


class VocalSeparationService(Protocol):
    def separate(self, audio: Path, directory: Path, context: JobContext) -> Path: ...


class TranscriptionService(Protocol):
    def transcribe(self, audio: np.ndarray, context: JobContext) -> tuple[list[Word], str]: ...


class LyricsAlignmentService(Protocol):
    def align(self, track: Track, context: JobContext) -> list[LyricLine]: ...


class DemucsSeparation:
    def __init__(self, device: str):
        self.device = device

    def separate(self, audio: Path, directory: Path, context: JobContext) -> Path:
        context.run(
            [
                sys.executable,
                "-m",
                "demucs",
                "--two-stems=vocals",
                "-n",
                "htdemucs",
                "-d",
                self.device,
                "--shifts",
                "0",
                "-o",
                str(directory),
                str(audio),
            ]
        )
        result = directory / "htdemucs" / audio.stem / "vocals.wav"
        if not result.exists():
            raise RuntimeError("Demucs did not produce a vocal stem")
        return result


def decode_audio(source: Path, target: Path, context: JobContext) -> np.ndarray:
    context.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
    )
    with wave.open(str(target), "rb") as audio:
        return (
            np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32)
            / 32768
        )


def select_device(requested: str) -> str:
    import torch

    if requested == "cpu":
        return "cpu"
    # PyTorch exposes ROCm devices through its cuda API. Only accept HIP builds here.
    if requested in {"rocm", "auto"} and torch.version.hip and torch.cuda.is_available():
        return "cuda"
    if requested == "rocm":
        raise RuntimeError(
            "ROCm was requested but a working HIP PyTorch GPU is unavailable. Choose CPU in Settings."
        )
    return "cpu"


class WhisperTranscription:
    def __init__(self, settings: Settings, device: str):
        self.settings, self.device = settings, device

    def transcribe(self, audio: np.ndarray, context: JobContext) -> tuple[list[Word], str]:
        import torch
        import whisper

        context.progress(f"Load vocal recognition model ({self.settings.whisper_model})", 30)
        model = whisper.load_model(
            self.settings.whisper_model,
            device=self.device,
            download_root=str(Path(self.settings.cache_directory) / "models/whisper"),
        )
        context.check()
        seconds = len(audio) // 16000
        context.progress(
            f"Recognize vocals in {seconds // 60}:{seconds % 60:02d} of audio; this stage reports when finished",
            30,
        )
        # No reference prompt: independent evidence reduces lyric-induced hallucination.
        try:
            result = model.transcribe(
                audio,
                language=None if self.settings.language == "auto" else self.settings.language,
                word_timestamps=True,
                fp16=False,
                verbose=None,
                temperature=0,
                condition_on_previous_text=False,
                no_speech_threshold=None,
            )
            context.check()
            words = [
                Word(w["word"].strip(), w["start"], w["end"], float(w.get("probability", 0)))
                for segment in result["segments"]
                for w in segment.get("words", [])
                if w["end"] > w["start"]
            ]
            return words, result.get("language", "en")
        finally:
            del model
            if self.device != "cpu":
                torch.cuda.empty_cache()


@dataclass
class Passage:
    first: int
    last: int  # exclusive transcript word index
    score: float
    reordered: bool = False


def match_passages(
    lines: list[LyricLine], words: list[Word], context: JobContext | None = None
) -> dict[int, Passage]:
    """Beam search follows chronology, permits omitted lines, and consumes repeats once.

    Only strong evidence in unused audio can rescue reordered lines, at reduced confidence.
    """
    context = context or JobContext()
    tokens = [normalize_text(word.text) for word in words]
    candidates = []
    for line in lines:
        context.check()
        expected = alignment_text(line.text).split()
        options = []
        if expected:
            for start in range(len(words)):
                for length in range(max(1, len(expected) - 2), len(expected) + 3):
                    end = start + length
                    if end > len(words) or words[end - 1].end - words[start].start > max(
                        20, len(expected) * 3
                    ):
                        continue
                    if any(words[j + 1].start - words[j].end > 3 for j in range(start, end - 1)):
                        continue
                    recognized = " ".join(tokens[start:end])
                    similarity = SequenceMatcher(None, " ".join(expected), recognized).ratio()
                    evidence = sum(word.confidence for word in words[start:end]) / length
                    score = similarity * (0.6 + 0.4 * evidence)
                    # Short/common lines need much stronger evidence.
                    minimum = 0.82 if len(expected) < 3 else 0.64
                    if score >= minimum and similarity >= 0.72:
                        options.append(Passage(start, end, score))
        # Keep different occurrences, including late choruses, instead of global top-k.
        options.sort(key=lambda p: (p.first, -p.score))
        distinct = {}
        for option in options:
            current = distinct.get(option.first)
            if current is None or option.score > current.score:
                distinct[option.first] = option
        candidates.append(list(distinct.values()))
    # State: objective, consumed transcript index, chosen passages.
    states = [(0.0, 0, {})]
    for line_index, options in enumerate(candidates):
        context.check()
        expanded = list(states)  # skip this reference line entirely
        weight = max(1, len(alignment_text(lines[line_index].text).split()))
        for score, last, chosen in states:
            for option in options:
                if option.first >= last:
                    expanded.append(
                        (score + option.score * weight, option.last, {**chosen, line_index: option})
                    )
        # Retain best state per endpoint; the future depends only on endpoint.
        best = {}
        for state in expanded:
            if state[1] not in best or state[0] > best[state[1]][0]:
                best[state[1]] = state
        states = sorted(best.values(), key=lambda s: s[0], reverse=True)[:128]
    chosen = max(states, key=lambda s: s[0])[2] if states else {}
    used = {index for passage in chosen.values() for index in range(passage.first, passage.last)}
    for index, options in enumerate(candidates):
        if index in chosen:
            continue
        available = [
            p for p in options if p.score >= 0.88 and not used.intersection(range(p.first, p.last))
        ]
        if available:
            option = max(available, key=lambda p: p.score)
            option = Passage(option.first, option.last, min(option.score, 0.65), True)
            chosen[index] = option
            used.update(range(option.first, option.last))
    return chosen


def ctc_viterbi(
    log_probs: np.ndarray, tokens: list[int], blank: int
) -> list[tuple[int, int, float]]:
    """CTC best path for supplied tokens, allowing blank margins and repeated letters.

    Returns frame ranges and geometric acoustic support for each token. Not a calibrated probability.
    """
    if not tokens or log_probs.shape[0] < len(tokens):
        raise ValueError("Insufficient audio frames for reference text")
    labels = np.full(2 * len(tokens) + 1, blank, dtype=int)
    labels[1::2] = tokens
    states = len(labels)
    scores = np.full(states, -np.inf)
    scores[0] = 0
    back = np.zeros((len(log_probs), states), dtype=np.uint8)
    skip_allowed = np.zeros(states, dtype=bool)
    skip_allowed[2:] = (labels[2:] != blank) & (labels[2:] != labels[:-2])
    for frame, probabilities in enumerate(log_probs):
        stay = scores
        advance = np.r_[-np.inf, scores[:-1]]
        skip = np.r_[[-np.inf, -np.inf], scores[:-2]]
        skip[~skip_allowed] = -np.inf
        transitions = np.stack((stay, advance, skip))
        choices = transitions.argmax(axis=0)
        back[frame] = choices
        scores = transitions[choices, np.arange(states)] + probabilities[labels]
    state = states - 1 if scores[-1] >= scores[-2] else states - 2
    if not np.isfinite(scores[state]):
        raise ValueError("No valid forced alignment path")
    positions = [[] for _ in tokens]
    for frame in range(len(log_probs) - 1, -1, -1):
        if state % 2:
            positions[state // 2].append(frame)
        state -= int(back[frame, state])
    if any(not frames for frames in positions):
        raise ValueError("Forced alignment omitted reference characters")
    result = []
    for token, frames in zip(tokens, positions):
        start, end = min(frames), max(frames) + 1
        support = float(np.exp(np.mean(log_probs[frames, token])))
        result.append((start, end, support))
    return result


def download_ctc_model(model_id: str, cache_directory: Path, context: JobContext) -> str:
    """Download only inference files, with byte progress rather than a silent 65%."""
    from concurrent.futures import ThreadPoolExecutor

    from huggingface_hub import HfApi, snapshot_download, try_to_load_from_cache

    context.progress("Check alignment model download (first use)", 65)
    info = HfApi().model_info(model_id, files_metadata=True, timeout=10)
    files = info.siblings
    weights = [item.rfilename for item in files if item.rfilename.endswith(".safetensors")]
    if not weights:
        weights = [
            item.rfilename
            for item in files
            if item.rfilename.endswith(".bin") and "pytorch_model" in item.rfilename
        ]
    if not weights:
        raise ValueError("This CTC repository has no supported inference weights")
    names = [
        item.rfilename
        for item in files
        if item.rfilename.endswith(".json") or item.rfilename in weights
    ]
    total = sum(item.size or 0 for item in files if item.rfilename in names)
    blobs = cache_directory / ("models--" + model_id.replace("/", "--")) / "blobs"
    context.check()
    # snapshot_download's public tqdm hook reports files, not download bytes.
    # Poll its resumable cache files while the public downloader owns transfers.
    # This thread only exists inside the killable analysis child.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            snapshot_download,
            model_id,
            revision="main",
            cache_dir=str(cache_directory),
            allow_patterns=names,
            max_workers=1,
            etag_timeout=10,
        )
        previous = None
        while not future.done():
            context.check()
            downloaded = 0
            for name in names:
                cached = try_to_load_from_cache(
                    model_id, name, cache_dir=str(cache_directory), revision=info.sha
                )
                if isinstance(cached, str):
                    downloaded += Path(cached).stat().st_size
            for partial in blobs.glob("*.incomplete"):
                try:
                    downloaded += partial.stat().st_size
                except FileNotFoundError:
                    pass  # A completed transfer has just renamed the file.
            if downloaded != previous:
                maximum = f" / {total / 1048576:.1f} MB" if total else " MB"
                context.progress(
                    f"Download alignment model: {downloaded / 1048576:.1f}{maximum}", 65
                )
                previous = downloaded
            context.cancelled.wait(0.25)
        context.check()
        return future.result()


class CTCForcedAligner:
    def __init__(
        self, model_id: str, device: str, cache_directory: Path, context: JobContext | None = None
    ):
        context = context or JobContext()
        context.progress("Initialize alignment backend", 65)
        from transformers import AutoModelForCTC, AutoProcessor

        context.check()
        try:
            from huggingface_hub import snapshot_download

            snapshot = snapshot_download(
                model_id, cache_dir=str(cache_directory), local_files_only=True
            )
            self.processor = AutoProcessor.from_pretrained(snapshot, local_files_only=True)
            self.model = AutoModelForCTC.from_pretrained(snapshot, local_files_only=True)
        except OSError:
            snapshot = download_ctc_model(model_id, cache_directory, context)
            context.progress("Load downloaded alignment weights into memory", 65)
            self.processor = AutoProcessor.from_pretrained(snapshot, local_files_only=True)
            self.model = AutoModelForCTC.from_pretrained(snapshot, local_files_only=True)
        context.check()
        self.model = self.model.to(device).eval()
        self.device = device

    def words(self, audio: np.ndarray, text: str, offset: float) -> list[Word]:
        import torch

        tokenizer = self.processor.tokenizer
        text = alignment_text(text)
        # English models have uppercase vocabularies; multilingual ones generally lowercase.
        vocab = tokenizer.get_vocab()
        if "A" in vocab and "a" not in vocab:
            text = text.upper()
        tokens = tokenizer(text, add_special_tokens=False).input_ids
        if tokenizer.unk_token_id in tokens:
            raise ValueError("Reference characters unsupported by this language model")
        encoded = self.processor(audio, sampling_rate=16000, return_tensors="pt")
        with torch.inference_mode():
            inputs = {key: value.to(self.device) for key, value in encoded.items()}
            logits = self.model(**inputs).logits[0]
            probabilities = torch.log_softmax(logits.float(), dim=-1).cpu().numpy()
        spans = ctc_viterbi(probabilities, tokens, tokenizer.pad_token_id)
        scale = len(audio) / 16000 / len(probabilities)
        words, current = [], []
        delimiter = tokenizer.word_delimiter_token_id
        for token, span in zip(tokens, spans):
            if token == delimiter:
                if current:
                    words.append(current)
                    current = []
            else:
                current.append(span)
        if current:
            words.append(current)
        texts = alignment_text(text).split()
        if len(words) != len(texts):
            raise ValueError("Tokenizer word boundaries did not match supplied lyrics")
        return [
            Word(
                word,
                offset + spans[0][0] * scale,
                offset + spans[-1][1] * scale,
                float(np.mean([span[2] for span in spans])),
            )
            for word, spans in zip(texts, words)
        ]


def group_passages(passages: dict[int, Passage], words: list[Word]) -> list[list[int]]:
    """Group nearby evidenced lines into bounded windows in actual audio order."""
    groups = []
    for index, passage in sorted(passages.items(), key=lambda item: words[item[1].first].start):
        if (
            groups
            and words[passage.last - 1].end - words[passages[groups[-1][0]].first].start <= 28
            and words[passage.first].start - words[passages[groups[-1][-1]].last - 1].end <= 3
        ):
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups


class WhisperForcedAligner:
    """Force supplied text through Whisper attention/DTW, within evidenced passages.

    Adjacent lines share context so each line is not forced to be a new utterance.
    Independent recognition still decides which lines may be located at all.
    """

    def __init__(self, settings: Settings, device: str, language: str, context: JobContext):
        import whisper
        from whisper.tokenizer import get_tokenizer

        context.progress("Load known-lyrics timing model (Whisper)", 65)
        self.model = whisper.load_model(
            settings.whisper_model,
            device=device,
            download_root=str(Path(settings.cache_directory) / "models/whisper"),
        )
        context.check()
        self.tokenizer = get_tokenizer(
            self.model.is_multilingual,
            num_languages=self.model.num_languages,
            language=language,
            task="transcribe",
        )
        self.refine_words = settings.ai_backend == "whisper-refined"

    def words(self, audio: np.ndarray, text: str, offset: float) -> list[Word]:
        import whisper
        from whisper.timing import find_alignment, merge_punctuations

        if not 0 < len(audio) <= 30 * 16000:
            raise ValueError("Known-lyric alignment needs a window of at most 30 seconds")
        tokens = self.tokenizer.encode(" " + alignment_reference(text))
        if len(tokens) + len(self.tokenizer.sot_sequence) + 2 > self.model.dims.n_text_ctx:
            raise ValueError("Too many lyric tokens in one timing window")
        mel = whisper.log_mel_spectrogram(audio, self.model.dims.n_mels)
        timings = find_alignment(
            self.model,
            self.tokenizer,
            tokens,
            whisper.pad_or_trim(mel, 3000).to(self.model.device),
            mel.shape[-1],
        )
        merge_punctuations(timings, "\"'“¿([{-", "\"'.。,，!！?？:：”)]}、")
        return [
            Word(
                item.word.strip(),
                float(offset + item.start),
                float(offset + item.end),
                float(item.probability),
            )
            for item in timings
            if normalize_text(item.word)
        ]

    def align_lines(self, audio, lines, passages, recognized, context):
        groups = group_passages(passages, recognized)
        prepared = {}
        for number, group in enumerate(groups):
            context.check()
            context.progress(
                f"Align known lyrics: audio window {number + 1} of {len(groups)}",
                70 + int(25 * number / len(groups)),
            )
            first = recognized[passages[group[0]].first].start
            last = recognized[passages[group[-1]].last - 1].end
            start = max(0, first - 0.5)
            end = min(len(audio) / 16000, last + 0.5)
            text = " ".join(alignment_reference(lines[index].text) for index in group)
            try:
                output = self.words(audio[int(start * 16000) : int(end * 16000)], text, start)
                if normalize_text(" ".join(word.text for word in output)) != alignment_text(text):
                    raise ValueError("Known-lyric alignment did not cover all supplied words")
                if len(output) != len(alignment_text(text).split()):
                    raise ValueError("Tokenizer word boundaries did not match lyric lines")
                cursor = 0
                for index in group:
                    count = len(alignment_text(lines[index].text).split())
                    prepared[index] = output[cursor : cursor + count]
                    cursor += count
                    if getattr(self, "refine_words", False):
                        from .word_refinement import refine_whisper_starts

                        context.progress(
                            f"Refine word starts: lyric line {index + 1} (extra acoustic checks)",
                            70 + int(25 * number / len(groups)),
                        )
                        line_words = prepared[index]
                        crop_start = max(0, line_words[0].start - 1)
                        crop_end = min(len(audio) / 16000, line_words[-1].end + 1)
                        if crop_end - crop_start <= 30:
                            prepared[index] = refine_whisper_starts(
                                self.model, self.tokenizer,
                                audio[int(crop_start * 16000):int(crop_end * 16000)],
                                line_words, crop_start, context,
                            )
            except ValueError as exc:
                for index in group:
                    prepared[index] = exc
        return prepared


class LocalAlignment:
    VERSION = 3

    def __init__(
        self,
        settings: Settings,
        transcription: TranscriptionService | None = None,
        forced_aligner=None,
        separation: VocalSeparationService | None = None,
    ):
        self.settings = settings
        self.transcription = transcription
        self.forced_aligner = forced_aligner
        self.separation = separation
        self.cache = Cache(Path(settings.cache_directory))

    def align(self, track: Track, context: JobContext, fresh: bool = False) -> list[LyricLine]:
        if file_hash(track.path) != track.content_hash:
            raise ValueError("MP3 changed; reopen before analysis")
        if not track.aligned_lines:
            raise ValueError("Paste lyrics and apply them to the table first")
        if self.settings.ai_backend not in {"whisper-ctc", "whisper-attention", "whisper-refined"}:
            raise ValueError("Unsupported alignment backend")
        injected = (
            self.transcription is not None
            or self.forced_aligner is not None
            or self.separation is not None
        )
        versions = {}
        if not injected:
            packages = ["torch", "openai-whisper"]
            if self.settings.ai_backend == "whisper-ctc":
                packages.append("transformers")
            if self.settings.separate_vocals:
                packages.append("demucs")
            for package in packages:
                try:
                    versions[package] = importlib.metadata.version(package)
                except importlib.metadata.PackageNotFoundError:
                    raise RuntimeError(
                        "AI dependencies are optional. Install the ai extra as described in README, or use manual timing."
                    ) from None
        identity = {
            "version": self.VERSION,
            "file": track.content_hash,
            "lyrics": [line.text for line in track.aligned_lines],
            "versions": versions,
            "model": self.settings.whisper_model,
            "ctc": self.settings.ctc_model,
            "backend": self.settings.ai_backend,
            "language": self.settings.language,
            "separation": self.settings.separate_vocals,
            "device": self.settings.device,
        }
        stored = None if injected or fresh else self.cache.get("alignment", identity)
        if stored and any(line["start"] is not None for line in stored):
            context.progress("Load cached alignment — unchanged audio and lyrics", 100)
            return [
                LyricLine(**{**line, "words": [Word(**word) for word in line["words"]]})
                for line in stored
            ]
        device = "cpu" if injected else select_device(self.settings.device)
        log.info(
            "alignment backend",
            extra={
                "backend": self.settings.ai_backend,
                "device": device,
                "model": self.settings.whisper_model,
                "versions": versions,
            },
        )
        with tempfile.TemporaryDirectory(prefix="song-alignment-") as temporary:
            directory = Path(temporary)
            source = track.path
            if self.settings.separate_vocals:
                with context.stage("Separate vocals", 5):
                    source = (self.separation or DemucsSeparation(device)).separate(
                        source, directory / "stems", context
                    )
            with context.stage("Decode local audio", 20):
                audio = decode_audio(source, directory / "decoded.wav", context)
            transcription_identity = {
                key: val for key, val in identity.items() if key not in {"lyrics", "ctc", "backend"}
            }
            transcript = (
                None
                if injected or fresh
                else self.cache.get("transcription", transcription_identity)
            )
            with context.stage("Transcribe local vocals", 30):
                if transcript:
                    context.progress("Reuse vocal recognition from unchanged local audio", 30)
                    recognized = [Word(**word) for word in transcript["words"]]
                    language = transcript["language"]
                else:
                    recognized, language = (
                        self.transcription or WhisperTranscription(self.settings, device)
                    ).transcribe(audio, context)
                    if not injected:
                        self.cache.put(
                            "transcription",
                            transcription_identity,
                            {"words": [asdict(word) for word in recognized], "language": language},
                        )
            with context.stage("Locate known lyric passages", 60):
                passages = match_passages(track.aligned_lines, recognized, context)
                reference_counts = Counter(
                    alignment_text(line.text) for line in track.aligned_lines
                )
                located_counts = Counter(
                    alignment_text(track.aligned_lines[index].text) for index in passages
                )
            aligner = self.forced_aligner
            if passages and aligner is None:
                with context.stage("Load known-lyrics timing model", 65):
                    if self.settings.ai_backend in {"whisper-attention", "whisper-refined"}:
                        aligner = WhisperForcedAligner(self.settings, device, language, context)
                    else:
                        model_id = self.settings.ctc_model or CTC_MODELS.get(language)
                        if not model_id:
                            raise ValueError(
                                f"No CTC model configured for {language}; choose Whisper attention in Settings"
                            )
                        aligner = CTCForcedAligner(
                            model_id,
                            device,
                            Path(self.settings.cache_directory) / "models/ctc",
                            context,
                        )
            prepared = None
            if passages and hasattr(aligner, "align_lines"):
                with context.stage("Force-align known lyrics in local audio windows", 70):
                    prepared = aligner.align_lines(
                        audio, track.aligned_lines, passages, recognized, context
                    )
            results = []
            with context.stage(
                "Validate word and line timestamps"
                if prepared is not None
                else "Force-align supplied lyrics",
                95 if prepared is not None else 70,
            ):
                for index, line in enumerate(track.aligned_lines):
                    context.check()
                    result = LyricLine(line.text, note="No reliable local vocal match")
                    passage = passages.get(index)
                    if passage:
                        first = recognized[passage.first].start
                        last = recognized[passage.last - 1].end
                        margin = 2.5 if prepared is not None else 0.25
                        start = max(0, first - margin)
                        end = min(len(audio) / 16000, last + margin)
                        crop = audio[int(start * 16000) : int(end * 16000)]
                        try:
                            if prepared is None and end - start > 30:
                                raise ValueError(
                                    "Passage exceeds 30 seconds; split into shorter lyric lines"
                                )
                            aligned = (
                                prepared[index]
                                if prepared is not None
                                else aligner.words(crop, line.text, start)
                            )
                            if isinstance(aligned, ValueError):
                                raise aligned
                            if normalize_text(
                                " ".join(word.text for word in aligned)
                            ) != alignment_text(line.text):
                                raise ValueError(
                                    "Forced alignment did not cover all supplied words"
                                )
                            if any(
                                right.start < left.end for left, right in zip(aligned, aligned[1:])
                            ):
                                raise ValueError("Forced word spans overlap or run backwards")
                            if not aligned or any(
                                not np.isfinite(w.start + w.end + w.confidence)
                                or w.start < start
                                or w.end > end + 0.05
                                or w.end <= w.start
                                for w in aligned
                            ):
                                raise ValueError("Invalid forced word spans")
                            support = min(w.confidence for w in aligned)
                            confidence = min(passage.score, support)
                            ambiguous_repeat = reference_counts[alignment_text(line.text)] > max(
                                1, located_counts[alignment_text(line.text)]
                            )
                            if ambiguous_repeat:
                                confidence = min(confidence, 0.65)
                            if confidence < 0.35:
                                result.confidence = confidence
                                result.note = "Weak acoustic evidence; timestamp withheld"
                            else:
                                result.start, result.end = aligned[0].start, aligned[-1].end
                                result.words, result.confidence = aligned, confidence
                                result.source = "ai"
                                result.note = (
                                    "Repeated occurrence ambiguous after cuts; review"
                                    if ambiguous_repeat
                                    else (
                                        "Reordered passage; review"
                                        if passage.reordered
                                        else "Heuristic score; review singing alignment"
                                    )
                                )
                                if prepared is not None and (
                                    abs(result.start - first) > 0.5 or abs(result.end - last) > 0.5
                                ):
                                    result.confidence = min(result.confidence, 0.65)
                                    result.note = "Timing differs from vocal recognition boundaries; listen to review"
                        except Cancelled:
                            raise
                        except ValueError as exc:
                            result.note = str(exc)
                    results.append(result)
                    context.progress(
                        f"Set lyric timestamps: line {index + 1} of {len(track.aligned_lines)}",
                        (
                            95 + int(4 * (index + 1) / len(track.aligned_lines))
                            if prepared is not None
                            else 70 + int(29 * (index + 1) / len(track.aligned_lines))
                        ),
                    )
            context.check()
            if file_hash(track.path) != track.content_hash:
                raise ValueError("MP3 changed during analysis; results discarded")
            if not injected:
                self.cache.put("alignment", identity, [asdict(line) for line in results])
            context.progress("Local timing analysis complete", 100)
            return results
