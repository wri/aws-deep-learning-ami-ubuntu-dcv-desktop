#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.9"
# dependencies = ["rich-click>=1.7.0", "questionary>=2.0.1", "rich>=13.0.0"]
# ///
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlparse
import uuid
import rich_click as click
import questionary
from questionary import Choice
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

# Configure rich-click
click.rich_click.USE_RICH_MARKUP = True
click.rich_click.USE_MARKDOWN = True
click.rich_click.SHOW_ARGUMENTS = True
click.rich_click.GROUP_ARGUMENTS_OPTIONS = True
click.rich_click.SHOW_DEFAULT = True
click.rich_click.STYLE_ERRORS_SUGGESTION = "magenta italic"
click.rich_click.ERRORS_SUGGESTION = "Try running the '--help' flag for more information."
click.rich_click.ERRORS_EPILOGUE = "To find out more, visit [link=https://github.com/your-repo]https://github.com/your-repo[/link]"
click.rich_click.OPTION_GROUPS = {
    "main": [
        {
            "name": "Defaults If Omitted",
            "options": [
                "--template",
                "--region",
                "--profile",
                "--desktop-access-cidr-v6",
                "--slack-webhook-url",
                "--user",
                "--ubuntu-password",
                "--project-tag",
                "--update-stack",
                "--delete-rollback",
            ],
        },
        {
            "name": "Create VPC",
            "options": [
                "--create-vpc",
                "--vpc-cidr",
                "--public-subnet-cidr",
                "--subnet-az",
            ],
        },
        {
            "name": "Common Prompts If Omitted",
            "options": [
                "--stack-name-suffix",
                "--vpc-id",
                "--subnet-id",
                "--desktop-access-cidr",
                "--instance-type",
                "--public-ip",
                "--assign-static-ip",
                "--ebs-size",
            ],
        },
        {
            "name": "Windows Template Options",
            "options": [
                "--instance-profile-name",
                "--driver-type",
                "--tesla-driver-version",
                "--allow-ssh-port",
                "--allow-rdp-port",
                "--listen-port",
                "--ssh-public-key",
            ],
        },
        {
            "name": "Ubuntu Template Options",
            "options": [
                "--key-name",
                "--s3-bucket",
                "--security-group-id",
                "--ami-type",
                "--enable-efs",
                "--desktop-flavor",
                "--ubuntu-ami-override",
                "--userdata-script-url",
                "--skip-desktop-install",
                "--instance-role-name",
            ],
        },
        {
            "name": "Options",
            "options": [
                "--debug",
                "--dry-run",
            ],
        },
    ]
}


def run_aws(args, region=None, profile=None):
    cmd = ["aws"] + args
    if region:
        cmd += ["--region", region]
    if profile:
        cmd += ["--profile", profile]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as exc:
        print(exc.output.strip(), file=sys.stderr)
        console.print(f"[red]AWS CLI Error:[/red] {exc.output.strip()}", file=sys.stderr)
        sys.exit(exc.returncode)
    return output


def _sanitize_bucket_component(value):
    value = value.lower()
    value = re.sub(r"[^a-z0-9-]", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value


def get_account_id(region=None, profile=None):
    cmd = ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"]
    if region:
        cmd += ["--region", region]
    if profile:
        cmd += ["--profile", profile]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except subprocess.CalledProcessError:
        return ""
    return output.strip()


def build_default_bucket_name(region=None, profile=None):
    account_name = _sanitize_bucket_component(get_account_name(region, profile))
    if not account_name:
        raise ValueError("Account name not available via organizations:DescribeAccount.")
    base = account_name
    prefix = "wri-aws-"
    max_alias_len = 63 - len(prefix) - 1 - len(region or "")
    if max_alias_len < 1:
        base = account_name[:12]
    elif len(base) > max_alias_len:
        base = base[:max_alias_len]
    return f"{prefix}{base}-{region}"


def is_s3_url(url):
    return "s3.amazonaws.com" in url or ".s3." in url


def parse_s3_url(url):
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path.lstrip("/")
    if host.startswith("s3."):
        if "/" not in path:
            return None, None
        bucket, key = path.split("/", 1)
        return bucket, key
    if ".s3." in host:
        bucket = host.split(".s3.")[0]
        return bucket, path
    if host.endswith(".s3.amazonaws.com"):
        bucket = host.split(".s3.amazonaws.com")[0]
        return bucket, path
    return None, None


def download_s3_object(bucket, key, region=None, profile=None):
    handle = tempfile.NamedTemporaryFile(delete=False)
    handle.close()
    cmd = ["aws", "s3api", "get-object", "--bucket", bucket, "--key", key, handle.name]
    if region:
        cmd += ["--region", region]
    if profile:
        cmd += ["--profile", profile]
    subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    return handle.name


def get_account_name(region=None, profile=None):
    account_id = get_account_id(region, profile)
    if not account_id:
        return ""
    cmd = [
        "aws",
        "organizations",
        "describe-account",
        "--account-id",
        account_id,
        "--output",
        "json",
    ]
    if region:
        cmd += ["--region", region]
    cmd += ["--profile", "wri-admin"]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except subprocess.CalledProcessError:
        return ""
    data = json.loads(output)
    account = data.get("Account", {})
    return account.get("Name", "")


def ensure_bucket(bucket, region=None, profile=None):
    cmd = ["aws", "s3api", "head-bucket", "--bucket", bucket]
    if region:
        cmd += ["--region", region]
    if profile:
        cmd += ["--profile", profile]
    try:
        subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
        return
    except subprocess.CalledProcessError:
        pass
    create_cmd = ["aws", "s3api", "create-bucket", "--bucket", bucket]
    if region and region != "us-east-1":
        create_cmd += ["--create-bucket-configuration", f"LocationConstraint={region}"]
    if region:
        create_cmd += ["--region", region]
    if profile:
        create_cmd += ["--profile", profile]
    subprocess.check_output(create_cmd, stderr=subprocess.STDOUT, text=True)


def upload_template_content(content, bucket, source_name, region=None, profile=None):
    timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S")
    base_name = os.path.basename(source_name) if source_name else "template.yaml"
    stem, ext = os.path.splitext(base_name)
    ext = ext or ".yaml"
    key = f"cloud-formation/templates/{stem}-{timestamp}{ext}"
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(content)
        temp_path = handle.name
    try:
        cmd = ["aws", "s3api", "put-object", "--bucket", bucket, "--key", key, "--body", temp_path]
        if region:
            cmd += ["--region", region]
        if profile:
            cmd += ["--profile", profile]
        subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
    if region == "us-east-1":
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def resolve_template_source(template, region=None, profile=None):
    console.print("[dim]Resolving template source...[/dim]")
    if template.startswith("https://") and is_s3_url(template):
        console.print("[dim]Template is an S3 URL; downloading for parameter parsing...[/dim]")
        bucket, key = parse_s3_url(template)
        if not bucket or not key:
            return template, None
        parse_path = download_s3_object(bucket, key, region, profile)
        return template, parse_path
    if template.startswith("https://"):
        console.print("[dim]Downloading template from HTTPS URL...[/dim]")
        with urllib.request.urlopen(template) as response:
            content = response.read()
        console.print("[dim]Uploading template to S3...[/dim]")
        bucket = build_default_bucket_name(region, profile)
        ensure_bucket(bucket, region, profile)
        template_url = upload_template_content(content, bucket, os.path.basename(urlparse(template).path), region, profile)
        parse_handle = tempfile.NamedTemporaryFile(delete=False)
        parse_handle.write(content)
        parse_handle.close()
        return template_url, parse_handle.name
    console.print("[dim]Reading local template file...[/dim]")
    with open(template, "rb") as handle:
        content = handle.read()
    if len(content) <= 51200:
        console.print("[dim]Template size within limits; using local file...[/dim]")
        return None, template
    console.print("[dim]Template too large; uploading to S3...[/dim]")
    bucket = build_default_bucket_name(region, profile)
    ensure_bucket(bucket, region, profile)
    template_url = upload_template_content(content, bucket, template, region, profile)
    return template_url, template


def get_stack_info(stack_name, region=None, profile=None):
    """Get stack information and extract instance ID and key name."""
    try:
        cmd = ["aws", "cloudformation", "describe-stacks", "--stack-name", stack_name, "--output", "json"]
        if region:
            cmd += ["--region", region]
        if profile:
            cmd += ["--profile", profile]
        
        # Use subprocess directly to avoid sys.exit() in run_aws
        stack_raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
        
        stack_info = json.loads(stack_raw)
        stack_status = stack_info["Stacks"][0]["StackStatus"]
        outputs = stack_info["Stacks"][0].get("Outputs", [])
        
        # Extract instance ID and key name from outputs
        instance_id = None
        key_name = None
        for output in outputs:
            output_key = output.get("OutputKey")
            if output_key in ("InstanceId", "EC2instanceID"):
                instance_id = output.get("OutputValue")
            elif output_key == "KeyPairName":
                key_name = output.get("OutputValue")
        
        # Fallback: get instance ID from resources if not in outputs
        if not instance_id:
            try:
                resource_cmd = ["aws", "cloudformation", "describe-stack-resource",
                    "--stack-name", stack_name,
                    "--logical-resource-id", "DesktopInstance",
                    "--query", "StackResourceDetail.PhysicalResourceId",
                    "--output", "text"]
                if region:
                    resource_cmd += ["--region", region]
                if profile:
                    resource_cmd += ["--profile", profile]
                
                instance_id = subprocess.check_output(resource_cmd, stderr=subprocess.DEVNULL, text=True).strip()
            except subprocess.CalledProcessError:
                pass
        
        return stack_status, instance_id, key_name
        
    except subprocess.CalledProcessError:
        return None, None, None


def get_aws_resources(region=None, profile=None):
    """Fetch all required AWS resources."""
    # Get VPCs
    vpcs_raw = run_aws([
        "ec2", "describe-vpcs",
        "--query", "Vpcs[].{Id:VpcId,Cidr:CidrBlock,Name:Tags[?Key=='Name']|[0].Value}",
        "--output", "json"
    ], region, profile)
    vpcs = json.loads(vpcs_raw)
    
    # Get key pairs
    keys_raw = run_aws([
        "ec2", "describe-key-pairs",
        "--query", "KeyPairs[].KeyName",
        "--output", "json"
    ], region, profile)
    keypairs = json.loads(keys_raw)
    
    # Get S3 buckets
    buckets_raw = run_aws([
        "s3api", "list-buckets",
        "--query", "Buckets[].Name",
        "--output", "json"
    ], region, profile)
    buckets = json.loads(buckets_raw)
    
    return vpcs, keypairs, buckets


def get_vpcs(region=None, profile=None):
    vpcs_raw = run_aws([
        "ec2", "describe-vpcs",
        "--query", "Vpcs[].{Id:VpcId,Cidr:CidrBlock,Name:Tags[?Key=='Name']|[0].Value}",
        "--output", "json"
    ], region, profile)
    return json.loads(vpcs_raw)


def get_keypairs(region=None, profile=None):
    keys_raw = run_aws([
        "ec2", "describe-key-pairs",
        "--query", "KeyPairs[].KeyName",
        "--output", "json"
    ], region, profile)
    return json.loads(keys_raw)


def get_buckets(region=None, profile=None):
    buckets_raw = run_aws([
        "s3api", "list-buckets",
        "--query", "Buckets[].Name",
        "--output", "json"
    ], region, profile)
    return json.loads(buckets_raw)


def update_stack_flow(ctx, stack_name, template, region, profile, dry_run, stack_status, tag_value, is_windows):
    if not stack_status:
        console.print(f"[red]❌ Stack '{stack_name}' does not exist. Cannot update.[/red]")
        return 1
    if stack_status not in ["CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"]:
        console.print(f"[red]❌ Stack exists but is in state '{stack_status}'. Cannot update.[/red]")
        return 1

    describe_cmd = ["aws", "cloudformation", "describe-stacks", "--stack-name", stack_name, "--output", "json"]
    if region:
        describe_cmd += ["--region", region]
    if profile:
        describe_cmd += ["--profile", profile]
    try:
        stack_raw = subprocess.check_output(describe_cmd, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]❌ Failed to describe stack:[/red] {exc.output.strip()}")
        return 1

    try:
        stack_info = json.loads(stack_raw)
        stack_params = stack_info["Stacks"][0].get("Parameters", [])
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        console.print(f"[red]❌ Failed to parse stack parameters:[/red] {exc}")
        return 1

    parameters = []
    for param in stack_params:
        key = param.get("ParameterKey")
        value = param.get("ParameterValue")
        if not key:
            continue
        if value in (None, "****"):
            parameters.append(f"ParameterKey={key},UsePreviousValue=true")
        else:
            parameters.append(f"ParameterKey={key},ParameterValue={value}")

    cmd = [
        "aws", "cloudformation", "update-stack",
        "--stack-name", stack_name,
        "--capabilities", "CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND",
    ]
    if parameters:
        cmd += ["--parameters"] + parameters
    if template.startswith("https://"):
        cmd += ["--template-url", template]
    else:
        cmd += ["--template-body", f"file://{template}"]

    if tag_value:
        cmd += ["--tags", f"Key=wri:project,Value={tag_value}"]

    if region:
        cmd += ["--region", region]
    if profile:
        cmd += ["--profile", profile]

    console.print("\n[bold cyan]CloudFormation command:[/bold cyan]")
    console.print(" ".join(cmd))

    if dry_run:
        return 0

    if not confirm("Update stack now?"):
        console.print("[red]Cancelled.[/red]")
        return 1

    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as exc:
        if "No updates are to be performed" in exc.output:
            console.print("[yellow]No updates to apply.[/yellow]")
            return 0
        console.print(f"[red]❌ Stack update failed:[/red] {exc.output.strip()}")
        return 1

    console.print(f"\n[yellow]Stack '{stack_name}' update initiated. Waiting for completion...[/yellow]")
    wait_cmd = ["aws", "cloudformation", "wait", "stack-update-complete", "--stack-name", stack_name]
    if region:
        wait_cmd += ["--region", region]
    if profile:
        wait_cmd += ["--profile", profile]

    try:
        subprocess.check_call(wait_cmd)
        console.print(f"[green]✅ Stack '{stack_name}' updated successfully![/green]")
        with console.status("Loading stack info..."):
            _, instance_id, _ = get_stack_info(stack_name, region, profile)
        password_was_provided = 'ubuntu_password' in ctx.params and ctx.params['ubuntu_password'] is not None
        with console.status("Loading stack info..."):
            _, instance_id, key_name = get_stack_info(stack_name, region, profile)
        display_instance_info(
            instance_id,
            key_name,
            region,
            profile,
            is_new_stack=False,
            password_set=password_was_provided,
            is_windows=is_windows,
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[red]❌ Stack update failed or timed out: {e}[/red]")
        return 1

    return 0


def get_subnets_for_vpc(vpc_id, region=None, profile=None):
    """Get subnets for a specific VPC."""
    subnets_raw = run_aws([
        "ec2", "describe-subnets",
        "--filters", f"Name=vpc-id,Values={vpc_id}",
        "--query", "Subnets[].{Id:SubnetId,Cidr:CidrBlock,Az:AvailabilityZone,Public:MapPublicIpOnLaunch,Name:Tags[?Key=='Name']|[0].Value}",
        "--output", "json"
    ], region, profile)
    return json.loads(subnets_raw)


def get_security_groups_for_vpc(vpc_id, region=None, profile=None):
    """Get security groups for a specific VPC."""
    security_groups_raw = run_aws([
        "ec2", "describe-security-groups",
        "--filters", f"Name=vpc-id,Values={vpc_id}",
        "--query", "SecurityGroups[].{Id:GroupId,Name:GroupName,Description:Description}",
        "--output", "json"
    ], region, profile)
    return json.loads(security_groups_raw)


def create_vpc_with_public_subnet(
    vpc_cidr,
    subnet_cidr,
    subnet_az,
    vpc_name,
    name_prefix,
    region=None,
    profile=None,
):
    vpc_raw = run_aws(
        ["ec2", "create-vpc", "--cidr-block", vpc_cidr, "--output", "json"],
        region,
        profile,
    )
    vpc_id = json.loads(vpc_raw)["Vpc"]["VpcId"]
    vpc_tag = vpc_name or f"{name_prefix}-vpc"
    run_aws(
        ["ec2", "create-tags", "--resources", vpc_id, "--tags", f"Key=Name,Value={vpc_tag}"],
        region,
        profile,
    )
    run_aws(
        [
            "ec2",
            "modify-vpc-attribute",
            "--vpc-id",
            vpc_id,
            "--enable-dns-support",
            json.dumps({"Value": True}),
        ],
        region,
        profile,
    )
    run_aws(
        [
            "ec2",
            "modify-vpc-attribute",
            "--vpc-id",
            vpc_id,
            "--enable-dns-hostnames",
            json.dumps({"Value": True}),
        ],
        region,
        profile,
    )

    igw_raw = run_aws(["ec2", "create-internet-gateway", "--output", "json"], region, profile)
    igw_id = json.loads(igw_raw)["InternetGateway"]["InternetGatewayId"]
    run_aws(
        ["ec2", "create-tags", "--resources", igw_id, "--tags", f"Key=Name,Value={name_prefix}-igw"],
        region,
        profile,
    )
    run_aws(
        ["ec2", "attach-internet-gateway", "--internet-gateway-id", igw_id, "--vpc-id", vpc_id],
        region,
        profile,
    )

    rt_raw = run_aws(["ec2", "create-route-table", "--vpc-id", vpc_id, "--output", "json"], region, profile)
    rt_id = json.loads(rt_raw)["RouteTable"]["RouteTableId"]
    run_aws(
        ["ec2", "create-tags", "--resources", rt_id, "--tags", f"Key=Name,Value={name_prefix}-public-rt"],
        region,
        profile,
    )
    run_aws(
        [
            "ec2",
            "create-route",
            "--route-table-id",
            rt_id,
            "--destination-cidr-block",
            "0.0.0.0/0",
            "--gateway-id",
            igw_id,
        ],
        region,
        profile,
    )

    subnet_cmd = ["ec2", "create-subnet", "--vpc-id", vpc_id, "--cidr-block", subnet_cidr, "--output", "json"]
    if subnet_az:
        subnet_cmd += ["--availability-zone", subnet_az]
    subnet_raw = run_aws(subnet_cmd, region, profile)
    subnet_id = json.loads(subnet_raw)["Subnet"]["SubnetId"]
    run_aws(
        ["ec2", "create-tags", "--resources", subnet_id, "--tags", f"Key=Name,Value={name_prefix}-public-subnet"],
        region,
        profile,
    )
    run_aws(
        ["ec2", "modify-subnet-attribute", "--subnet-id", subnet_id, "--map-public-ip-on-launch"],
        region,
        profile,
    )
    run_aws(
        ["ec2", "associate-route-table", "--subnet-id", subnet_id, "--route-table-id", rt_id],
        region,
        profile,
    )

    return vpc_id, subnet_id


def parse_template_options(template_path):
    lines = []
    if template_path.startswith("https://"):
        try:
            with urllib.request.urlopen(template_path) as response:
                content = response.read().decode("utf-8")
        except Exception:
            return {}, {}, set()
        lines = content.splitlines()
    else:
        if not os.path.exists(template_path):
            return {}, {}, set()
        with open(template_path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()

    defaults = {}
    allowed = {}
    parameters = set()
    current = None
    in_allowed = False
    in_params = False

    for line in lines:
            if re.match(r"^Parameters:\s*$", line):
                in_params = True
                current = None
                in_allowed = False
                continue

            if in_params and re.match(r"^[A-Za-z0-9].*:\s*$", line) and not line.startswith(" "):
                in_params = False
                current = None
                in_allowed = False
                continue

            if not in_params:
                continue

            key_match = re.match(r"^\s{2}([A-Za-z0-9]+):\s*$", line)
            if key_match:
                current = key_match.group(1)
                parameters.add(current)
                in_allowed = False
                continue

            if current:
                default_match = re.match(r"^\s{4}Default:\s*(.+)$", line)
                if default_match:
                    value = default_match.group(1).strip().strip("'\"")
                    defaults[current] = value
                    continue

                if re.match(r"^\s{4}AllowedValues:\s*$", line):
                    allowed[current] = []
                    in_allowed = True
                    continue

                if in_allowed:
                    value_match = re.match(r"^\s{6}-\s*(.+)$", line)
                    if value_match:
                        allowed[current].append(value_match.group(1).strip().strip("'\""))
                        continue
                    in_allowed = False

    return defaults, allowed, parameters


def resolve_param_name(param_names, *candidates):
    for name in candidates:
        if name in param_names:
            return name
    return None


def param_is_required(param_name, defaults):
    return bool(param_name) and param_name not in defaults


def find_ssh_public_keys():
    ssh_dir = os.path.expanduser("~/.ssh")
    if not os.path.isdir(ssh_dir):
        return []
    keys = []
    for path in sorted(glob.glob(os.path.join(ssh_dir, "*.pub"))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read().strip()
        except OSError:
            continue
        if content:
            keys.append({"path": path, "content": content})
    return keys


def resolve_ssh_public_key(value):
    if not value:
        return ""
    value = value.strip()
    if not value:
        return ""
    candidate = os.path.expanduser(value)
    if os.path.isfile(candidate):
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return value
    return value


def prompt_ssh_public_key():
    keys = find_ssh_public_keys()
    if keys:
        choices = [
            Choice(
                title=f"{os.path.basename(item['path'])}  {item['content'][:48]}...",
                value=item,
            )
            for item in keys
        ]
        choices.append(Choice(title="Enter custom public key", value="__custom__"))
        selection = questionary.select(
            "Select SSH public key",
            choices=choices,
            use_shortcuts=True,
        ).ask()
        if selection is None:
            console.print("[red]Cancelled.[/red]")
            sys.exit(1)
        if selection == "__custom__":
            value = questionary.text("SSH public key (paste or path to .pub file)").ask()
            if value is None:
                console.print("[red]Cancelled.[/red]")
                sys.exit(1)
            return resolve_ssh_public_key(value)
        return selection["content"]

    value = questionary.text("SSH public key (paste or path to .pub file)").ask()
    if value is None:
        console.print("[red]Cancelled.[/red]")
        sys.exit(1)
    return resolve_ssh_public_key(value)


def select_from_list(items, label, formatter=None):
    if formatter is None:
        formatter = lambda x: str(x)

    if not items:
        while True:
            value = questionary.text(f"No {label} found. Enter {label} manually:").ask()
            if value is None:
                console.print("[red]Cancelled.[/red]")
                sys.exit(1)
            if value.strip():
                return value.strip()

    choices = [Choice(title=formatter(item), value=item) for item in items]
    selection = questionary.select(
        f"Select {label}",
        choices=choices,
        use_shortcuts=True,
    ).ask()
    if selection is None:
        console.print("[red]Cancelled.[/red]")
        sys.exit(1)
    return selection


def prompt_optional_choice(label, allowed_values, default_value):
    prompt = label
    if default_value:
        prompt = f"{label} (enter to use default: {default_value})"
    if allowed_values:
        choices = [default_value] if default_value else []
        choices += [value for value in allowed_values if value != default_value]
        choices.append("Enter custom value")
        selection = questionary.select(prompt, choices=choices).ask()
        if selection is None:
            console.print("[red]Cancelled.[/red]")
            sys.exit(1)
        if selection == "Enter custom value":
            value = questionary.text(f"{label} custom value:").ask()
            return value.strip() if value else None
        if selection == default_value:
            return default_value
        return selection

    value = questionary.text(prompt).ask()
    if value and value.strip():
        return value.strip()
    return None


def get_public_cidr():
    try:
        with urllib.request.urlopen("http://checkip.amazonaws.com/", timeout=5) as resp:
            ip = resp.read().decode("utf-8").strip()
        if ip:
            return f"{ip}/32"
    except Exception:
        return None
    return None


def display_instance_info(
    instance_id,
    key_name,
    region=None,
    profile=None,
    is_new_stack=False,
    password_set=False,
    show_dcv=True,
    is_windows=False,
):
    """Display instance information including SSH and DCV connection details."""
    if not instance_id:
        console.print("[red]❌ Could not find DesktopInstance resource[/red]")
        return
    
    console.print(f"[cyan]🖥️  Instance ID:[/cyan] {instance_id}")
    
    # Get instance details for SSH info
    try:
        instance_cmd = [
            "aws",
            "ec2",
            "describe-instances",
            "--instance-ids",
            instance_id,
            "--query",
            "Reservations[0].Instances[0].{PublicIpAddress:PublicIpAddress,PrivateIpAddress:PrivateIpAddress,State:State.Name}",
            "--output",
            "json",
        ]
        if region:
            instance_cmd += ["--region", region]
        if profile:
            instance_cmd += ["--profile", profile]
        
        instance_info = subprocess.check_output(instance_cmd, text=True)
        instance_data = json.loads(instance_info)
        
        public_ip = instance_data.get("PublicIpAddress")
        private_ip = instance_data.get("PrivateIpAddress")
        state = instance_data.get("State")
        
        console.print(f"[cyan]📡 Instance State:[/cyan] {state}")
        console.print(f"[cyan]🌐 Public IP:[/cyan] {public_ip or 'none'}")
        console.print(f"[cyan]🏠 Private IP:[/cyan] {private_ip}")
        
        if public_ip:
            if key_name:
                console.print(f"[green]🔑 SSH Connection:[/green]")
                console.print(f"   [dim]ssh -i ~/.ssh/{key_name}.pem ubuntu@{public_ip}[/dim]")

            if show_dcv:
                console.print(f"[green]🖥️  DCV Connection:[/green]")
                console.print(f"   [dim]https://{public_ip}:8443[/dim]")

                if is_new_stack and not password_set:
                    console.print("")
                    if is_windows:
                        message = (
                            "[yellow]⚠️  REMINDER: Set password for DCV login!\n"
                            "Default username: Administrator\n"
                            "Use SSM Session Manager or RDP to set it.[/yellow]"
                        )
                    else:
                        message = (
                            "[yellow]⚠️  REMINDER: Set password for DCV login!\n"
                            "Default username: ubuntu\n"
                            "Run: sudo passwd ubuntu[/yellow]"
                        )
                    console.print(Panel.fit(
                        message,
                        title="Password Required",
                        border_style="yellow"
                    ))
            
    except subprocess.CalledProcessError:
        console.print("[yellow]⚠️  Could not retrieve instance network information[/yellow]")


def confirm(prompt):
    while True:
        choice = questionary.text(f"{prompt} (y/N)").ask()
        if choice is None:
            return False
        choice = choice.strip().lower()
        if choice in ("y", "yes"):
            return True
        if choice in ("", "n", "no"):
            return False


@click.command()
@click.option("--stack-name-suffix", help="Stack name suffix (base: data-science-instance)")
@click.option(
    "--template",
    default="https://raw.githubusercontent.com/wri/aws-deep-learning-ami-ubuntu-dcv-desktop/refs/heads/scripts/WIndowsServer-NICE-DCV.yaml",
    show_default=True,
    help="Path or https:// URL to the CloudFormation template.",
)
@click.option("--region", help="AWS region (overrides AWS config).")
@click.option("--profile", help="AWS CLI profile to use.")
@click.option("--dry-run", is_flag=True, help="Print command only.")
@click.option("--create-vpc", is_flag=True, help="Create a new VPC and public subnet.")
@click.option("--vpc-cidr", default=None, help="CIDR block for a new VPC (with --create-vpc).")
@click.option("--public-subnet-cidr", default=None, help="CIDR for a new public subnet (with --create-vpc).")
@click.option("--subnet-az", default=None, help="Availability Zone for a new subnet (with --create-vpc).")
@click.option("--vpc-id", help="VPC ID.")
@click.option("--subnet-id", help="Subnet ID.")
@click.option("--key-name", help="EC2 key pair name.")
@click.option("--s3-bucket", help="S3 bucket name.")
@click.option("--desktop-access-cidr", help="Desktop access CIDR (e.g. 1.2.3.4/32).")
@click.option("--desktop-access-cidr-v6", help="Desktop access IPv6 CIDR (ingressIPv6).")
@click.option("--security-group-id", help="Security group ID.")
@click.option("--ami-type", help="AMI type (AWSUbuntuAMIType).")
@click.option("--instance-type", help="Instance type (DesktopInstanceType).")
@click.option("--public-ip", help="Desktop has public IP (DesktopHasPublicIpAddress).")
@click.option("--assign-static-ip", help="Assign static public IP (assignStaticIP).")
@click.option("--enable-efs", help="Enable EFS (EnableEFS).")
@click.option("--desktop-flavor", help="Desktop package to install (DesktopFlavor).")
@click.option("--ebs-size", help="EBS volume size in GB (EbsVolumeSize).")
@click.option("--ubuntu-ami-override", help="Ubuntu AMI override (leave blank or omit to use default AMI).")
@click.option("--userdata-script-url", help="User-data script URL override (UserdataScriptUrl).")
@click.option("--debug", is_flag=True, help="Enable debug mode (Debug).")
@click.option("--skip-desktop-install", is_flag=True, help="Skip installing desktop and DCV components.")
@click.option("--slack-webhook-url", help="Slack webhook URL for completion notifications (optional).")
@click.option("--user", help="Username for hostname generation (optional).")
@click.option("--ubuntu-password", help="Password for ubuntu user (required for DCV login).")
@click.option("--instance-role-name", help="Existing IAM role name to attach to the instance.")
@click.option("--instance-profile-name", help="Existing IAM instance profile name to attach (Windows template).")
@click.option("--ssh-public-key", help="SSH public key content or path to .pub file (Windows SSH).")
@click.option("--allow-ssh-port", help="Allow inbound SSH (allowSSHport).")
@click.option("--allow-rdp-port", help="Allow inbound RDP (allowRDPport).")
@click.option("--listen-port", help="DCV listen port (listenPort).")
@click.option("--driver-type", help="Driver type (driverType).")
@click.option("--tesla-driver-version", help="Tesla driver version (teslaDriverVersion).")
@click.option("--project-tag", help="Override value for wri:project tag (defaults to stack-name-suffix).")
@click.option("--update-stack", is_flag=True, help="Update an existing stack instead of creating a new one.")
@click.option(
    "--delete-rollback/--no-delete-rollback",
    default=True,
    show_default=True,
    help="Delete stacks in ROLLBACK_COMPLETE before creating a new one.",
)
@click.pass_context
def main(ctx, 
         stack_name_suffix, 
         template, 
         region, 
         profile, 
         dry_run, 
         create_vpc,
         vpc_cidr,
         public_subnet_cidr,
         subnet_az,
         vpc_id, 
         subnet_id, 
         key_name, 
         s3_bucket, 
         desktop_access_cidr, 
         desktop_access_cidr_v6,
         security_group_id, 
         ami_type, 
         instance_type, 
         public_ip, 
         assign_static_ip,
         enable_efs, 
         desktop_flavor, 
         ebs_size, 
         ubuntu_ami_override, 
         userdata_script_url, 
         debug, 
         slack_webhook_url, 
         user, 
         ubuntu_password, 
         instance_role_name,
         instance_profile_name,
         ssh_public_key,
         allow_ssh_port,
         allow_rdp_port,
         listen_port,
         driver_type,
         tesla_driver_version,
         project_tag,
         skip_desktop_install, 
         update_stack,
         delete_rollback
         ):
    """
    🚀 **Interactive launcher for deep-learning-ubuntu-desktop CloudFormation stack.**
    
    This tool helps you create AWS EC2 instances configured for deep learning with:
    
    - **NVIDIA GPU support** with pre-installed drivers
    - **NICE DCV** remote desktop access  
    - **Pre-installed software**: VS Code, Kiro, Docker, Conda
    - **Flexible networking**: Public or private subnet deployment
    - **EFS integration** for shared storage (optional)
    
    **Quick Start:**
    ```bash
    uv run ./scripts/launch-desktop.py --stack-name-suffix myname
    ```
    
    **Non-interactive mode:**
    ```bash
    uv run ./scripts/launch-desktop.py \\
        --stack-name-suffix dev \\
        --vpc-id vpc-12345 \\
        --subnet-id subnet-67890 \\
        --key-name my-key \\
        --s3-bucket my-bucket \\
        --desktop-access-cidr 1.2.3.4/32
    ```
    """

    # Build stack name
    base_name = "data-science-instance-linux"
    if os.path.basename(template) == "WIndowsServer-NICE-DCV.yaml":
        base_name = "data-science-instance-windows"
    
    if stack_name_suffix:
        # Use command line suffix
        stack_name = f"{base_name}-{stack_name_suffix}"
        console.print(f"[cyan]Using stack name:[/cyan] {stack_name}")
    elif not vpc_id:  # If no command line options, do interactive stack naming
        suffix = questionary.text(
            f"Stack name suffix (base: {base_name})",
            default="",
            instruction="Leave blank for just the base name, or add a suffix like 'dev'"
        ).ask()
        
        if suffix is None:
            console.print("[red]Cancelled.[/red]")
            return 1
        
        # Build final stack name
        if suffix.strip():
            stack_name = f"{base_name}-{suffix.strip()}"
        else:
            stack_name = base_name
        
        console.print(f"[cyan]Using stack name:[/cyan] {stack_name}")
    else:
        # Non-interactive mode without suffix, use base name
        stack_name = base_name
        console.print(f"[cyan]Using stack name:[/cyan] {stack_name}")

    is_template_url = template.startswith("https://")
    if not is_template_url and not os.path.exists(template):
        console.print(f"[red]❌ Template not found:[/red] {template}")
        return 1
    # Template validation disabled for now.
    # if is_template_url:
    #     console.print("[dim]Skipping template validation for URL-based templates.[/dim]")
    # else:
    #     with console.status("Validating CloudFormation template..."):
    #         validate_cmd = ["aws", "cloudformation", "validate-template", "--template-body", f"file://{template}"]
    #         if region:
    #             validate_cmd += ["--region", region]
    #         if profile:
    #             validate_cmd += ["--profile", profile]
    #         try:
    #             subprocess.check_output(validate_cmd, stderr=subprocess.STDOUT, text=True)
    #         except subprocess.CalledProcessError as exc:
    #             console.print(f"[red]❌ Template validation failed:[/red] {exc.output.strip()}")
    #             return 1

    if not region:
        try:
            default_region = subprocess.check_output(
                ["aws", "configure", "get", "region"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except subprocess.CalledProcessError:
            default_region = ""
        if not default_region:
            console.print("[red]❌ AWS region not set. Use --region or run 'aws configure'.[/red]")
            return 1
        region = default_region

    try:
        template_url, template_path = resolve_template_source(template, region, profile)
    except ValueError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        return 1
    template_source = template_url or template_path
    is_template_url = bool(template_url)
    if template_url and template_url != template:
        console.print(f"[dim]Using uploaded template URL: {template_url}[/dim]")
    template_basename = os.path.basename(
        urlparse(template_source).path if template_source.startswith("https://") else template_source
    )
    is_windows_template = template_basename == "WIndowsServer-NICE-DCV.yaml"

    # Check if stack already exists
    with console.status("Loading stack info..."):
        stack_status, instance_id, key_name_from_stack = get_stack_info(stack_name, region, profile)

    if update_stack:
        return update_stack_flow(
            ctx=ctx,
            stack_name=stack_name,
            template=template_source,
            region=region,
            profile=profile,
            dry_run=dry_run,
            stack_status=stack_status,
            tag_value=project_tag.strip() if project_tag else (stack_name_suffix.strip() if stack_name_suffix else ""),
            is_windows=is_windows_template,
        )

    if not stack_status:
        console.print(f"[dim]No existing stack named '{stack_name}' found.[/dim]")

    if stack_status:
        console.print(f"[yellow]⚠️  Stack '{stack_name}' already exists with status: {stack_status}[/yellow]")
        if stack_status in ["CREATE_COMPLETE", "UPDATE_COMPLETE"]:
            display_instance_info(
                instance_id,
                key_name_from_stack,
                region,
                profile,
                is_new_stack=False,
                is_windows=is_windows_template,
            )
            return 0
        if stack_status == "ROLLBACK_COMPLETE" and delete_rollback:
            console.print(f"[yellow]Deleting stack '{stack_name}' in ROLLBACK_COMPLETE...[/yellow]")
            delete_cmd = ["aws", "cloudformation", "delete-stack", "--stack-name", stack_name]
            if region:
                delete_cmd += ["--region", region]
            if profile:
                delete_cmd += ["--profile", profile]
            try:
                subprocess.check_output(delete_cmd, stderr=subprocess.STDOUT, text=True)
            except subprocess.CalledProcessError as exc:
                console.print(f"[red]❌ Stack delete failed:[/red] {exc.output.strip()}")
                return 1
            wait_cmd = ["aws", "cloudformation", "wait", "stack-delete-complete", "--stack-name", stack_name]
            if region:
                wait_cmd += ["--region", region]
            if profile:
                wait_cmd += ["--profile", profile]
            try:
                subprocess.check_output(wait_cmd, stderr=subprocess.STDOUT, text=True)
            except subprocess.CalledProcessError as exc:
                console.print(f"[red]❌ Stack delete wait failed:[/red] {exc.output.strip()}")
                return 1
            console.print(f"[green]Deleted stack '{stack_name}'. Proceeding with create.[/green]")
            stack_status = None
        else:
            console.print(f"[red]❌ Stack exists but is in state '{stack_status}'. Cannot proceed with creation.[/red]")
            return 1

    # Parse template
    parse_source = template_path or template_source
    defaults, allowed, param_names = parse_template_options(parse_source)
    vpc_param = resolve_param_name(param_names, "DesktopVpcId", "vpcID")
    subnet_param = resolve_param_name(param_names, "DesktopVpcSubnetId", "subnetID")
    cidr_param = resolve_param_name(param_names, "DesktopAccessCIDR", "ingressIPv4")
    key_param = resolve_param_name(param_names, "KeyName")
    s3_param = resolve_param_name(param_names, "S3Bucket")
    security_group_param = resolve_param_name(param_names, "DesktopSecurityGroupId")
    ami_type_param = resolve_param_name(param_names, "AWSUbuntuAMIType")
    instance_type_param = resolve_param_name(param_names, "DesktopInstanceType", "instanceType")
    public_ip_param = resolve_param_name(param_names, "DesktopHasPublicIpAddress", "displayPublicIP")
    assign_static_ip_param = resolve_param_name(param_names, "assignStaticIP")
    enable_efs_param = resolve_param_name(param_names, "EnableEFS")
    desktop_flavor_param = resolve_param_name(param_names, "DesktopFlavor")
    ebs_size_param = resolve_param_name(param_names, "EbsVolumeSize", "volumeSize")
    listen_port_param = resolve_param_name(param_names, "listenPort")
    driver_type_param = resolve_param_name(param_names, "driverType")
    tesla_driver_param = resolve_param_name(param_names, "teslaDriverVersion")
    rdp_param = resolve_param_name(param_names, "allowRDPport")
    ipv6_param = resolve_param_name(param_names, "ingressIPv6")
    ubuntu_override_param = resolve_param_name(param_names, "UbuntuAMIOverride")
    userdata_param = resolve_param_name(param_names, "UserdataScriptUrl")
    debug_param = resolve_param_name(param_names, "Debug")
    slack_param = resolve_param_name(param_names, "SlackWebhookUrl")
    stack_suffix_param = resolve_param_name(param_names, "StackNameSuffix")
    user_param = resolve_param_name(param_names, "User")
    ubuntu_password_param = resolve_param_name(param_names, "UbuntuPassword")
    instance_role_param = resolve_param_name(param_names, "InstanceRoleName")
    instance_profile_param = resolve_param_name(param_names, "existingInstanceProfileName", "ExistingInstanceProfileName")
    project_tag_param = resolve_param_name(param_names, "ProjectTagValue")
    ssh_public_key_param = resolve_param_name(param_names, "sshPublicKey")
    allow_ssh_param = resolve_param_name(param_names, "allowSSHport")
    template_supports_ssh_key = ssh_public_key_param is not None
    required_values = []
    for param_name, value in [
        (vpc_param, vpc_id if not create_vpc else "created"),
        (subnet_param, subnet_id if not create_vpc else "created"),
        (cidr_param, desktop_access_cidr),
        (key_param, key_name),
        (s3_param, s3_bucket),
    ]:
        if param_is_required(param_name, defaults):
            required_values.append(bool(value))
    non_interactive = all(required_values) if required_values else False

    # Interactive prompts (only if not provided via command line)
    if vpc_param and not vpc_id:
        if create_vpc and (vpc_id or subnet_id):
            console.print("[red]❌ --create-vpc cannot be used with --vpc-id or --subnet-id.[/red]")
            return 1
        if non_interactive and not create_vpc:
            console.print("[red]❌ Missing --vpc-id (or use --create-vpc) in non-interactive mode.[/red]")
            return 1
        if not create_vpc and not non_interactive:
            with console.status("Loading AWS resources (VPCs, key pairs, S3 buckets)..."):
                vpcs, keypairs, buckets = get_aws_resources(region, profile)
            choices = [
                Choice(
                    title=f"{vpc.get('Id')}  {vpc.get('Cidr')}  {vpc.get('Name') or ''}".strip(),
                    value=vpc,
                )
                for vpc in vpcs
            ]
            choices.append(Choice(title="Create new VPC and public subnet", value="__create_vpc__"))
            selection = questionary.select("Select VPC", choices=choices, use_shortcuts=True).ask()
            if selection is None:
                console.print("[red]Cancelled.[/red]")
                return 1
            if selection == "__create_vpc__":
                create_vpc = True
            else:
                vpc_id = selection.get("Id")
        if create_vpc:
            if not vpc_cidr:
                vpc_cidr = "10.0.0.0/16" if non_interactive else questionary.text(
                    "New VPC CIDR", default="10.0.0.0/16"
                ).ask()
            if vpc_cidr is None:
                console.print("[red]Cancelled.[/red]")
                return 1
            vpc_name_default = "data-science-instance-vpc"
            if non_interactive:
                vpc_name = vpc_name_default
            else:
                vpc_name = questionary.text(
                    "VPC name (optional)", default=vpc_name_default
                ).ask()
                if vpc_name is None:
                    console.print("[red]Cancelled.[/red]")
                    return 1
                vpc_name = vpc_name.strip() or vpc_name_default
            if not public_subnet_cidr:
                public_subnet_cidr = "10.0.1.0/24" if non_interactive else questionary.text(
                    "New public subnet CIDR", default="10.0.1.0/24"
                ).ask()
            if public_subnet_cidr is None:
                console.print("[red]Cancelled.[/red]")
                return 1
            if subnet_az is None and not non_interactive:
                subnet_az = questionary.text("New subnet AZ (optional)", default="").ask()
                if subnet_az is None:
                    console.print("[red]Cancelled.[/red]")
                    return 1
            subnet_az = (subnet_az or "").strip()
            vpc_id, subnet_id = create_vpc_with_public_subnet(
                vpc_cidr=vpc_cidr.strip(),
                subnet_cidr=public_subnet_cidr.strip(),
                subnet_az=subnet_az,
                vpc_name=vpc_name,
                name_prefix=stack_name,
                region=region,
                profile=profile,
            )
        elif not vpc_id:
            if "vpcs" not in locals():
                with console.status("Loading AWS resources (VPCs, key pairs, S3 buckets)..."):
                    vpcs, keypairs, buckets = get_aws_resources(region, profile)
            vpc = select_from_list(
                vpcs,
                "VPCs",
                formatter=lambda v: f"{v.get('Id')}  {v.get('Cidr')}  {v.get('Name') or ''}".strip(),
            )
            vpc_id = vpc if isinstance(vpc, str) else vpc.get("Id")

    if subnet_param and not subnet_id:
        subnets = get_subnets_for_vpc(vpc_id, region, profile)
        subnet = select_from_list(subnets, "subnets",
            formatter=lambda s: f"{s.get('Id')}  {s.get('Cidr')}  {s.get('Az')}  public={s.get('Public')}  {s.get('Name') or ''}".strip())
        subnet_id = subnet if isinstance(subnet, str) else subnet.get("Id")

    if key_param and not key_name:
        if 'keypairs' not in locals():
            with console.status("Loading AWS resources (VPCs, key pairs, S3 buckets)..."):
                _, keypairs, buckets = get_aws_resources(region, profile)
        key_name = select_from_list(keypairs, "EC2 key pairs")
        
    if s3_param and not s3_bucket:
        if 'buckets' not in locals():
            with console.status("Loading AWS resources (VPCs, key pairs, S3 buckets)..."):
                vpcs, keypairs, buckets = get_aws_resources(region, profile)
        s3_bucket = select_from_list(buckets, "S3 buckets")

    if security_group_param and not security_group_id:
        security_groups = get_security_groups_for_vpc(vpc_id, region, profile)
        security_groups.insert(0, {"Id": "", "Name": "Auto-create new security group", "Description": "Let CloudFormation create a new security group"})
        
        security_group = select_from_list(security_groups, "security groups",
            formatter=lambda sg: f"{sg.get('Id') or 'auto-create'}  {sg.get('Name')}  {sg.get('Description') or ''}".strip())
        security_group_id = security_group if isinstance(security_group, str) else security_group.get("Id")

    # Get CIDR
    cidr = ""
    if cidr_param:
        if desktop_access_cidr:
            cidr = desktop_access_cidr.strip()
        else:
            default_cidr = get_public_cidr()
            if defaults.get(cidr_param, "") == "0.0.0.0/0":
                default_cidr = default_cidr or ""
            while True:
                cidr = questionary.text("Desktop access CIDR (e.g. 1.2.3.4/32)", default=default_cidr or "").ask()
                if cidr is None:
                    console.print("[red]Cancelled.[/red]")
                    return 1
                if cidr.strip():
                    cidr = cidr.strip()
                    if cidr == "0.0.0.0/0":
                        console.print("[red]❌ Desktop access CIDR cannot be 0.0.0.0/0.[/red]")
                        continue
                    break

    cidr_v6 = None
    if ipv6_param and desktop_access_cidr_v6:
        cidr_v6 = desktop_access_cidr_v6.strip()

    install_desktop = None
    if "InstallDesktop" in param_names:
        install_desktop = "false" if skip_desktop_install else defaults.get("InstallDesktop", "true")

    # Optional parameters
    if ami_type_param and not ami_type:
        ami_type = prompt_optional_choice(f"AMI type ({ami_type_param})", 
            allowed.get(ami_type_param), defaults.get(ami_type_param))
    if instance_type_param and not instance_type:
        instance_type = prompt_optional_choice(f"Instance type ({instance_type_param})", 
            allowed.get(instance_type_param), defaults.get(instance_type_param))
    if public_ip_param and not public_ip:
        public_ip = prompt_optional_choice(f"Desktop has public IP ({public_ip_param})", 
            allowed.get(public_ip_param), defaults.get(public_ip_param))
    if assign_static_ip_param and not assign_static_ip:
        assign_static_ip = prompt_optional_choice(f"Assign static IP ({assign_static_ip_param})",
            allowed.get(assign_static_ip_param), defaults.get(assign_static_ip_param))
    if enable_efs_param and not enable_efs:
        enable_efs = prompt_optional_choice(f"Enable EFS ({enable_efs_param})", 
            allowed.get(enable_efs_param), defaults.get(enable_efs_param))
    if desktop_flavor_param and not desktop_flavor:
        if install_desktop == "true":
            desktop_flavor = prompt_optional_choice(f"Desktop flavor ({desktop_flavor_param})",
                allowed.get(desktop_flavor_param), defaults.get(desktop_flavor_param, "xfce4"))
        else:
            desktop_flavor = defaults.get(desktop_flavor_param, "xfce4")
    if driver_type_param and not driver_type:
        driver_type = prompt_optional_choice(f"Driver type ({driver_type_param})",
            allowed.get(driver_type_param), defaults.get(driver_type_param))
    if listen_port_param and not listen_port:
        default_listen = str(defaults.get(listen_port_param, "8443"))
        listen_port = questionary.text(f"DCV listen port ({listen_port_param})", default=default_listen).ask()
        if listen_port is None:
            console.print("[red]Cancelled.[/red]")
            return 1
        listen_port = listen_port.strip() or default_listen
    if tesla_driver_param and not tesla_driver_version and driver_type == "NVIDIA-Tesla":
        default_tesla = str(defaults.get(tesla_driver_param, ""))
        if non_interactive:
            tesla_driver_version = default_tesla
        else:
            tesla_driver_version = questionary.text(f"Tesla driver version ({tesla_driver_param})", default=default_tesla).ask()
            if tesla_driver_version is None:
                console.print("[red]Cancelled.[/red]")
                return 1
            tesla_driver_version = tesla_driver_version.strip() or default_tesla

    allow_ssh_value = allow_ssh_port
    if allow_ssh_param and not allow_ssh_value:
        default_allow_ssh = defaults.get(allow_ssh_param)
        if ssh_public_key and default_allow_ssh == "No":
            default_allow_ssh = "Yes"
        if non_interactive:
            allow_ssh_value = default_allow_ssh
        else:
            allow_ssh_value = prompt_optional_choice(
                f"Allow SSH inbound ({allow_ssh_param})",
                allowed.get(allow_ssh_param),
                default_allow_ssh,
            )

    ssh_public_key_value = ""
    if template_supports_ssh_key:
        if ssh_public_key:
            ssh_public_key_value = resolve_ssh_public_key(ssh_public_key)
        elif allow_ssh_value == "Yes" and not non_interactive:
            ssh_public_key_value = prompt_ssh_public_key()

    allow_rdp_value = allow_rdp_port
    if rdp_param and not allow_rdp_value:
        allow_rdp_value = prompt_optional_choice(f"Allow RDP inbound ({rdp_param})",
            allowed.get(rdp_param), defaults.get(rdp_param))

    ebs_value = None
    if ebs_size_param:
        if ebs_size:
            ebs_value = str(ebs_size)
        else:
            ebs_value = questionary.text(f"EBS volume size in GB ({ebs_size_param})", 
                default=str(defaults.get(ebs_size_param, "64"))).ask()
            if ebs_value is None:
                console.print("[red]Cancelled.[/red]")
                return 1

    ubuntu_override = ""
    if ubuntu_override_param:
        if ubuntu_ami_override is None and not non_interactive:
            ubuntu_override = questionary.text("Ubuntu AMI override (leave blank to use default)", default="").ask()
            if ubuntu_override is None:
                console.print("[red]Cancelled.[/red]")
                return 1
            ubuntu_override = ubuntu_override.strip()
        else:
            ubuntu_override = ubuntu_ami_override.strip() if ubuntu_ami_override else ""

    userdata_script_value = ""
    if userdata_param:
        if userdata_script_url:
            userdata_script_value = userdata_script_url.strip()
        else:
            userdata_script_value = defaults.get(userdata_param, "")

    debug_value = None
    if debug_param:
        debug_value = "true" if debug else "false"

    # Build parameters
    parameters = []

    def add_parameter(param_name, value, allow_empty=False):
        if not param_name:
            return
        if value is None:
            if allow_empty:
                value = ""
            else:
                return
        parameters.append(f"ParameterKey={param_name},ParameterValue={value}")

    add_parameter(s3_param, s3_bucket)
    add_parameter(vpc_param, vpc_id)
    add_parameter(subnet_param, subnet_id)
    add_parameter(cidr_param, cidr)
    if cidr_v6:
        add_parameter(ipv6_param, cidr_v6)
    add_parameter(key_param, key_name)
    add_parameter(ubuntu_override_param, ubuntu_override, allow_empty=True)
    add_parameter(userdata_param, userdata_script_value, allow_empty=True)
    add_parameter(debug_param, debug_value)
    if install_desktop is not None:
        add_parameter("InstallDesktop", install_desktop)
    add_parameter(desktop_flavor_param, desktop_flavor)
    add_parameter(ebs_size_param, ebs_value)
    add_parameter(security_group_param, security_group_id if security_group_id is not None else "")
    add_parameter(slack_param, slack_webhook_url or "")
    add_parameter(stack_suffix_param, stack_name_suffix or "")
    add_parameter(user_param, user or "")
    add_parameter(ubuntu_password_param, ubuntu_password or "")
    add_parameter(instance_role_param, instance_role_name or "")
    add_parameter(instance_profile_param, instance_profile_name or "")
    add_parameter(project_tag_param, project_tag or "")
    add_parameter(ssh_public_key_param, ssh_public_key_value)
    add_parameter(allow_ssh_param, allow_ssh_value)
    add_parameter(rdp_param, allow_rdp_value)
    add_parameter(listen_port_param, listen_port)
    add_parameter(driver_type_param, driver_type)
    add_parameter(tesla_driver_param, tesla_driver_version)
    add_parameter(assign_static_ip_param, assign_static_ip)

    # Add optional parameters
    for param_key, param_value in [
        (ami_type_param, ami_type),
        (instance_type_param, instance_type), 
        (public_ip_param, public_ip),
        (enable_efs_param, enable_efs)
    ]:
        add_parameter(param_key, param_value)

    # Build and display command
    cmd = [
        "aws", "cloudformation", "create-stack",
        "--stack-name", stack_name,
        "--capabilities", "CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND",
    ]
    if parameters:
        cmd += ["--parameters"] + parameters
    if is_template_url:
        cmd += ["--template-url", template_source]
    else:
        cmd += ["--template-body", f"file://{template_source}"]

    if project_tag or stack_name_suffix:
        tag_value = project_tag.strip() if project_tag else stack_name_suffix.strip()
        cmd += ["--tags", f"Key=wri:project,Value={tag_value}"]

    if region:
        cmd += ["--region", region]
    if profile:
        cmd += ["--profile", profile]

    console.print("\n[bold cyan]CloudFormation command:[/bold cyan]")
    console.print(" ".join(cmd))

    if dry_run:
        return 0

    if not confirm("Create stack now?"):
        console.print("[red]Cancelled.[/red]")
        return 1

    # Create stack and wait for completion
    subprocess.check_call(cmd)
    console.print(f"\n[yellow]Stack '{stack_name}' creation initiated. Waiting for completion...[/yellow]")
    
    wait_cmd = ["aws", "cloudformation", "wait", "stack-create-complete", "--stack-name", stack_name]
    if region:
        wait_cmd += ["--region", region]
    if profile:
        wait_cmd += ["--profile", profile]
    
    try:
        subprocess.check_call(wait_cmd)
        console.print(f"[green]✅ Stack '{stack_name}' created successfully![/green]")
        
        # Get final stack info and display
        with console.status("Loading stack info..."):
            _, instance_id, _ = get_stack_info(stack_name, region, profile)
        password_was_provided = 'ubuntu_password' in ctx.params and ctx.params['ubuntu_password'] is not None
        display_instance_info(
            instance_id,
            key_name,
            region,
            profile,
            is_new_stack=True,
            password_set=password_was_provided,
            show_dcv=install_desktop == "true" if install_desktop is not None else True,
            is_windows=is_windows_template,
        )
            
    except subprocess.CalledProcessError as e:
        console.print(f"[red]❌ Stack creation failed or timed out: {e}[/red]")
        return 1
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
