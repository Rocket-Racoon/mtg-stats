"""Pure display-name helpers shared by the models and the services (no database access)."""

import unicodedata


def display_name_key(value: str) -> str:
    """The comparison key of a display name: equal keys mean "the same name" (S-2.3.1).

    Ignores case, accents and invisible characters: NFKD-decompose, drop every combining mark
    (category ``Mn``) and every format character (category ``Cf``), casefold, and collapse runs of
    whitespace. ``"José"``, ``"JOSE"`` and ``"jose"`` share a key, and so do ``"Bob"`` and
    ``"Bob"`` followed by a zero-width space. Pure; no length check. An empty key means the name
    has nothing visible to compare (only marks or format characters).

    ``Cf`` was chosen over the Unicode default-ignorable set: the standard library has no property
    for the latter, and ``Cf`` covers zero-width space and joiners, soft hyphen, word joiner, BOM,
    Mongolian vowel separator (U+180E) and the tag characters. Variation selectors are ``Mn``.
    Not covered: default-ignorable letters such as the Hangul fillers (category ``Lo``).
    """
    decomposed = unicodedata.normalize("NFKD", value)
    kept = "".join(ch for ch in decomposed if unicodedata.category(ch) not in ("Mn", "Cf"))
    return " ".join(kept.casefold().split())
