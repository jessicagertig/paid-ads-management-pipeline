#!/usr/bin/env python3
"""Non-destructive probe: can this developer token mutate negative keywords?

Sends a real MutateSharedCriteria request with validate_only=True. Google
validates the operation and returns errors without executing it, so nothing
is added to the account.
"""

import sys
from pathlib import Path

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

ENV_FILE = Path(__file__).parent.parent / ".env"
SHARED_SET_ID = "12170618983"
PROBE_TERM = "zzz validate only probe do not add"


def load_env() -> dict:
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def main() -> None:
    env = load_env()
    customer_id = env["GOOGLE_ADS_CUSTOMER_ID"]
    config = {
        "developer_token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }
    client = GoogleAdsClient.load_from_dict(config, version="v25")
    shared_criterion_service = client.get_service("SharedCriterionService")
    shared_set_service = client.get_service("SharedSetService")

    shared_set_resource_name = shared_set_service.shared_set_path(customer_id, SHARED_SET_ID)

    operations = []
    for match_type in ("EXACT", "PHRASE"):
        operation = client.get_type("SharedCriterionOperation")
        shared_criterion = operation.create
        shared_criterion.shared_set = shared_set_resource_name
        shared_criterion.keyword.text = PROBE_TERM
        shared_criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum[match_type]
        operations.append(operation)

    request = client.get_type("MutateSharedCriteriaRequest")
    request.customer_id = customer_id
    request.operations.extend(operations)
    request.validate_only = True

    try:
        shared_criterion_service.mutate_shared_criteria(request=request)
    except GoogleAdsException as e:
        print("WRITE PROBE FAILED")
        for error in e.failure.errors:
            print(f"  error_code: {str(error.error_code).strip()}")
            print(f"  message:    {error.message}")
        sys.exit(1)

    print("WRITE PROBE PASSED — validate_only mutate accepted, nothing written.")


if __name__ == "__main__":
    main()
