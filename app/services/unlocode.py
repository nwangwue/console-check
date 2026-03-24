import csv
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from app.config import FUZZY_MATCH_THRESHOLD


@dataclass
class UnlocodeEntry:
    code: str       # 3-char code (e.g., "DAL")
    name: str       # City name with diacritics
    name_ascii: str # City name ASCII-safe
    state: str      # 2-char state code


@dataclass
class UnlocodeResult:
    code: str | None = None
    resolved: bool = False
    confidence: float = 0.0  # 1.0 for exact, fuzzy score/100 for others
    suggestions: list[dict] = field(default_factory=list)  # top 3 if unresolved


# Common city name normalizations
CITY_NORMALIZATIONS = {
    "st.": "saint",
    "st ": "saint ",
    "ft.": "fort",
    "ft ": "fort ",
    "mt.": "mount",
    "mt ": "mount ",
    "n.": "north",
    "s.": "south",
    "e.": "east",
    "w.": "west",
}


def _normalize_city(city: str) -> str:
    """Apply common city name normalizations."""
    result = city.lower().strip()
    for abbrev, full in CITY_NORMALIZATIONS.items():
        result = result.replace(abbrev, full)
    # Strip extra whitespace
    result = re.sub(r'\s+', ' ', result).strip()
    return result


class UnlocodeService:
    def __init__(self, csv_path: str):
        """Load US UNLOCODE data into memory."""
        self.entries: list[UnlocodeEntry] = []
        self.by_city_state: dict[tuple[str, str], list[UnlocodeEntry]] = {}
        self.by_state: dict[str, list[UnlocodeEntry]] = {}

        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                entry = UnlocodeEntry(
                    code=row['code'],
                    name=row['name'],
                    name_ascii=row['name_ascii'],
                    state=row.get('state', '') or '',
                )
                self.entries.append(entry)

                # Index by (city_lower, state_upper)
                key = (entry.name_ascii.lower(), entry.state.upper())
                self.by_city_state.setdefault(key, []).append(entry)

                # Also index normalized city name
                norm_key = (_normalize_city(entry.name_ascii), entry.state.upper())
                if norm_key != key:
                    self.by_city_state.setdefault(norm_key, []).append(entry)

                # Index by state
                self.by_state.setdefault(entry.state.upper(), []).append(entry)

    def resolve(self, city: str, state: str) -> UnlocodeResult:
        """
        Resolve city + state to UNLOCODE.
        Strategy: exact match -> normalized match -> fuzzy match -> suggestions.
        """
        state_upper = state.strip().upper()
        city_lower = city.strip().lower()

        # 1. Exact match
        matches = self.by_city_state.get((city_lower, state_upper))
        if matches:
            return UnlocodeResult(
                code=matches[0].code,
                resolved=True,
                confidence=1.0,
            )

        # 2. Normalized match
        normalized = _normalize_city(city)
        matches = self.by_city_state.get((normalized, state_upper))
        if matches:
            return UnlocodeResult(
                code=matches[0].code,
                resolved=True,
                confidence=0.95,
            )

        # 3. Fuzzy match within same state
        state_entries = self.by_state.get(state_upper, [])
        if state_entries:
            choices = {e.name_ascii: e for e in state_entries}
            results = process.extract(
                city,
                list(choices.keys()),
                scorer=fuzz.ratio,
                limit=3,
            )

            if results and results[0][1] >= FUZZY_MATCH_THRESHOLD:
                best_match = choices[results[0][0]]
                return UnlocodeResult(
                    code=best_match.code,
                    resolved=True,
                    confidence=results[0][1] / 100.0,
                )

            # 4. Return suggestions
            suggestions = [
                {"code": choices[r[0]].code, "name": r[0], "score": r[1]}
                for r in results
            ]
            return UnlocodeResult(
                code=None,
                resolved=False,
                confidence=0.0,
                suggestions=suggestions,
            )

        return UnlocodeResult(code=None, resolved=False, confidence=0.0)

    def search(self, query: str) -> list[UnlocodeEntry]:
        """Search UNLOCODE entries by partial city name, code, or state."""
        query_lower = query.lower().strip()
        # Prioritize exact code matches, then name/state matches
        code_matches = []
        other_matches = []
        for entry in self.entries:
            if query_lower == entry.code.lower():
                code_matches.append(entry)
            elif (query_lower in entry.name_ascii.lower()
                    or query_lower in entry.code.lower()
                    or query_lower == entry.state.lower()):
                other_matches.append(entry)
        results = code_matches + other_matches
        return results[:20]
