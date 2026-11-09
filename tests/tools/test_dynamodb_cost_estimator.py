import io
import json
from urllib.error import URLError

import pytest

from agent.tools import aws_cost_estimator as estimator
from agent.tools.capacity_calculator import calculate_capacity
from schemas import ComponentChoices


def design(**fields):
    return ComponentChoices.model_validate({
        "components": [{
            "id": "messages",
            "service": "dynamodb",
            "region": "us-east-1",
            "rationale": "Stores the application's messaging records.",
            "based_on": ["requirement:daily_active_users"],
            **fields,
        }]
    })


def metrics():
    common = {
        "thread_id": "thread",
        "unit": "qps",
        "computed_from": "calculated peak",
        "drives": "database",
        "item_size_kb": 5,
    }
    return [
        {**common, "metric": "peak_read_qps", "value": 100},
        {**common, "metric": "peak_write_qps", "value": 200},
    ]


def card():
    products = {}
    terms = {}
    for suffix in ("", "IA"):
        for key, price in (("Read", "0.000000125"), ("Write", "0.000000625")):
            sku = key + suffix
            products[sku] = {
                "productFamily": "Amazon DynamoDB PayPerRequest Throughput",
                "attributes": {
                    "regionCode": "us-east-1",
                    "group": f"DDB-{key}Units{suffix}",
                    "operation": "PayPerRequestThroughput",
                },
            }
            terms[sku] = {"term": {
                "effectiveDate": "2026-01-01",
                "priceDimensions": {"price": {
                    "unit": f"{key}RequestUnits",
                    "beginRange": "0",
                    "endRange": "Inf",
                    "pricePerUnit": {"USD": price},
                }},
            }}
    return {
        "publicationDate": "2026-01-02",
        "products": products,
        "terms": {"OnDemand": terms},
    }


def install_card(monkeypatch, data):
    monkeypatch.setattr(
        estimator, "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(data).encode()),
    )


@pytest.mark.parametrize(("table_class", "suffix"), [("standard", ""), ("standard_ia", "IA")])
def test_dynamodb_request_pricing_uses_peak_fraction_and_read_mix(monkeypatch, table_class, suffix):
    install_card(monkeypatch, card())
    estimate = estimator.estimate_aws_cost(design(table_class=table_class), metrics())
    result = estimate["components"][0]

    assert result["estimated_usage"]["monthly_read_operations"] == "26280000.00"
    assert result["estimated_usage"]["monthly_read_request_units"] == "39420000.0000"
    assert result["estimated_usage"]["monthly_write_request_units"] == "262800000.00"
    assert set(result["monthly_usd"].values()) == {"169.1775"}
    assert result["price_source"]["request_units"]["read"]["sku"] == "Read" + suffix
    assert "50%" in result["usage_assumptions"][2]
    assert "storage" in result["issues"][0]
    assert estimate["scenarios"]["on_demand"] == {
        "known_monthly_usd": "169.1775", "complete": False,
    }
    assert all(not scenario["complete"] for scenario in estimate["scenarios"].values())


@pytest.mark.parametrize("change", ["missing", "duplicate", "unit", "size"])
def test_missing_or_unusable_capacity_is_not_priced(monkeypatch, change):
    rows = metrics()
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(rows[0])
    elif change == "unit":
        rows[0]["unit"] = "requests"
    else:
        rows[0].pop("item_size_kb")
    monkeypatch.setattr(estimator, "urlopen", lambda *args, **kwargs: pytest.fail("unexpected lookup"))
    result = estimator.estimate_aws_cost(design(), rows)["components"][0]
    assert not result["monthly_usd"]
    assert len(result["issues"]) == 2


def test_multiple_regions_or_dynamodb_components_are_not_assigned_global_workload(monkeypatch):
    d = design()
    second = d.components[0].model_copy(update={"id": "second"})
    d.components.append(second)
    monkeypatch.setattr(estimator, "urlopen", lambda *args, **kwargs: pytest.fail("unexpected lookup"))
    result = estimator.estimate_aws_cost(d, metrics())
    assert all(not component["monthly_usd"] for component in result["components"])
    assert "allocation" in result["components"][0]["issues"][1]

    second.region = "us-west-2"
    result = estimator.estimate_aws_cost(d, metrics())
    assert all(not component["monthly_usd"] for component in result["components"])


def test_provisioned_mode_is_explicitly_unestimated(monkeypatch):
    monkeypatch.setattr(estimator, "urlopen", lambda *args, **kwargs: pytest.fail("unexpected lookup"))
    result = estimator.estimate_aws_cost(design(capacity_mode="provisioned"), metrics())
    assert "provisioned" in result["components"][0]["issues"][1]
    assert not result["components"][0]["monthly_usd"]


def test_ambiguous_prices_and_network_errors_are_visible(monkeypatch):
    data = card()
    data["products"]["other"] = data["products"]["Read"]
    install_card(monkeypatch, data)
    result = estimator.estimate_aws_cost(design(), metrics())["components"][0]
    assert "found 2" in result["issues"][1]
    assert not result["monthly_usd"]

    def fail(*args, **kwargs):
        raise URLError("unavailable")

    monkeypatch.setattr(estimator, "urlopen", fail)
    result = estimator.estimate_aws_cost(design(), metrics())["components"][0]
    assert "unavailable" in result["issues"][1]
    assert not result["monthly_usd"]


def test_tiered_request_prices_are_not_silently_mispriced(monkeypatch):
    data = card()
    data["terms"]["OnDemand"]["Read"]["term"]["priceDimensions"]["price"]["endRange"] = "100"
    install_card(monkeypatch, data)
    result = estimator.estimate_aws_cost(design(), metrics())["components"][0]
    assert "Unsupported DynamoDB read price dimensions" in result["issues"][1]
    assert not result["monthly_usd"]


def test_actual_capacity_output_supplies_resolved_item_size(monkeypatch):
    install_card(monkeypatch, card())
    capacity = calculate_capacity({
        "system_type": "messaging / chat apps",
        "explicit_requirements": {"daily_active_users": 1000},
        "assumptions": {"avg_message_size_kb": 5},
    })
    result = estimator.estimate_aws_cost(design(), capacity)["components"][0]
    assert result["estimated_usage"]["read_item_size_kb"] == "5.0"
    assert result["estimated_usage"]["write_item_size_kb"] == "5.0"
    assert "on_demand" in result["monthly_usd"]


def test_invalid_numeric_capacity_is_reported():
    rows = metrics()
    rows[0]["value"] = float("nan")
    with pytest.raises(ValueError):
        estimator.estimate_aws_cost(design(), rows)
