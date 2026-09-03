#!/usr/bin/env python3
"""Name each accessible Google Ads account and say whether it is a manager."""

import sys
from pathlib import Path

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

ENV_FILE = Path(__file__).parent.parent / ".env"

QUERY = """
    SELECT
      customer.id,
      customer.descriptive_name,
      customer.manager,
      customer.test_account,
      customer.currency_code,
      customer.time_zone,
      customer.status
    FROM customer
    LIMIT 1
"""


def load_env() -> dict:
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def main() -> None:
    env = load_env()
    config = {
        "developer_token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }

    client = GoogleAdsClient.load_from_dict(config, version="v25")
    customer_service = client.get_service("CustomerService")
    google_ads_service = client.get_service("GoogleAdsService")

    resource_names = customer_service.list_accessible_customers().resource_names

    for resource_name in resource_names:
        customer_id = resource_name.split("/")[-1]
        try:
            rows = google_ads_service.search(customer_id=customer_id, query=QUERY)
            for row in rows:
                c = row.customer
                kind = "MANAGER" if c.manager else "client"
                test = " TEST" if c.test_account else ""
                print(f"{c.id}  {kind}{test}  {c.descriptive_name!r}  {c.currency_code}  {c.time_zone}  {c.status.name}")
        except GoogleAdsException as e:
            codes = "; ".join(str(err.error_code).strip() for err in e.failure.errors)
            msgs = "; ".join(err.message for err in e.failure.errors)
            print(f"{customer_id}  FAILED  {codes} | {msgs}")


if __name__ == "__main__":
    main()
