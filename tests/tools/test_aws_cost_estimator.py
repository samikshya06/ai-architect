import io
import json
from urllib.error import URLError

import pytest

from agent.tools import aws_cost_estimator as estimator
from schemas import ComponentChoices


def component(service, **fields):
    return {
        "id": service,
        "service": service,
        "region": "us-east-1",
        "rationale": "Selected to satisfy the stated application requirements.",
        "based_on": ["requirement:daily_active_users"],
        **fields,
    }


def term(hourly, upfront=None, **attributes):
    dimensions = {
        "hourly": {
            "unit": "Hrs",
            "beginRange": "0",
            "endRange": "Inf",
            "pricePerUnit": {"USD": str(hourly)},
        }
    }
    if upfront is not None:
        dimensions["upfront"] = {
            "unit": "Quantity",
            "pricePerUnit": {"USD": str(upfront)},
        }
    return {
        "effectiveDate": "2026-01-01T00:00:00Z",
        "termAttributes": attributes,
        "priceDimensions": dimensions,
    }


def card():
    reserved = {}
    for length, months in (("1yr", 12), ("3yr", 36)):
        for payment, hourly, upfront in (
            ("Partial Upfront", "0.5", months * 10),
            ("All Upfront", "0", months * 30),
        ):
            reserved[length + payment] = term(
                hourly,
                upfront,
                LeaseContractLength=length,
                PurchaseOption=payment,
                OfferingClass="standard",
            )
    return {
        "publicationDate": "2026-01-02T00:00:00Z",
        "products": {
            "instance": {
                "productFamily": "Compute Instance",
                "attributes": {
                    "regionCode": "us-east-1",
                    "instanceType": "m7i.large",
                    "operatingSystem": "Linux",
                    "tenancy": "Shared",
                    "preInstalledSw": "NA",
                    "capacitystatus": "Used",
                    "usagetype": "BoxUsage:m7i.large",
                },
            }
        },
        "terms": {
            "OnDemand": {"instance": {"ondemand": term("1")}},
            "Reserved": {"instance": reserved},
        },
    }


def response(data):
    return io.BytesIO(json.dumps(data).encode())


def test_live_card_parsing_and_scenario_calculation(monkeypatch):
    urls = []

    def open_card(url, timeout):
        urls.append(url)
        return response(card())

    monkeypatch.setattr(estimator, "urlopen", open_card)
    design = ComponentChoices.model_validate(
        {
            "components": [
                component("ec2", instance_type="m7i.large", count=2),
                component("data_transfer", direction="internet"),
            ]
        }
    )
    estimate = estimator.estimate_aws_cost(design)
    ec2, transfer = estimate["components"]

    assert len(urls) == 1
    assert "/AmazonEC2/current/us-east-1/" in urls[0]
    assert ec2["price_source"]["sku"] == "instance"
    assert ec2["rates"]["reserved_1yr_partial_upfront"] == {
        "hourly_usd_per_instance": "0.5",
        "upfront_usd_per_instance": "120",
    }
    assert ec2["monthly_usd"] == {
        "on_demand": "1460.0000",
        "reserved_1yr_partial_upfront": "750.0000",
        "reserved_1yr_all_upfront": "60.0000",
        "reserved_3yr_partial_upfront": "750.0000",
        "reserved_3yr_all_upfront": "60.0000",
    }
    assert ec2["reservation_upfront_usd"]["reserved_3yr_all_upfront"] == "2160.0000"
    assert estimate["scenarios"]["on_demand"] == {
        "known_monthly_usd": "1460.0000",
        "complete": False,
    }
    assert transfer["cost_profile"] == "variable"
    assert not transfer["monthly_usd"]
    assert "Transfer volume" in transfer["issues"][0]


def test_missing_reserved_term_does_not_fall_back_to_on_demand(monkeypatch):
    data = card()
    data["terms"]["Reserved"]["instance"].clear()
    monkeypatch.setattr(estimator, "urlopen", lambda *args, **kwargs: response(data))
    design = ComponentChoices.model_validate(
        {"components": [component("ec2", instance_type="m7i.large")]}
    )
    result = estimator.estimate_aws_cost(design)
    assert result["components"][0]["monthly_usd"] == {"on_demand": "730.0000"}
    assert result["scenarios"]["reserved_1yr_all_upfront"]["complete"] is False
    assert "reserved_1yr_all_upfront" in " ".join(result["components"][0]["issues"])


def test_ambiguous_products_and_network_failure_are_visible(monkeypatch):
    data = card()
    data["products"]["other"] = data["products"]["instance"]
    monkeypatch.setattr(estimator, "urlopen", lambda *args, **kwargs: response(data))
    design = ComponentChoices.model_validate(
        {"components": [component("ec2", instance_type="m7i.large")]}
    )
    result = estimator.estimate_aws_cost(design)
    assert "found 2" in " ".join(result["components"][0]["issues"])
    assert not result["components"][0]["monthly_usd"]

    def fail(*args, **kwargs):
        raise URLError("unavailable")

    monkeypatch.setattr(estimator, "urlopen", fail)
    result = estimator.estimate_aws_cost(design)
    assert "unavailable" in " ".join(result["components"][0]["issues"])
    assert result["scenarios"]["on_demand"]["complete"] is False


@pytest.mark.parametrize("service", list(estimator.MISSING))
def test_other_components_are_explicitly_unestimated(service):
    fields = {
        "fargate": {"vcpu": 1, "memory_gb": 2},
        "lambda": {"memory_mb": 512},
        "api_gateway": {"api_type": "http"},
        "load_balancer": {"lb_type": "application"},
        "cloudfront": {"edge_geography": "india"},
        "data_transfer": {"direction": "internet"},
    }.get(service, {})
    design = ComponentChoices.model_validate(
        {"components": [component(service, **fields)]}
    )
    result = estimator.estimate_aws_cost(design)
    assert result["components"][0]["issues"]
    assert result["components"][0]["monthly_usd"] == {}
    assert not result["scenarios"]["on_demand"]["complete"]


def test_reservation_rejects_tiered_or_unexpected_dimensions():
    bad = term("1", 10)
    bad["priceDimensions"]["hourly"]["endRange"] = "100"
    with pytest.raises(estimator.PricingError):
        estimator._price_dimensions(bad, True)


def test_rds_and_cache_product_matching():
    design = ComponentChoices.model_validate(
        {
            "components": [
                component(
                    "rds", engine="postgres", instance_type="db.t3.micro", multi_az=True
                ),
                component(
                    "elasticache", engine="redis", instance_type="cache.t3.micro"
                ),
            ]
        }
    )
    db, cache = design.components
    db_product = {
        "productFamily": "Database Instance",
        "attributes": {
            "regionCode": "us-east-1",
            "instanceType": "db.t3.micro",
            "databaseEngine": "PostgreSQL",
            "deploymentOption": "Multi-AZ",
            "licenseModel": "No license required",
            "usagetype": "InstanceUsage:db.t3.micro",
        },
    }
    cache_product = {
        "productFamily": "Cache Instance",
        "attributes": {
            "regionCode": "us-east-1",
            "instanceType": "cache.t3.micro",
            "cacheEngine": "Redis",
            "usagetype": "NodeUsage:cache.t3.micro",
        },
    }
    assert estimator._product_matches(db, db_product)
    assert estimator._product_matches(cache, cache_product)
    cache_product["attributes"]["usagetype"] = (
        "ExtendedSupport-NodeUsage:cache.t3.micro"
    )
    assert not estimator._product_matches(cache, cache_product)
