#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.9"
# dependencies = ["click>=8.1.0", "questionary>=2.0.1"]
# ///
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import shlex
import plistlib
import time
import uuid

import click
import questionary
from questionary import Choice


ACCESS_LEVELS = {
    "read-only": ["s3:ListBucket", "s3:GetObject"],
    "read-write": ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
    "full": ["s3:*"],
}


def run_aws(args, region=None, profile=None, allow_fail=False):
    cmd = ["aws"] + args
    if region:
        cmd += ["--region", region]
    if profile:
        cmd += ["--profile", profile]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as exc:
        if allow_fail:
            return None
        print(exc.output.strip(), file=sys.stderr)
        sys.exit(exc.returncode)
    return output


def ask_or_exit(prompt):
    answer = prompt.ask()
    if answer is None:
        sys.exit(1)
    return answer


def prompt_if_missing(value, message):
    if value:
        return value
    return ask_or_exit(questionary.text(message))


def parse_bucket_list(raw):
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def list_buckets(profile):
    output = run_aws(
        ["s3api", "list-buckets", "--query", "Buckets[].Name", "--output", "json"],
        profile=profile,
        allow_fail=True,
    )
    if not output:
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return []


def prompt_buckets(value, profile, allow_lookup=True, allow_empty=False):
    if value:
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, str):
            return parse_bucket_list(value)
        return list(value)
    if allow_lookup:
        buckets = list_buckets(profile)
        if buckets:
            done_label = "[Done] Finish selection"
            selected = []
            remaining = buckets[:]
            while True:
                choices = [done_label] + remaining
                choice = ask_or_exit(
                    questionary.autocomplete(
                        "Select source buckets (type to filter):",
                        choices=choices,
                    )
                )
                if choice == done_label:
                    if selected:
                        return selected
                    click.echo("Select at least one bucket before finishing.", err=True)
                    continue
                if choice in selected:
                    continue
                selected.append(choice)
                remaining = [name for name in buckets if name not in selected]
    manual = ask_or_exit(
        questionary.text("Source bucket name(s), comma-separated:")
    )
    buckets = parse_bucket_list(manual)
    if not buckets and allow_empty:
        return []
    return buckets


def list_aws_profiles():
    profiles = set()
    for filename in (Path.home() / ".aws" / "config", Path.home() / ".aws" / "credentials"):
        if not filename.exists():
            continue
        try:
            content = filename.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line.startswith("[") or not line.endswith("]"):
                continue
            section = line.strip("[]").strip()
            if section.startswith("profile "):
                section = section[len("profile ") :].strip()
            if section:
                profiles.add(section)
    return sorted(profiles)


def prompt_profile(value, message):
    if value:
        return value
    profiles = list_aws_profiles()
    if profiles:
        return ask_or_exit(
            questionary.select(
                message,
                choices=[Choice(name, value=name) for name in profiles],
            )
        )
    return ask_or_exit(questionary.text(message))


def prompt_access_level(value):
    if value:
        return value
    return ask_or_exit(
        questionary.select(
            "Access level for the instance role:",
            choices=[
                Choice("Read-only (List/Get)", value="read-only"),
                Choice("Read/write (List/Get/Put/Delete)", value="read-write"),
                Choice("Full (s3:*)", value="full"),
            ],
        )
    )


def normalize_prefix(prefix):
    if not prefix:
        return None
    cleaned = prefix.lstrip("/")
    if cleaned and not cleaned.endswith("/"):
        cleaned += "/"
    return cleaned or None


def get_instance_profile_arn(instance_id, region, profile):
    output = run_aws(
        [
            "ec2",
            "describe-instances",
            "--instance-ids",
            instance_id,
            "--query",
            "Reservations[0].Instances[0].IamInstanceProfile.Arn",
            "--output",
            "text",
        ],
        region=region,
        profile=profile,
    )
    arn = output.strip()
    if arn == "None" or not arn:
        raise RuntimeError(f"Instance {instance_id} has no instance profile attached.")
    return arn


def list_instances(region, profile):
    output = run_aws(
        [
            "ec2",
            "describe-instances",
            "--query",
            "Reservations[].Instances[].{Id:InstanceId,Name:Tags[?Key=='Name']|[0].Value,State:State.Name,Type:InstanceType,PrivateIp:PrivateIpAddress}",
            "--output",
            "json",
        ],
        region=region,
        profile=profile,
        allow_fail=True,
    )
    if not output:
        return []
    try:
        instances = json.loads(output)
    except json.JSONDecodeError:
        return []
    return [inst for inst in instances if inst.get("State") != "terminated"]


def list_regions(profile):
    output = run_aws(
        [
            "ec2",
            "describe-regions",
            "--query",
            "Regions[].RegionName",
            "--output",
            "json",
        ],
        profile=profile,
        allow_fail=True,
    )
    if not output:
        return []
    try:
        regions = json.loads(output)
    except json.JSONDecodeError:
        return []
    return sorted(regions)


def prompt_region(value, profile):
    if value:
        return value
    regions = list_regions(profile)
    if regions:
        return ask_or_exit(
            questionary.autocomplete(
                "AWS region for EC2 lookups:",
                choices=regions,
            )
        )
    return ask_or_exit(questionary.text("AWS region for EC2 lookups:"))


def format_instance_choice(instance):
    name = instance.get("Name") or "no-name"
    state = instance.get("State") or "unknown"
    instance_type = instance.get("Type") or "unknown"
    ip = instance.get("PrivateIp") or "no-ip"
    return f"{instance['Id']} | {name} | {state} | {instance_type} | {ip}"


def prompt_instance_id(value, region, profile):
    if value:
        return value
    instances = list_instances(region, profile)
    if instances:
        choices = [
            Choice(format_instance_choice(inst), value=inst["Id"]) for inst in instances
        ]
        return ask_or_exit(
            questionary.select(
                "Select EC2 instance:",
                choices=choices,
            )
        )
    return ask_or_exit(questionary.text("EC2 instance ID:"))


def get_role_arn_from_instance_profile(profile_arn, region, profile):
    name = profile_arn.split("/")[-1]
    output = run_aws(
        [
            "iam",
            "get-instance-profile",
            "--instance-profile-name",
            name,
            "--query",
            "InstanceProfile.Roles[0].Arn",
            "--output",
            "text",
        ],
        region=region,
        profile=profile,
    )
    role_arn = output.strip()
    if not role_arn or role_arn == "None":
        raise RuntimeError(f"Instance profile {name} has no roles.")
    return role_arn


def parse_role_name(role_arn):
    match = re.search(r":role/(.+)$", role_arn)
    if not match:
        raise RuntimeError(f"Could not parse role name from {role_arn}.")
    return match.group(1).split("/")[-1]


def get_bucket_policy(bucket, profile):
    output = run_aws(
        ["s3api", "get-bucket-policy", "--bucket", bucket, "--query", "Policy", "--output", "text"],
        profile=profile,
        allow_fail=True,
    )
    if not output:
        return {"Version": "2012-10-17", "Statement": []}
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Bucket policy JSON is invalid: {exc}") from exc


def build_statement(sid, role_arn, actions, bucket, prefix):
    bucket_arn = f"arn:aws:s3:::{bucket}"
    object_arn = f"{bucket_arn}/*"
    if prefix:
        object_arn = f"{bucket_arn}/{prefix}*"

    statement = {
        "Sid": sid,
        "Effect": "Allow",
        "Principal": {"AWS": role_arn},
        "Action": actions,
        "Resource": [bucket_arn, object_arn],
    }
    if prefix:
        statement["Condition"] = {"StringLike": {"s3:prefix": [prefix, f"{prefix}*"]}}
    return statement


def upsert_statement(policy, statement):
    statements = policy.get("Statement", [])
    sid = statement.get("Sid")
    updated = False
    new_statements = []
    for entry in statements:
        if entry.get("Sid") == sid:
            new_statements.append(statement)
            updated = True
        else:
            new_statements.append(entry)
    if not updated:
        new_statements.append(statement)
    policy["Statement"] = new_statements
    return policy


def build_role_policy(actions, buckets, prefix):
    resources = []
    for bucket in buckets:
        bucket_arn = f"arn:aws:s3:::{bucket}"
        object_arn = f"{bucket_arn}/*"
        if prefix:
            object_arn = f"{bucket_arn}/{prefix}*"
        resources.extend([bucket_arn, object_arn])
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": actions,
                "Resource": resources,
            }
        ],
    }


def build_equivalent_command(
    buckets,
    source_profile,
    target_profile,
    region,
    instance_id,
    role_arn,
    access,
    prefix,
    sid,
    policy_name,
    dry_run,
    cyberduck,
    cyberduck_dir,
    cyberduck_role_name,
    cyberduck_only,
):
    parts = ["uv", "run", "utils/grant-cross-account-s3.py"]
    for bucket_name in buckets:
        parts += ["--bucket", bucket_name]
    if source_profile:
        parts += ["--source-profile", source_profile]
    if target_profile:
        parts += ["--target-profile", target_profile]
    if region:
        parts += ["--region", region]
    if role_arn:
        parts += ["--role-arn", role_arn]
    elif instance_id:
        parts += ["--instance-id", instance_id]
    if access:
        parts += ["--access", access]
    if prefix:
        parts += ["--prefix", prefix]
    if sid:
        parts += ["--sid", sid]
    if policy_name:
        parts += ["--policy-name", policy_name]
    if dry_run:
        parts.append("--dry-run")
    if cyberduck:
        parts.append("--cyberduck")
        if cyberduck_dir:
            parts += ["--cyberduck-dir", str(cyberduck_dir)]
        if cyberduck_role_name:
            parts += ["--cyberduck-role-name", cyberduck_role_name]
    if cyberduck_only:
        parts.append("--cyberduck-only")
    return " ".join(shlex.quote(part) for part in parts)


def sanitize_filename(value, fallback):
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", value or "").strip()
    return cleaned or fallback


def build_cyberduck_profile(role_name):
    return {
        "Protocol": "s3",
        "Vendor": "s3-role",
        "Description": "S3 (Credentials from EC2 Instance Metadata)",
        "Context": (
            "http://169.254.169.254/latest/meta-data/iam/"
            f"security-credentials/{role_name}"
        ),
        "Username Configurable": False,
        "Default Nickname": "S3 (Credentials from EC2 Instance Metadata)",
        "Password Configurable": False,
        "Token Configurable": False,
        "Anonymous Configurable": False,
    }


def build_cyberduck_bookmark(nickname, path_value):
    return {
        "Protocol": "s3",
        "Provider": "s3-role",
        "Nickname": nickname,
        "UUID": str(uuid.uuid4()),
        "Hostname": "s3.amazonaws.com",
        "Port": "443",
        "Path": path_value,
        "Access Timestamp": str(int(time.time() * 1000)),
    }


def write_plist(path, payload):
    with path.open("wb") as handle:
        plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=False)


def build_cyberduck_bookmarks(buckets, source_profile, output_dir):
    bookmarks = []
    profile_label = sanitize_filename(source_profile, "account")
    main_nickname = f"S3 {profile_label}"
    main_filename = sanitize_filename(main_nickname, "S3 account")
    bookmarks.append(
        (output_dir / f"{main_filename}.duck", build_cyberduck_bookmark(main_nickname, "/"))
    )
    for bucket_name in buckets:
        nickname = f"S3 {bucket_name}"
        filename = sanitize_filename(nickname, f"S3 {bucket_name}")
        bookmarks.append(
            (
                output_dir / f"{filename}.duck",
                build_cyberduck_bookmark(nickname, f"/{bucket_name}"),
            )
        )
    return bookmarks


@click.command(
    help="Grant a cross-account EC2 role access to an S3 bucket.",
    epilog="Example:\n  uv run utils/grant-cross-account-s3.py --bucket my-bucket --source-profile src --target-profile dst --instance-id i-0123456789abcdef0",
)
@click.option(
    "--bucket",
    multiple=True,
    help="Target S3 bucket name (repeatable or comma-separated).",
)
@click.option("--source-profile", help="AWS CLI profile for the bucket account.")
@click.option("--target-profile", help="AWS CLI profile for the EC2 account.")
@click.option("--region", help="AWS region for EC2 lookups.")
@click.option("--instance-id", help="EC2 instance ID (used to resolve the role).")
@click.option("--role-arn", help="IAM role ARN to grant access (overrides instance lookup).")
@click.option("--access", type=click.Choice(list(ACCESS_LEVELS.keys())), help="Access level.")
@click.option("--prefix", help="Optional S3 prefix (folder) to scope access.")
@click.option("--sid", default="AllowEc2CrossAccountS3Access", show_default=True, help="Bucket policy Sid.")
@click.option(
    "--policy-name",
    default="CrossAccountS3Access",
    show_default=True,
    help="Inline role policy name.",
)
@click.option(
    "--cyberduck",
    is_flag=True,
    help="Create Cyberduck connection profile and bookmarks.",
)
@click.option(
    "--cyberduck-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("output/cyberduck"),
    show_default=True,
    help="Output directory for Cyberduck profile and bookmarks.",
)
@click.option(
    "--cyberduck-role-name",
    default="s3access",
    show_default=True,
    help="IAM role name for instance metadata credentials in Cyberduck.",
)
@click.option(
    "--cyberduck-only",
    is_flag=True,
    help="Only create Cyberduck files without changing AWS policies.",
)
@click.option("--dry-run", is_flag=True, help="Print changes without applying.")
def main(
    bucket,
    source_profile,
    target_profile,
    region,
    instance_id,
    role_arn,
    access,
    prefix,
    sid,
    policy_name,
    cyberduck,
    cyberduck_dir,
    cyberduck_role_name,
    cyberduck_only,
    dry_run,
):
    if cyberduck_only:
        cyberduck = True
    if not cyberduck_only:
        source_profile = prompt_profile(
            source_profile, "AWS profile for bucket account (source):"
        )
        buckets = prompt_buckets(bucket, source_profile, allow_lookup=True)
    else:
        source_profile = source_profile or "account"
        buckets = prompt_buckets(
            bucket, source_profile, allow_lookup=False, allow_empty=True
        )
    if not buckets and not cyberduck_only:
        click.echo("Error: at least one bucket is required.", err=True)
        sys.exit(1)
    if not cyberduck_only:
        access = prompt_access_level(access)
        target_profile = prompt_profile(
            target_profile, "AWS profile for EC2 account (target):"
        )
        region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if not region:
            region = prompt_region(region, target_profile)
        prefix = normalize_prefix(prefix)

        if not role_arn:
            instance_id = prompt_instance_id(instance_id, region, target_profile)
            try:
                profile_arn = get_instance_profile_arn(instance_id, region, target_profile)
                role_arn = get_role_arn_from_instance_profile(profile_arn, region, target_profile)
            except RuntimeError as exc:
                click.echo(f"Error: {exc}", err=True)
                sys.exit(1)

        role_name = parse_role_name(role_arn)
        actions = ACCESS_LEVELS[access]
    else:
        access = None
        target_profile = None
        region = None
        prefix = normalize_prefix(prefix)
        role_name = None
        actions = None

    click.echo("\nPlanned changes:")
    click.echo(f"- Buckets: {', '.join(buckets)}")
    click.echo(f"- Bucket account profile: {source_profile}")
    if not cyberduck_only:
        click.echo(f"- EC2 account profile: {target_profile}")
        click.echo(f"- Role ARN: {role_arn}")
        click.echo(f"- Access: {access}")
    click.echo(f"- Prefix: {prefix or '(none)'}")
    if not cyberduck_only:
        click.echo(f"- Bucket policy Sid: {sid}")
        click.echo(f"- Inline role policy: {policy_name}")
    if cyberduck:
        click.echo(f"- Cyberduck output dir: {cyberduck_dir}")
        click.echo(f"- Cyberduck role name: {cyberduck_role_name}")

    if not cyberduck_only:
        bucket_policy_updates = []
        for bucket_name in buckets:
            try:
                bucket_policy = get_bucket_policy(bucket_name, source_profile)
            except RuntimeError as exc:
                click.echo(f"Error: {exc}", err=True)
                sys.exit(1)
            statement = build_statement(sid, role_arn, actions, bucket_name, prefix)
            updated_policy = upsert_statement(bucket_policy, statement)
            policy_json = json.dumps(updated_policy, separators=(",", ":"), sort_keys=True)
            bucket_policy_updates.append((bucket_name, policy_json))

        role_policy = build_role_policy(actions, buckets, prefix)
        role_policy_json = json.dumps(role_policy, separators=(",", ":"), sort_keys=True)
    else:
        bucket_policy_updates = []
        role_policy_json = None

    if dry_run:
        click.echo("\nDry run enabled. No changes will be applied.")
        if not cyberduck_only:
            for bucket_name, policy_json in bucket_policy_updates:
                click.echo(f"\nBucket policy update for {bucket_name}:")
                click.echo(policy_json)
            click.echo("\nRole inline policy update:")
            click.echo(role_policy_json)
        if cyberduck:
            click.echo("\nCyberduck files:")
            profile_path = cyberduck_dir / "S3 (Credentials from Instance Metadata).cyberduckprofile"
            click.echo(str(profile_path))
            for path, _ in build_cyberduck_bookmarks(buckets, source_profile, cyberduck_dir):
                click.echo(str(path))
        return
    command = build_equivalent_command(
        buckets=buckets,
        source_profile=source_profile,
        target_profile=target_profile,
        region=region,
        instance_id=instance_id,
        role_arn=role_arn,
        access=access,
        prefix=prefix,
        sid=sid,
        policy_name=policy_name,
        dry_run=dry_run,
        cyberduck=cyberduck,
        cyberduck_dir=cyberduck_dir,
        cyberduck_role_name=cyberduck_role_name,
        cyberduck_only=cyberduck_only,
    )
    click.echo("\nEquivalent command:")
    click.echo(command)
    if not ask_or_exit(questionary.confirm("Apply these changes?")):
        click.echo("Cancelled.")
        sys.exit(1)

    if not cyberduck_only:
        for bucket_name, policy_json in bucket_policy_updates:
            run_aws(
                ["s3api", "put-bucket-policy", "--bucket", bucket_name, "--policy", policy_json],
                profile=source_profile,
            )
        run_aws(
            [
                "iam",
                "put-role-policy",
                "--role-name",
                role_name,
                "--policy-name",
                policy_name,
                "--policy-document",
                role_policy_json,
            ],
            region=region,
            profile=target_profile,
        )

    if cyberduck:
        cyberduck_dir.mkdir(parents=True, exist_ok=True)
        profile_path = (
            cyberduck_dir / "S3 (Credentials from Instance Metadata).cyberduckprofile"
        )
        write_plist(profile_path, build_cyberduck_profile(cyberduck_role_name))
        for path, payload in build_cyberduck_bookmarks(buckets, source_profile, cyberduck_dir):
            write_plist(path, payload)

    click.echo("\nDone. The EC2 role can now access the bucket.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
