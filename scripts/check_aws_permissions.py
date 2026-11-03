#!/usr/bin/env python3
"""Check the IAM user in config/secrets/secrets.env against the portal docs.

The capability list follows the README: a Route 53 domain, a private S3
site, CloudFront, and an ACM certificate in us-east-1. The queue is local.
The local app uploads the latest queue HTML to S3. There is no API Gateway,
Lambda, DynamoDB, or CloudWatch.

This script does not create, buy, or change anything. Domain registration is
checked with IAM simulation and with read-only Route 53 Domains calls only.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    sys.exit(
        "boto3 is not installed for this Python. "
        "Run it with the interview_helper_mux virtualenv, for example:\n"
        "  ../interview_helper_mux/.venv/bin/python scripts/check_aws_permissions.py"
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
SECRETS_PATH = REPO_ROOT / "config" / "secrets" / "secrets.env"
REGION = "us-east-1"

POLICY_FOR_PREFIX = {
    "route53": "AmazonRoute53FullAccess",
    "route53domains": "AmazonRoute53FullAccess",
    "s3": "AmazonS3FullAccess",
    "acm": "AWSCertificateManagerFullAccess",
    "cloudfront": "CloudFrontFullAccess",
}


def load_secrets(path: Path) -> dict[str, str]:
    if not path.is_file():
        sys.exit(f"Missing secrets file: {path}")
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    access_key = values.get("AWS_ACCESS_KEY_ID", "")
    secret_key = values.get("AWS_SECRET_ACCESS_KEY", "")
    if not access_key or not secret_key:
        sys.exit(f"{path} needs AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.")
    region = values.get("AWS_DEFAULT_REGION") or REGION
    return {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "aws_session_token": values.get("AWS_SESSION_TOKEN") or None,
        "region_name": region,
    }


def capabilities(account_id: str) -> list[dict]:
    """Actions Terraform needs. Resources match what the AWS provider sends."""
    bucket = "arn:aws:s3:::portal-permission-probe"
    object_arn = f"{bucket}/*"
    cert = f"arn:aws:acm:{REGION}:{account_id}:certificate/*"
    distribution = f"arn:aws:cloudfront::{account_id}:distribution/*"
    zone = "arn:aws:route53:::hostedzone/*"
    return [
        {
            "name": "Route 53 DNS for the site domain",
            "doc": "Hosted zone and records that point the domain at CloudFront",
            "actions": [
                ("route53:CreateHostedZone", "*"),
                ("route53:GetHostedZone", zone),
                ("route53:ListHostedZones", "*"),
                ("route53:ChangeResourceRecordSets", zone),
                ("route53:GetChange", "*"),
                ("route53:ListResourceRecordSets", zone),
                ("route53:DeleteHostedZone", zone),
            ],
        },
        {
            "name": "Route 53 domain registration",
            "doc": "Check, price, and register a new domain. This script never registers one.",
            "actions": [
                ("route53domains:CheckDomainAvailability", "*"),
                ("route53domains:ListPrices", "*"),
                ("route53domains:RegisterDomain", "*"),
                ("route53domains:GetOperationDetail", "*"),
                ("route53domains:ListDomains", "*"),
                ("route53domains:GetDomainDetail", "*"),
                ("route53domains:UpdateDomainNameservers", "*"),
            ],
        },
        {
            "name": "ACM certificate in us-east-1",
            "doc": "CloudFront only reads certificates from us-east-1",
            "actions": [
                ("acm:RequestCertificate", "*"),
                ("acm:DescribeCertificate", cert),
                ("acm:ListCertificates", "*"),
                ("acm:DeleteCertificate", cert),
                ("acm:AddTagsToCertificate", cert),
            ],
        },
        {
            "name": "Private S3 site bucket",
            "doc": "Private origin. The local app overwrites the queue HTML here on a period.",
            "actions": [
                ("s3:CreateBucket", bucket),
                ("s3:PutBucketPolicy", bucket),
                ("s3:GetBucketPolicy", bucket),
                ("s3:DeleteBucketPolicy", bucket),
                ("s3:PutBucketPublicAccessBlock", bucket),
                ("s3:PutBucketOwnershipControls", bucket),
                ("s3:PutBucketTagging", bucket),
                ("s3:PutObject", object_arn),
                ("s3:GetObject", object_arn),
                ("s3:DeleteObject", object_arn),
                ("s3:ListBucket", bucket),
                ("s3:DeleteBucket", bucket),
            ],
        },
        {
            "name": "CloudFront distribution",
            "doc": "HTTPS and origin access control for the static site",
            "actions": [
                ("cloudfront:CreateOriginAccessControl", "*"),
                ("cloudfront:GetOriginAccessControl", "*"),
                ("cloudfront:DeleteOriginAccessControl", "*"),
                ("cloudfront:CreateDistribution", "*"),
                ("cloudfront:GetDistribution", distribution),
                ("cloudfront:UpdateDistribution", distribution),
                ("cloudfront:DeleteDistribution", distribution),
                ("cloudfront:CreateInvalidation", distribution),
                ("cloudfront:ListDistributions", "*"),
                ("cloudfront:TagResource", "*"),
                ("cloudfront:GetCachePolicy", "*"),
                ("cloudfront:GetOriginRequestPolicy", "*"),
            ],
        },
    ]


def decision(result: dict) -> str:
    if result.get("EvalDecision") == "allowed":
        return "allowed"
    return "denied"


def simulate(iam, principal_arn: str, actions: list[tuple[str, str]]) -> list[dict]:
    """One resource per call. Mixing ARNs makes IAM evaluate every action on every ARN."""
    grouped: dict[str, list[str]] = {}
    for action, resource in actions:
        grouped.setdefault(resource, []).append(action)
    context = [
        {
            "ContextKeyName": "iam:PassedToService",
            "ContextKeyValues": ["lambda.amazonaws.com"],
            "ContextKeyType": "string",
        }
    ]
    rows: list[dict] = []
    for resource, action_names in grouped.items():
        response = iam.simulate_principal_policy(
            PolicySourceArn=principal_arn,
            ActionNames=action_names,
            ResourceArns=[resource],
            ContextEntries=context,
        )
        rows.extend(response["EvaluationResults"])
    by_action = {row["EvalActionName"]: row for row in rows}
    missing = [action for action, _resource in actions if action not in by_action]
    if missing:
        raise RuntimeError(f"IAM simulation omitted actions: {', '.join(missing)}")
    return [by_action[action] for action, _resource in actions]


def attached_policies(iam, user_name: str) -> list[str]:
    names: list[str] = []
    attached = iam.get_paginator("list_attached_user_policies").paginate(UserName=user_name)
    for page in attached:
        names.extend(item["PolicyName"] for item in page["AttachedPolicies"])
    groups = iam.list_groups_for_user(UserName=user_name)["Groups"]
    for group in groups:
        group_policies = iam.get_paginator("list_attached_group_policies").paginate(
            GroupName=group["GroupName"]
        )
        for page in group_policies:
            for item in page["AttachedPolicies"]:
                names.append(f"{item['PolicyName']} (group {group['GroupName']})")
    return names


def live_probes(session) -> list[tuple[str, str, str]]:
    """Read-only calls. A deny here can mean an SCP even when simulation says allowed."""
    route53 = session.client("route53", region_name=REGION)
    domains = session.client("route53domains", region_name=REGION)
    s3 = session.client("s3", region_name=REGION)
    acm = session.client("acm", region_name=REGION)
    cloudfront = session.client("cloudfront", region_name=REGION)
    probes = [
        ("Route 53 ListHostedZones", lambda: route53.list_hosted_zones(MaxItems="1")),
        ("Route 53 Domains ListDomains", lambda: domains.list_domains(MaxItems=1)),
        (
            "Route 53 Domains CheckDomainAvailability",
            lambda: domains.check_domain_availability(
                DomainName="permission-probe-not-for-sale-9f3c2a.com"
            ),
        ),
        ("S3 ListBuckets", lambda: s3.list_buckets()),
        ("ACM ListCertificates us-east-1", lambda: acm.list_certificates(MaxItems=1)),
        ("CloudFront ListDistributions", lambda: cloudfront.list_distributions(MaxItems="1")),
    ]
    rows: list[tuple[str, str, str]] = []
    for label, call in probes:
        try:
            call()
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "ClientError")
            message = error.response.get("Error", {}).get("Message", "")
            rows.append((label, code, message))
        except BotoCoreError as error:
            rows.append((label, "BotoCoreError", str(error)))
        else:
            rows.append((label, "ok", ""))
    return rows


def main() -> int:
    secrets = load_secrets(SECRETS_PATH)
    session = boto3.Session(
        aws_access_key_id=secrets["aws_access_key_id"],
        aws_secret_access_key=secrets["aws_secret_access_key"],
        aws_session_token=secrets["aws_session_token"],
        region_name=secrets["region_name"],
    )
    sts = session.client("sts", region_name=REGION)
    try:
        identity = sts.get_caller_identity()
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "ClientError")
        message = error.response.get("Error", {}).get("Message", "")
        print(f"Credentials were rejected: {code}: {message}")
        return 1

    account_id = identity["Account"]
    principal_arn = identity["Arn"]
    print(f"Account:  {account_id}")
    print(f"Principal: {principal_arn}")
    print(f"Region checked for regional services: {REGION}")
    print()

    if ":user/" not in principal_arn:
        print("These credentials are not an IAM user, so attached-user policies were not listed.")
        print("Simulation still runs against the principal ARN.")
        policy_names: list[str] = []
    else:
        user_name = principal_arn.split(":user/", 1)[1]
        iam = session.client("iam", region_name=REGION)
        try:
            policy_names = attached_policies(iam, user_name)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "ClientError")
            message = error.response.get("Error", {}).get("Message", "")
            print(f"Could not list attached policies: {code}: {message}")
            policy_names = []
        else:
            print("Attached policies:")
            for name in policy_names or ["(none)"]:
                print(f"  - {name}")
            print()

    iam = session.client("iam", region_name=REGION)
    denied_actions: list[str] = []
    print("IAM simulation (no resources are created):")
    try:
        for capability in capabilities(account_id):
            results = simulate(iam, principal_arn, capability["actions"])
            denied = [row["EvalActionName"] for row in results if decision(row) != "allowed"]
            denied_actions.extend(denied)
            status = "allowed" if not denied else "MISSING"
            print(f"  [{status}] {capability['name']}")
            print(f"           {capability['doc']}")
            for action in denied:
                print(f"           deny {action}")
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "ClientError")
        message = error.response.get("Error", {}).get("Message", "")
        print(f"IAM simulation failed: {code}: {message}")
        return 1

    print()
    print("Live read-only calls:")
    live_denied = False
    for label, code, message in live_probes(session):
        if code == "ok":
            print(f"  [ok] {label}")
            continue
        live_denied = True
        detail = f": {message}" if message else ""
        print(f"  [FAIL] {label} — {code}{detail}")

    print()
    if not denied_actions and not live_denied:
        print("This user can perform the documented AWS setup.")
        print("Domain registration is permitted. It still charges the account and needs a payment method.")
        print("This script did not register a domain or create any resource.")
        return 0

    prefixes = []
    for action in denied_actions:
        prefix = action.split(":", 1)[0]
        if prefix not in prefixes:
            prefixes.append(prefix)
    needed = []
    for prefix in prefixes:
        policy = POLICY_FOR_PREFIX.get(prefix)
        if policy and policy not in needed:
            needed.append(policy)

    print("Additional permissions needed before Terraform can build the portal:")
    if needed:
        for policy in needed:
            print(f"  - {policy}")
    else:
        print("  - Simulation found no IAM gaps. Live calls failed; check SCPs or the error above.")
    if live_denied and not denied_actions:
        print("Live calls failed where simulation allowed them. Trust the live error; a service control policy can block what simulation allows.")
    print("This script did not register a domain or create any resource.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
