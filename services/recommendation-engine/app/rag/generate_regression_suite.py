"""Generate the balanced 300-case retrieval regression suite."""

import argparse
import hashlib
import itertools
import json
from pathlib import Path

from pydantic import TypeAdapter

from app.rag.evaluate_retrieval import RetrievalEvaluationCase

FEATURE_CLAUSES = {
    "servesFood": [
        "serves meals",
        "offers cooked food",
        "has a food menu",
        "provides dinner with drinks",
        "plates up pub food",
        "has a working kitchen",
        "offers something substantial to eat",
    ],
    "hasOutdoorSeating": [
        "has outdoor seating",
        "offers tables in a beer garden",
        "has a terrace for drinks",
        "provides seating outside",
        "has tables in the open air",
        "offers a courtyard with seats",
        "lets customers sit beyond the indoor area",
    ],
    "showsSports": [
        "shows live sport",
        "screens football matches",
        "airs televised games",
        "has sports on its televisions",
        "shows the championship live",
        "carries tonight's sports feed",
        "lets customers watch the match",
    ],
    "hasLiveMusic": [
        "hosts live music",
        "puts on live bands",
        "has musicians performing",
        "offers live acoustic sets",
        "stages live gigs",
        "has performers playing music in person",
        "features live musical acts",
    ],
    "hasDj": [
        "has a DJ",
        "hosts DJ nights",
        "has DJs playing music",
        "offers a DJ-led evening",
        "puts on DJ sets",
        "features a resident DJ",
        "has a dance night with a DJ",
    ],
    "dogFriendly": [
        "welcomes dogs",
        "is dog friendly",
        "allows canine visitors",
        "lets customers bring a dog",
        "admits companion pets",
        "welcomes a greyhound",
        "allows dogs inside the venue",
    ],
    "wheelchairAccessible": [
        "is wheelchair accessible",
        "has step-free access",
        "welcomes wheelchair users",
        "has an accessible entrance",
        "can be entered without steps",
        "provides wheelchair access",
        "has a step-free way inside",
    ],
    "acceptsReservations": [
        "accepts reservations",
        "lets customers book a table",
        "takes advance bookings",
        "allows tables to be reserved",
        "can hold seats before arrival",
        "offers table reservations",
        "confirms seating in advance",
    ],
    "suitableForGroups": [
        "is suitable for groups",
        "accepts group bookings",
        "can host a large party",
        "has room for numerous coworkers",
        "welcomes collective gatherings",
        "can accommodate a group event",
        "offers space for a party of friends",
    ],
}

NEGATIVE_CLAUSES = {
    "servesFood": [
        "does not serve food",
        "has no food menu",
        "offers drinks but no meals",
        "does not provide anything to eat",
    ],
    "hasOutdoorSeating": [
        "has no outdoor seating",
        "keeps every customer table indoors",
        "does not have a beer garden",
        "offers no terrace or outside tables",
    ],
    "showsSports": [
        "does not show sport",
        "has no televised football",
        "does not screen live matches",
        "keeps sports off its televisions",
    ],
    "hasLiveMusic": [
        "does not host live music",
        "has no live bands",
        "does not stage live gigs",
        "offers no in-person musical performances",
    ],
    "hasDj": [
        "does not have a DJ",
        "has no DJ nights",
        "does not put on DJ sets",
        "offers music without a DJ",
    ],
    "dogFriendly": [
        "does not allow dogs",
        "is not dog friendly",
        "admits no canine visitors",
        "does not let customers bring a dog",
    ],
    "wheelchairAccessible": [
        "is not wheelchair accessible",
        "has no step-free access",
        "cannot be entered without steps",
        "does not provide wheelchair access",
    ],
    "acceptsReservations": [
        "does not accept reservations",
        "takes no table bookings",
        "does not hold seats in advance",
        "operates without advance reservations",
    ],
    "suitableForGroups": [
        "is not suitable for groups",
        "takes no group bookings",
        "cannot accommodate a large party",
        "does not host collective gatherings",
    ],
}


def buildRegressionCases() -> list[RetrievalEvaluationCase]:
    """Build 300 deterministic cases with a documented category balance."""
    cases: list[RetrievalEvaluationCase] = []

    # Two location wrappers turn seven independent paraphrases into fourteen
    # positive cases per feature without changing their expected hard filter.
    for feature, clauses in FEATURE_CLAUSES.items():
        for phraseIndex, clause in enumerate(clauses, start=1):
            for wrapperIndex, query in enumerate(
                (f"Pub that {clause}", f"Find a Camden venue that {clause}"),
                start=1,
            ):
                cases.append(
                    RetrievalEvaluationCase(
                        name=f"regression-300-positive-{feature}-{phraseIndex}-{wrapperIndex}",
                        query=query,
                        requiredAttributes=[feature],
                    )
                )

    # Negative facts require an explicit verified false value. Unknown is never
    # accepted, including for phrases such as "no step-free access".
    for feature, clauses in NEGATIVE_CLAUSES.items():
        for phraseIndex, clause in enumerate(clauses, start=1):
            for wrapperIndex, query in enumerate(
                (f"Pub that {clause}", f"Find a Westminster venue that {clause}"),
                start=1,
            ):
                cases.append(
                    RetrievalEvaluationCase(
                        name=f"regression-300-negative-{feature}-{phraseIndex}-{wrapperIndex}",
                        query=query,
                        excludedAttributes=[feature],
                    )
                )

    features = list(FEATURE_CLAUSES)
    pairs = list(itertools.combinations(features, 2))
    triples = list(itertools.combinations(features, 3))[:18]
    for caseIndex, selected in enumerate([*pairs, *triples], start=1):
        clauses = [FEATURE_CLAUSES[feature][caseIndex % 7] for feature in selected]
        cases.append(
            RetrievalEvaluationCase(
                name=f"regression-300-multi-positive-{caseIndex}",
                query="Find a pub that " + ", ".join(clauses[:-1]) + f" and {clauses[-1]}",
                requiredAttributes=list(selected),
            )
        )

    # Mixed cases exercise negation scope across two different features.
    for caseIndex, (required, excluded) in enumerate(pairs[:18], start=1):
        cases.append(
            RetrievalEvaluationCase(
                name=f"regression-300-mixed-{caseIndex}",
                query=(
                    f"Find a pub that {FEATURE_CLAUSES[required][caseIndex % 7]} but "
                    f"{NEGATIVE_CLAUSES[excluded][caseIndex % 4]}"
                ),
                requiredAttributes=[required],
                excludedAttributes=[excluded],
            )
        )

    # Contradictions must abstain instead of silently choosing one instruction.
    contradictionFeatures = [*features, *features, *features, *features[:3]]
    for caseIndex, feature in enumerate(contradictionFeatures, start=1):
        cases.append(
            RetrievalEvaluationCase(
                name=f"regression-300-contradiction-{caseIndex}",
                query=(
                    f"Find a pub that {FEATURE_CLAUSES[feature][caseIndex % 7]} but "
                    f"{NEGATIVE_CLAUSES[feature][caseIndex % 4]}"
                ),
                expectNoResults=True,
            )
        )

    if len(cases) != 300:
        raise RuntimeError(f"Expected 300 regression cases, generated {len(cases)}")
    return cases


def writeSuite(cases: list[RetrievalEvaluationCase], outputPath: Path, manifestPath: Path) -> None:
    """Write stable JSON plus a checksum manifest that labels the suite correctly."""
    output = TypeAdapter(list[RetrievalEvaluationCase]).dump_json(cases, indent=2).decode() + "\n"
    outputPath.write_text(output, encoding="utf-8")
    manifest = {
        "suite": "grounded-rag-regression-300-v1",
        "createdAt": "2026-08-12",
        "caseCount": len(cases),
        "sha256": hashlib.sha256(output.encode()).hexdigest(),
        "composition": {
            "positiveSingleFeature": 126,
            "negativeSingleFeature": 72,
            "positiveMultiConstraint": 54,
            "mixedRequiredAndExcluded": 18,
            "contradictions": 30,
        },
        "policy": (
            "Deterministic transparent-template regression suite. It may be used for development "
            "and must not be reported as an untouched holdout or independent generalisation test."
        ),
    }
    manifestPath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    writeSuite(buildRegressionCases(), args.output, args.manifest)
    print(f"Created 300 regression cases at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
