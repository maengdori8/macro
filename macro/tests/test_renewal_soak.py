from __future__ import annotations

import unittest

from macroapp.renewal import PriceState, RenewalPriceClassifier
from macroapp.renewal_soak import (
    _changed_corpus,
    _none_state,
    _price,
    _same_price_corpus,
)


class RenewalSoakTests(unittest.TestCase):
    def test_same_corpus_allows_fail_closed_but_never_false_change(
        self,
    ) -> None:
        baseline = _price("777")
        classifier = RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        same_corpus = _same_price_corpus(baseline)
        states = [
            classifier.classify(candidate).state
            for candidate in same_corpus
        ]

        self.assertTrue(states)
        self.assertTrue(
            all(
                state in (PriceState.UNCHANGED, PriceState.AMBIGUOUS)
                for state in states
            )
        )
        self.assertNotIn(PriceState.CHANGED, states)
        self.assertTrue(
            _none_state(
                classifier,
                same_corpus,
                PriceState.CHANGED,
            )
        )

    def test_changed_soak_corpus_remains_detectable(self) -> None:
        baseline = _price("777")
        classifier = RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        states = [
            classifier.classify(candidate).state
            for candidate in _changed_corpus(_price("635"))
        ]
        self.assertTrue(states)
        self.assertTrue(
            all(state is PriceState.CHANGED for state in states)
        )


if __name__ == "__main__":
    unittest.main()
