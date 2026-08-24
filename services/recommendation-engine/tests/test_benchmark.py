"""Local benchmark helper tests."""

from app.benchmark import percentile95


def testPercentile95SupportsSmallSamples() -> None:
    assert percentile95([10.0]) == 10.0
    assert percentile95([1.0, 2.0, 3.0, 4.0, 5.0]) == 4.8
