"""ONNX sequence classifiers for prompt attacks (CPU, no PyTorch).

Works with encoder classifiers exported to ONNX with a Hugging Face ``tokenizer.json`` (DeBERTa,
ModernBERT). Long texts are scored in overlapping character windows; the highest window wins.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

_WINDOW_CHARS = 2_000
_OVERLAP_CHARS = 200


class OnnxClassifier:
    def __init__(
        self,
        name: str,
        model_path: Path,
        tokenizer_path: Path,
        *,
        attack_index: int = 1,
        max_tokens: int = 512,
        threads: int = 1,
    ) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self.name = name
        self._session = ort.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.no_padding()
        self._tokenizer.enable_truncation(max_length=max_tokens)
        self._attack_index = attack_index
        self._lock = threading.Lock()  # one inference at a time per session

    def _windows(self, text: str) -> list[str]:
        if len(text) <= _WINDOW_CHARS:
            return [text]
        step = _WINDOW_CHARS - _OVERLAP_CHARS
        return [text[i : i + _WINDOW_CHARS] for i in range(0, len(text), step)]

    def _score(self, text: str) -> float:
        ids = self._tokenizer.encode(text).ids
        input_ids = np.asarray([ids], dtype=np.int64)
        feed = {"input_ids": input_ids, "attention_mask": np.ones_like(input_ids)}
        if "position_ids" in self._inputs:
            feed["position_ids"] = np.arange(len(ids), dtype=np.int64)[None, :]
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        with self._lock:
            (logits,) = self._session.run(["logits"], feed)
        row = logits[0].astype(np.float64)
        probs = np.exp(row - row.max())
        probs /= probs.sum()
        return float(probs[self._attack_index])

    def scores(self, texts: list[str]) -> list[float]:
        return [max(self._score(w) for w in self._windows(t)) if t.strip() else 0.0 for t in texts]
