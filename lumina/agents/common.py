"""Small text helpers shared by agents."""
import re

STOP = set("""the a an and or but if then than so of to in on at by for with about as into like through over after before
between from up down out off again further once here there when where why how all any both each few more most other some
such no nor not only own same too very can will just don should now is are was were be been being have has had do does did
this that these those it its they them their you your we our he she his her what which who whom i me my also has been
would could may might must shall one two three first second new many much make made get got""".split())

SENT_SPLIT = re.compile(r"(?<=[.!?。！？؟۔…])\s+")


def words(text):
    return re.findall(r"\S+", text or "")


def word_count(text):
    return len(words(text))


def sentences(text):
    return [s.strip() for s in SENT_SPLIT.split((text or "").strip()) if s.strip()]


def keywords(text, n=4):
    seen, out = set(), []
    toks = re.findall(r"[A-Za-z0-9\u00C0-\u024F\u0400-\u04FF\u0600-\u06FF]{3,}", text or "")
    freq = {}
    for t in toks:
        lt = t.lower()
        if lt in STOP:
            continue
        freq[lt] = freq.get(lt, 0) + 1 + (0.5 if t[0].isupper() else 0) + min(len(t), 9) / 20.0
    for w, _ in sorted(freq.items(), key=lambda kv: -kv[1]):
        if w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= n:
            break
    return out


def ngram_set(text, n=6):
    ws = [w.lower() for w in re.findall(r"\w+", text or "")]
    return {" ".join(ws[i:i + n]) for i in range(max(len(ws) - n + 1, 0))}


def overlap_ratio(text, source, n=6):
    a, b = ngram_set(text, n), ngram_set(source, n)
    return (len(a & b) / len(a)) if a else 0.0


def script_text(script):
    return "\n\n".join(s["narration"] for s in script["sections"])


def total_words(script):
    return sum(word_count(s["narration"]) for s in script["sections"])


def trim_sentences(script, budget):
    """Last-resort deterministic trim: drop trailing sentences from middle sections until under budget."""
    secs = script["sections"]
    guard = 0
    while total_words(script) > budget and guard < 400:
        guard += 1
        for idx in range(len(secs) - 2, 0, -1):
            ss = sentences(secs[idx]["narration"])
            if len(ss) > 1:
                secs[idx]["narration"] = " ".join(ss[:-1])
                break
        else:
            break
    return script


def clip(text, n):
    text = text or ""
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
