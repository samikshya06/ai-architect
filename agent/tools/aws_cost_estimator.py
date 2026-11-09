"""Deterministic estimates from the public, current AWS price lists."""

import shutil
import tempfile
from collections import defaultdict
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import ijson

from schemas.capacity import CapacityMetric
from schemas.components import AwsDesign, DynamoDb, Ec2, ElastiCache, Rds

HOURS_PER_MONTH = Decimal(730)
OFFER_CODES = {
    "ec2": "AmazonEC2",
    "rds": "AmazonRDS",
    "elasticache": "AmazonElastiCache",
}
PAYMENTS = ("Partial Upfront", "All Upfront")
TERMS = ("1yr", "3yr")
VARIABLE_SERVICES = {
    "fargate",
    "lambda",
    "api_gateway",
    "cloudfront",
    "dynamodb",
    "s3",
    "sqs",
    "sns",
    "cloudwatch",
    "data_transfer",
    "nat_gateway",
    "load_balancer",
}
MISSING = {
    "ebs": "Volume size and provisioned IOPS/throughput are not available.",
    "nat_gateway": "Gateway hours and data processing are not yet priced.",
    "fargate": "Fargate vCPU and memory charges are not yet priced.",
    "eks": "Cluster hours and any extended-support charges are not yet priced.",
    "lambda": "Invocation counts and execution durations are not available.",
    "api_gateway": "Request counts and other billable usage are not available.",
    "load_balancer": "Load balancer hours and capacity-unit usage are not yet priced.",
    "cloudfront": "Edge-specific transfer and request quantities are not available.",
    "s3": "Storage, request, and transfer quantities are not available.",
    "sqs": "Billable request counts and payload sizes are not available.",
    "sns": "Publish and delivery counts and protocols are not available.",
    "cloudwatch": "Log, metric, alarm, and other billable usage are not available.",
    "data_transfer": "Transfer volume and applicable tier allocation are not available.",
}


class PricingError(ValueError):
    """The live price list cannot safely price a component."""


def _product_matches(component, product):
    attrs = product.get("attributes", {})
    if attrs.get("regionCode") != component.region:
        return False
    usage = attrs.get("usagetype", "")
    if isinstance(component, Ec2):
        return (
            product.get("productFamily") == "Compute Instance"
            and attrs.get("instanceType") == component.instance_type
            and attrs.get("operatingSystem") == component.operating_system.capitalize()
            and attrs.get("tenancy") == "Shared"
            and attrs.get("preInstalledSw") == "NA"
            and attrs.get("capacitystatus") == "Used"
            and usage.endswith("BoxUsage:" + component.instance_type)
        )
    if isinstance(component, Rds):
        engines = {"postgres": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB"}
        return (
            product.get("productFamily") == "Database Instance"
            and attrs.get("instanceType") == component.instance_type
            and attrs.get("databaseEngine") == engines[component.engine]
            and attrs.get("deploymentOption")
            == ("Multi-AZ" if component.multi_az else "Single-AZ")
            and attrs.get("licenseModel") == "No license required"
            and usage.endswith("InstanceUsage:" + component.instance_type)
        )
    if isinstance(component, ElastiCache):
        engines = {"redis": "Redis", "valkey": "Valkey", "memcached": "Memcached"}
        return (
            product.get("productFamily") == "Cache Instance"
            and attrs.get("instanceType") == component.instance_type
            and attrs.get("cacheEngine") == engines[component.engine]
            and usage.endswith("NodeUsage:" + component.instance_type)
            and "ExtendedSupport" not in usage
        )
    return False


def _price_dimensions(term, reserved):
    hourly = []
    upfront = []
    for dim in term.get("priceDimensions", {}).values():
        unit = dim.get("unit")
        try:
            price = Decimal(dim["pricePerUnit"]["USD"])
        except (KeyError, InvalidOperation, TypeError) as exc:
            raise PricingError("Price dimension has no valid USD price") from exc
        if not price.is_finite() or price < 0:
            raise PricingError("Price dimension has an invalid USD price")
        if (
            unit == "Hrs"
            and dim.get("beginRange") == "0"
            and dim.get("endRange") == "Inf"
        ):
            hourly.append(price)
        elif reserved and unit == "Quantity":
            upfront.append(price)
        else:
            raise PricingError(f"Unsupported price dimension unit or range: {unit}")
    if (
        len(hourly) != 1
        or (reserved and len(upfront) != 1)
        or (not reserved and upfront)
    ):
        raise PricingError(
            "Expected one hourly rate and, for reservations, one upfront fee"
        )
    return hourly[0], upfront[0] if reserved else Decimal(0)


def _one_term(terms, *, length=None, payment=None):
    matches = []
    for term in terms.values():
        attrs = term.get("termAttributes", {})
        if length is None or (
            attrs.get("LeaseContractLength") == length
            and attrs.get("PurchaseOption") == payment
            and attrs.get("OfferingClass") == "standard"
        ):
            matches.append(term)
    if len(matches) != 1:
        raise PricingError(f"Expected one matching price term; found {len(matches)}")
    return matches[0]


def _fetch_prices(offer, region, components):
    """Fetch once per service/region; stream the large JSON into a temporary file."""
    url = f"https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{offer}/current/{region}/index.json"
    with urlopen(url, timeout=120) as response, tempfile.TemporaryFile() as price_file:
        shutil.copyfileobj(response, price_file)
        price_file.seek(0)
        publication = next(ijson.items(price_file, "publicationDate"), None)
        if not publication:
            raise PricingError("AWS price list has no publication date")
        price_file.seek(0)
        matches = defaultdict(list)
        for sku, product in ijson.kvitems(price_file, "products"):
            for component in components:
                if _product_matches(component, product):
                    matches[component.id].append(sku)
        skus = {sku for values in matches.values() for sku in values}
        prices = {sku: {} for sku in skus}
        for term_type in ("OnDemand", "Reserved"):
            price_file.seek(0)
            for sku, terms in ijson.kvitems(price_file, "terms." + term_type):
                if sku in skus:
                    prices[sku][term_type] = terms
    return url, publication, matches, prices


def _money(value):
    return str(value.quantize(Decimal("0.0001")))


def _dynamodb_request_rates(region, table_class):
    url = f"https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonDynamoDB/current/{region}/index.json"
    suffix = "IA" if table_class == "standard_ia" else ""
    groups = {"read": "DDB-ReadUnits" + suffix, "write": "DDB-WriteUnits" + suffix}
    with urlopen(url, timeout=120) as response, tempfile.TemporaryFile() as price_file:
        shutil.copyfileobj(response, price_file)
        price_file.seek(0)
        publication = next(ijson.items(price_file, "publicationDate"), None)
        if not publication:
            raise PricingError("AWS price list has no publication date")
        price_file.seek(0)
        matches = {key: [] for key in groups}
        for sku, product in ijson.kvitems(price_file, "products"):
            attrs = product.get("attributes", {})
            if (
                product.get("productFamily") == "Amazon DynamoDB PayPerRequest Throughput"
                and attrs.get("regionCode") == region
                and attrs.get("operation") == "PayPerRequestThroughput"
            ):
                for key, group in groups.items():
                    if attrs.get("group") == group:
                        matches[key].append(sku)
        for key, found in matches.items():
            if len(found) != 1:
                raise PricingError(
                    f"Expected one DynamoDB {key} request product; found {len(found)}"
                )
        skus = {key: found[0] for key, found in matches.items()}
        terms_by_sku = {}
        price_file.seek(0)
        for sku, terms in ijson.kvitems(price_file, "terms.OnDemand"):
            if sku in skus.values():
                terms_by_sku[sku] = terms

    rates = {}
    sources = {}
    for key, sku in skus.items():
        term = _one_term(terms_by_sku.get(sku, {}))
        dimensions = list(term.get("priceDimensions", {}).values())
        expected_unit = "ReadRequestUnits" if key == "read" else "WriteRequestUnits"
        if (
            len(dimensions) != 1
            or dimensions[0].get("unit") != expected_unit
            or dimensions[0].get("beginRange") != "0"
            or dimensions[0].get("endRange") != "Inf"
        ):
            raise PricingError(f"Unsupported DynamoDB {key} price dimensions")
        try:
            rate = Decimal(dimensions[0]["pricePerUnit"]["USD"])
        except (KeyError, InvalidOperation, TypeError) as exc:
            raise PricingError(f"Invalid DynamoDB {key} USD price") from exc
        if not rate.is_finite() or rate < 0:
            raise PricingError(f"Invalid DynamoDB {key} USD price")
        rates[key] = rate
        sources[key] = {"sku": sku, "effective_date": term.get("effectiveDate")}
    return url, publication, rates, sources


def _estimate_dynamodb(component, design, capacity_results, result, scenarios):
    result["issues"].append(
        "DynamoDB storage, indexes, backups, replication, and other charges are excluded."
    )
    if component.capacity_mode != "on_demand":
        result["issues"].append("DynamoDB provisioned capacity is not yet priced.")
        return
    if (
        sum(isinstance(c, DynamoDb) for c in design.components) != 1
        or len({c.region for c in design.components}) != 1
    ):
        result["issues"].append(
            "DynamoDB workload allocation is unavailable for multiple tables or regions."
        )
        return
    rows = [CapacityMetric.model_validate(row) for row in capacity_results]
    metrics = {}
    for name in ("peak_read_qps", "peak_write_qps"):
        matching = [row for row in rows if row.metric == name]
        if len(matching) != 1:
            result["issues"].append(f"Expected one {name} capacity metric; found {len(matching)}.")
            return
        row = matching[0]
        if row.unit != "qps" or row.item_size_kb is None or row.item_size_kb > 400:
            result["issues"].append(
                f"{name} requires qps and a representative item size between 0 and 400 KB."
            )
            return
        metrics[name] = row

    read, write = metrics["peak_read_qps"], metrics["peak_write_qps"]
    seconds = HOURS_PER_MONTH * Decimal(3600)
    read_count = Decimal(str(read.value)) * Decimal("0.1") * seconds
    write_count = Decimal(str(write.value)) * Decimal("0.1") * seconds
    read_blocks = (Decimal(str(read.item_size_kb)) / 4).to_integral_value(rounding=ROUND_CEILING)
    write_blocks = Decimal(str(write.item_size_kb)).to_integral_value(rounding=ROUND_CEILING)
    read_units = read_count * read_blocks * Decimal("0.75")
    write_units = write_count * write_blocks
    result["usage_assumptions"] = [
        "Average read and write rates are 10% of their respective peak QPS.",
        "Each application read or write maps to one DynamoDB read or write.",
        "50% of reads are strongly consistent and 50% are eventually consistent.",
        "Each read and write uses the capacity metric's representative item size.",
        "All modeled workload is allocated to this single DynamoDB component and region.",
        "Single-item non-transactional operations; no index or replicated writes.",
    ]
    result["estimated_usage"] = {
        "hours_per_month": str(HOURS_PER_MONTH),
        "peak_read_qps": str(read.value),
        "peak_write_qps": str(write.value),
        "read_item_size_kb": str(read.item_size_kb),
        "write_item_size_kb": str(write.item_size_kb),
        "monthly_read_operations": str(read_count),
        "monthly_write_operations": str(write_count),
        "monthly_read_request_units": str(read_units),
        "monthly_write_request_units": str(write_units),
    }
    try:
        url, publication, rates, sources = _dynamodb_request_rates(
            component.region, component.table_class
        )
    except (HTTPError, URLError, TimeoutError, OSError, ijson.JSONError, PricingError) as exc:
        result["issues"].append(f"Live AWS price lookup failed: {exc}")
        return
    cost = read_units * rates["read"] + write_units * rates["write"]
    result["price_source"] = {"url": url, "publication_date": publication, "request_units": sources}
    result["rates"] = {f"{key}_usd_per_request_unit": str(rate) for key, rate in rates.items()}
    result["monthly_usd"] = {scenario: _money(cost) for scenario in scenarios}


def estimate_aws_cost(design: AwsDesign, capacity_results: list[dict] | None = None) -> dict:
    """Return partial, clearly labeled estimates based on selected and assumed usage."""
    scenarios = ["on_demand"] + [
        f"reserved_{length}_{payment.lower().replace(' ', '_')}"
        for length in TERMS
        for payment in PAYMENTS
    ]
    totals = {scenario: Decimal(0) for scenario in scenarios}
    complete = {scenario: True for scenario in scenarios}
    results = {}
    grouped = defaultdict(list)
    for component in design.components:
        result = {
            "component_id": component.id,
            "service": component.service,
            "region": component.region,
            "cost_profile": "variable"
            if component.service in VARIABLE_SERVICES
            else "predictable",
            "profile_reason": (
                "Usage or traffic can fluctuate; spikes may increase charges."
                if component.service in VARIABLE_SERVICES
                else "Selected capacity is fixed; unmodeled usage and configuration can still change costs."
            ),
            "monthly_usd": {},
            "issues": [],
        }
        results[component.id] = result
        if component.service in OFFER_CODES:
            grouped[(OFFER_CODES[component.service], component.region)].append(
                component
            )
            if component.service == "rds":
                result["issues"].append(
                    "RDS storage, backups, I/O, and extended support are excluded."
                )
            if component.service == "ec2":
                result["issues"].append(
                    "EC2 storage, network, CPU credits, and other extras are excluded."
                )
            if component.service == "elasticache":
                result["issues"].append(
                    "ElastiCache backups, data transfer, and extended support are excluded."
                )
        elif isinstance(component, DynamoDb):
            _estimate_dynamodb(component, design, capacity_results or [], result, scenarios)
        else:
            result["issues"].append(MISSING[component.service])

    for (offer, region), components in grouped.items():
        try:
            url, publication, matches, prices = _fetch_prices(offer, region, components)
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            ijson.JSONError,
            PricingError,
        ) as exc:
            for component in components:
                results[component.id]["issues"].append(
                    f"Live AWS price lookup failed: {exc}"
                )
            continue
        for component in components:
            result = results[component.id]
            result["price_source"] = {"url": url, "publication_date": publication}
            found = matches[component.id]
            if len(found) != 1:
                result["issues"].append(
                    f"Expected one matching AWS product; found {len(found)}."
                )
                continue
            sku = found[0]
            result["price_source"]["sku"] = sku
            for scenario in scenarios:
                try:
                    if scenario == "on_demand":
                        term = _one_term(prices[sku].get("OnDemand", {}))
                        hourly, upfront = _price_dimensions(term, False)
                        months = 1
                    else:
                        _, length, payment_key = scenario.split("_", 2)
                        payment = payment_key.replace("_", " ").title()
                        term = _one_term(
                            prices[sku].get("Reserved", {}),
                            length=length,
                            payment=payment,
                        )
                        hourly, upfront = _price_dimensions(term, True)
                        months = 12 if length == "1yr" else 36
                    cost = component.count * (
                        hourly * HOURS_PER_MONTH + upfront / months
                    )
                    result["monthly_usd"][scenario] = _money(cost)
                    result.setdefault("rates", {})[scenario] = {
                        "hourly_usd_per_instance": str(hourly),
                        "upfront_usd_per_instance": str(upfront),
                    }
                    if scenario != "on_demand":
                        result.setdefault("reservation_upfront_usd", {})[scenario] = (
                            _money(component.count * upfront)
                        )
                    result.setdefault("term_effective_dates", {})[scenario] = term.get(
                        "effectiveDate"
                    )
                except PricingError as exc:
                    result["issues"].append(f"{scenario}: {exc}")

    for result in results.values():
        for scenario in scenarios:
            value = result["monthly_usd"].get(scenario)
            if value is not None:
                totals[scenario] += Decimal(value)
            if value is None or result["issues"]:
                complete[scenario] = False
    return {
        "currency": "USD",
        "assumptions": [
            "730 hours per month; selected EC2, RDS and ElastiCache counts run continuously.",
            "All selected eligible instances are assumed steady-state and reserved for the full term.",
            "Standard reserved instances; upfront fees amortized over 12 or 36 months.",
            "Public list prices exclude discounts, taxes, free tier, and unmodeled charges.",
        ],
        "scenarios": {
            scenario: {
                "known_monthly_usd": _money(totals[scenario]),
                "complete": complete[scenario],
            }
            for scenario in scenarios
        },
        "components": list(results.values()),
    }
