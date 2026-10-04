"""Deterministic roster matching (spec §5.7)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path

log = logging.getLogger(__name__)

MIN_PREFIX = 2  # a single letter would match half the roster


@dataclass(frozen=True)
class Student:
    name: str
    aliases: tuple[str, ...] = ()
    default_location: str | None = None
    default_duration_min: int | None = None


@dataclass
class Resolution:
    resolved: list[Student] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)  # name as typed -> candidate canonical names
    unknown: list[str] = field(default_factory=list)
    ordered: list[str] = field(default_factory=list)  # display names in the order typed (resolved + unknown)


def load_roster(path: Path) -> list[Student]:
    if not path.exists():
        log.info("No roster at %s; all names will be treated as unknown", path)
        return []
    raw = json.loads(path.read_text())
    return [
        Student(
            name=r["name"],
            aliases=tuple(r.get("aliases", [])),
            default_location=r.get("default_location"),
            default_duration_min=r.get("default_duration_min"),
        )
        for r in raw
    ]


def resolve_people(names: list[str], roster: list[Student]) -> Resolution:
    """Each typed name resolves independently:
    exact full name/alias → word-prefix match (every typed word must prefix a word of the
    same student's name) → typo tolerance on single name words → unknown."""
    res = Resolution()
    full: dict[str, list[Student]] = {}
    for s in roster:
        for key in (s.name, *s.aliases):
            full.setdefault(key.casefold(), []).append(s)
    tokens: dict[str, list[Student]] = {}
    for s in roster:
        for tok in s.name.casefold().split():
            tokens.setdefault(tok, []).append(s)

    def decide(typed: str, hits: list[Student]) -> None:
        hits = list(dict.fromkeys(hits))
        if len(hits) == 1:
            if hits[0] not in res.resolved:
                res.resolved.append(hits[0])
                res.ordered.append(hits[0].name)
        else:
            res.ambiguous[typed] = [h.name for h in hits]

    for typed in names:
        q = " ".join(typed.strip().casefold().split())
        if not q:
            continue
        if q in full:
            decide(typed, full[q])
            continue
        words = q.split()
        hits = [
            s for s in roster
            if all(len(w) >= MIN_PREFIX and any(t.startswith(w) for t in s.name.casefold().split()) for w in words)
        ]
        if hits:
            decide(typed, hits)
            continue
        close = get_close_matches(q, list(tokens), n=1, cutoff=0.8) if len(words) == 1 else []
        if close:
            decide(typed, tokens[close[0]])
        else:
            res.unknown.append(display_name(typed.strip()))
            res.ordered.append(display_name(typed.strip()))
    return res


def display_name(typed: str) -> str:
    """Unknown names are shown as typed, title-cased ('zoe' → 'Zoe')."""
    return " ".join(w[:1].upper() + w[1:] for w in typed.split())


def people_title(base: str, names: list[str]) -> str:
    """'Tennis lesson – Adam & Eve'; 4+ names → 'Tennis lesson (4 students)'."""
    if not names:
        return base
    if len(names) <= 3:
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " & " + names[-1]
        return f"{base} – {joined}"
    return f"{base} ({len(names)} students)"
