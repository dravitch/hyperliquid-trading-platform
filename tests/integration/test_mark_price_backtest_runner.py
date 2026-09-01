import json

from hltrader.runners.backtest import (
    MarkPriceSemanticVerdict,
    classify_mark_price_semantics,
    run_mark_price_semantic_probe,
)


def test_classifier_confirms_only_native_trigger_with_divergent_quote() -> None:
    verdict = classify_mark_price_semantics(
        mark_update_observed=True,
        quote_crossed_trigger=False,
        native_trigger_fired=True,
        proxy_used=False,
    )
    assert verdict is MarkPriceSemanticVerdict.MARK_PRICE_EQUIVALENT_CONFIRMED


def test_classifier_discloses_proxy_even_if_trigger_fires() -> None:
    verdict = classify_mark_price_semantics(
        mark_update_observed=True,
        quote_crossed_trigger=True,
        native_trigger_fired=True,
        proxy_used=True,
    )
    assert verdict is MarkPriceSemanticVerdict.PROXY_USED


def test_classifier_is_unverifiable_without_sufficient_evidence() -> None:
    verdict = classify_mark_price_semantics(
        mark_update_observed=False,
        quote_crossed_trigger=True,
        native_trigger_fired=True,
        proxy_used=False,
    )
    assert verdict is MarkPriceSemanticVerdict.UNVERIFIABLE


def test_pinned_backtest_engine_does_not_silently_claim_mark_equivalence(tmp_path) -> None:
    report = run_mark_price_semantic_probe()

    assert report.nautilus_version == "1.231.0"
    assert report.mark_update_observed is True
    assert report.quote_crossed_trigger is False
    assert report.native_trigger_fired is False
    assert report.proxy_used is False
    assert report.verdict is MarkPriceSemanticVerdict.UNVERIFIABLE

    path = tmp_path / "mark-price-semantics.json"
    report.write_json(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["verdict"] == "UNVERIFIABLE"
