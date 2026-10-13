import math
import re
from collections import Counter


STOPWORDS = frozenset({
    "a", "an", "the", "i", "me", "my", "mine", "you", "your", "yours", "we", "our", "ours",
    "he", "she", "it", "they", "their", "this", "that", "these", "those", "am", "is", "are",
    "was", "were", "be", "been", "being", "do", "does", "did", "have", "has", "had", "to",
    "of", "in", "on", "at", "for", "from", "with", "and", "or", "but", "if", "as", "by",
    "what", "which", "who", "where", "when", "how", "can", "could", "would", "should", "please",
})


def tokenize(text):
    """English words plus Chinese characters/bigrams; deterministic, no language model."""
    lowered = text.lower()
    tokens = [token for token in re.findall(r"[a-z0-9_]+", lowered) if token not in STOPWORDS]
    for sequence in re.findall(r"[\u3400-\u9fff]+", lowered):
        tokens.extend(sequence)
        tokens.extend(sequence[i:i + 2] for i in range(len(sequence) - 1))
    return tokens


def bm25(query, memories):
    tokens = set(tokenize(query))
    docs = [Counter(tokenize(m["key"] + " " + m["text"])) for m in memories]
    average = sum(sum(d.values()) for d in docs) / max(len(docs), 1) or 1
    frequencies = {token: sum(1 for doc in docs if token in doc) for token in tokens}
    scored = []
    for memory, doc in zip(memories, docs):
        length, score = sum(doc.values()), 0.0
        for token in tokens:
            freq = doc[token]
            if not freq:
                continue
            containing = frequencies[token]
            inverse = math.log(1 + (len(docs) - containing + 0.5) / (containing + 0.5))
            score += inverse * freq * 2.2 / (freq + 1.2 * (0.25 + 0.75 * length / average))
        if score > 0:
            scored.append((memory, score))
    return sorted(scored, key=lambda pair: (-pair[1], pair[0]["id"]))


def validate_vector(vector, dimensions):
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise ValueError("Embedding dimension mismatch")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
        raise ValueError("Embedding must contain finite numbers")
    try:
        norm = math.sqrt(sum(float(v) ** 2 for v in vector))
    except OverflowError:
        raise ValueError("Embedding norm exceeds numeric bounds") from None
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Embedding must have a finite nonzero norm")
    return [float(v) / norm for v in vector]


def cosine(left, right):
    if len(left) != len(right):
        raise ValueError("Cannot compare different dimensions")
    return sum(a * b for a, b in zip(left, right))


def fuse(lexical, semantic, limit):
    combined = {}
    for channel, ranking in (("bm25", lexical), ("semantic", semantic)):
        for rank, (memory, _) in enumerate(ranking, 1):
            item = combined.setdefault(memory["id"], {"memory": memory, "score": 0.0, "channels": []})
            item["score"] += 1 / (60 + rank)
            item["channels"].append(channel)
    return sorted(combined.values(), key=lambda h: (-h["score"], h["memory"]["id"]))[:limit]
