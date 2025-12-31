#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["click>=8.1.0", "questionary>=2.0.1"]
# ///
import json
import os
import re
import subprocess
import sys
import urllib.request
import click
import questionary
from questionary import Choice


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
        sys.exit(exc.returncode)
    return output


def parse_template_options(template_path):
    if not os.path.exists(template_path):
        return {}, {}

    defaults = {}
    allowed = {}
    current = None
    in_allowed = False

    with open(template_path, "r", encoding="utf-8") as handle:
        for line in handle:
            key_match = re.match(r"^\s{2}([A-Za-z0-9]+):\s*$", line)
            if key_match:
                current = key_match.group(1)
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

    return defaults, allowed


def select_from_list(items, label, formatter=None):
    if formatter is None:
        formatter = lambda x: str(x)

    if not items:
        while True:
            value = questionary.text(f"No {label} found. Enter {label} manually:").ask()
            if value is None:
                print("Cancelled.")
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
        print("Cancelled.")
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
            print("Cancelled.")
            sys.exit(1)
        if selection == "Enter custom value":
            value = questionary.text(f"{label} custom value:").ask()
            return value.strip() if value else None
        if selection == default_value:
            return None
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


def confirm(prompt):
    value = questionary.confirm(prompt, default=False).ask()
    return bool(value)


@click.command()
@click.option("--stack-name", default="deep-learning-ubuntu-desktop", show_default=True)
@click.option(
    "--template",
    default="deep-learning-ubuntu-desktop.yaml",
    show_default=True,
    help="Path to the CloudFormation template.",
)
@click.option("--region", help="AWS region (overrides AWS config).")
@click.option("--profile", help="AWS CLI profile to use.")
@click.option("--dry-run", is_flag=True, help="Print command only.")
def main(stack_name, template, region, profile, dry_run):
    """Interactive launcher for deep-learning-ubuntu-desktop CloudFormation stack."""

    # Check if stack already exists first, before any user interaction
    try:
        check_cmd = [
            "aws",
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            stack_name,
        ]
        if region:
            check_cmd += ["--region", region]
        if profile:
            check_cmd += ["--profile", profile]
        
        existing_stack = subprocess.check_output(check_cmd, stderr=subprocess.DEVNULL, text=True)
        stack_info = json.loads(existing_stack)
        stack_status = stack_info["Stacks"][0]["StackStatus"]
        
        print(f"⚠️  Stack '{stack_name}' already exists with status: {stack_status}")
        
        if stack_status in ["CREATE_COMPLETE", "UPDATE_COMPLETE"]:
            # Stack exists and is in good state, get instance ID
            outputs = stack_info["Stacks"][0].get("Outputs", [])
            instance_id = None
            
            # First try to get from outputs (for new stacks)
            for output in outputs:
                if output.get("OutputKey") == "InstanceId":
                    instance_id = output.get("OutputValue")
                    break
            
            # If not found in outputs, get from DesktopInstance resource (for old stacks)
            if not instance_id:
                try:
                    resources_cmd = [
                        "aws",
                        "cloudformation",
                        "describe-stack-resource",
                        "--stack-name",
                        stack_name,
                        "--logical-resource-id",
                        "DesktopInstance",
                        "--query",
                        "StackResourceDetail.PhysicalResourceId",
                        "--output",
                        "text",
                    ]
                    if region:
                        resources_cmd += ["--region", region]
                    if profile:
                        resources_cmd += ["--profile", profile]
                    
                    instance_id = subprocess.check_output(resources_cmd, text=True).strip()
                except subprocess.CalledProcessError:
                    pass
            
            if instance_id:
                print(f"🖥️  Instance ID: {instance_id}")
            else:
                print("❌ Could not find DesktopInstance resource")
            return 0
        else:
            print(f"❌ Stack exists but is in state '{stack_status}'. Cannot proceed with creation.")
            return 1
            
    except subprocess.CalledProcessError:
        # Stack doesn't exist, proceed with creation
        pass

    defaults, allowed = parse_template_options(template)

    vpcs_raw = run_aws(
        [
            "ec2",
            "describe-vpcs",
            "--query",
            "Vpcs[].{Id:VpcId,Cidr:CidrBlock,Name:Tags[?Key=='Name']|[0].Value}",
            "--output",
            "json",
        ],
        region=region,
        profile=profile,
    )
    vpcs = json.loads(vpcs_raw)
    vpc = select_from_list(
        vpcs,
        "VPCs",
        formatter=lambda v: f"{v.get('Id')}  {v.get('Cidr')}  {v.get('Name') or ''}".strip(),
    )
    vpc_id = vpc if isinstance(vpc, str) else vpc.get("Id")

    subnets_raw = run_aws(
        [
            "ec2",
            "describe-subnets",
            "--filters",
            f"Name=vpc-id,Values={vpc_id}",
            "--query",
            "Subnets[].{Id:SubnetId,Cidr:CidrBlock,Az:AvailabilityZone,Public:MapPublicIpOnLaunch,Name:Tags[?Key=='Name']|[0].Value}",
            "--output",
            "json",
        ],
        region=region,
        profile=profile,
    )
    subnets = json.loads(subnets_raw)
    subnet = select_from_list(
        subnets,
        "subnets",
        formatter=lambda s: (
            f"{s.get('Id')}  {s.get('Cidr')}  {s.get('Az')}  "
            f"public={s.get('Public')}  {s.get('Name') or ''}"
        ).strip(),
    )
    subnet_id = subnet if isinstance(subnet, str) else subnet.get("Id")

    keys_raw = run_aws(
        [
            "ec2",
            "describe-key-pairs",
            "--query",
            "KeyPairs[].KeyName",
            "--output",
            "json",
        ],
        region=region,
        profile=profile,
    )
    keypairs = json.loads(keys_raw)
    key_name = select_from_list(keypairs, "EC2 key pairs")

    buckets_raw = run_aws(
        ["s3api", "list-buckets", "--query", "Buckets[].Name", "--output", "json"],
        region=region,
        profile=profile,
    )
    buckets = json.loads(buckets_raw)
    s3_bucket = select_from_list(buckets, "S3 buckets")

    default_cidr = get_public_cidr()
    cidr_prompt = "Desktop access CIDR (e.g. 1.2.3.4/32)"
    while True:
        cidr = questionary.text(cidr_prompt, default=default_cidr or "").ask()
        if cidr is None:
            print("Cancelled.")
            return 1
        cidr = cidr.strip()
        if cidr:
            break

    ami_type = prompt_optional_choice(
        "AMI type (AWSUbuntuAMIType)",
        allowed.get("AWSUbuntuAMIType"),
        defaults.get("AWSUbuntuAMIType"),
    )

    instance_type = prompt_optional_choice(
        "Instance type (DesktopInstanceType)",
        allowed.get("DesktopInstanceType"),
        defaults.get("DesktopInstanceType"),
    )

    public_ip_default = defaults.get("DesktopHasPublicIpAddress", "true")
    public_ip = prompt_optional_choice(
        "Desktop has public IP (DesktopHasPublicIpAddress)",
        allowed.get("DesktopHasPublicIpAddress"),
        public_ip_default,
    )

    enable_efs_default = defaults.get("EnableEFS", "false")
    enable_efs = prompt_optional_choice(
        "Enable EFS (EnableEFS)",
        allowed.get("EnableEFS"),
        enable_efs_default,
    )

    ebs_default = defaults.get("EbsVolumeSize", "64")
    ebs_value = questionary.text(
        "EBS volume size in GB (EbsVolumeSize)",
        default=str(ebs_default),
    ).ask()
    if ebs_value is None:
        print("Cancelled.")
        return 1
    ebs_value = ebs_value.strip()

    ubuntu_override = questionary.text(
        "Ubuntu AMI override (leave blank to use default)",
        default="",
    ).ask()
    if ubuntu_override is None:
        print("Cancelled.")
        return 1
    ubuntu_override = ubuntu_override.strip()

    security_group_id = questionary.text(
        "Desktop security group ID (DesktopSecurityGroupId, leave blank to auto-create)",
        default="",
    ).ask()
    if security_group_id is None:
        print("Cancelled.")
        return 1
    security_group_id = security_group_id.strip()

    parameters = [
        f"ParameterKey=S3Bucket,ParameterValue={s3_bucket}",
        f"ParameterKey=DesktopVpcId,ParameterValue={vpc_id}",
        f"ParameterKey=DesktopVpcSubnetId,ParameterValue={subnet_id}",
        f"ParameterKey=DesktopAccessCIDR,ParameterValue={cidr}",
        f"ParameterKey=KeyName,ParameterValue={key_name}",
        f"ParameterKey=UbuntuAMIOverride,ParameterValue={ubuntu_override}",
        f"ParameterKey=EbsVolumeSize,ParameterValue={ebs_value}",
        f"ParameterKey=DesktopSecurityGroupId,ParameterValue={security_group_id}",
    ]

    if ami_type:
        parameters.append(f"ParameterKey=AWSUbuntuAMIType,ParameterValue={ami_type}")
    if instance_type:
        parameters.append(f"ParameterKey=DesktopInstanceType,ParameterValue={instance_type}")
    if public_ip:
        parameters.append(f"ParameterKey=DesktopHasPublicIpAddress,ParameterValue={public_ip}")
    if enable_efs:
        parameters.append(f"ParameterKey=EnableEFS,ParameterValue={enable_efs}")

    cmd = [
        "aws",
        "cloudformation",
        "create-stack",
        "--stack-name",
        stack_name,
        "--template-body",
        f"file://{template}",
        "--capabilities",
        "CAPABILITY_NAMED_IAM",
        "--parameters",
    ] + parameters

    if region:
        cmd += ["--region", region]
    if profile:
        cmd += ["--profile", profile]

    print("\nCloudFormation command:")
    print(" ".join(cmd))

    if dry_run:
        return 0

    if not confirm("Create stack now?"):
        print("Cancelled.")
        return 1

    subprocess.check_call(cmd)
    
    print(f"\nStack '{stack_name}' creation initiated. Waiting for completion...")
    
    # Wait for stack completion
    wait_cmd = [
        "aws",
        "cloudformation",
        "wait",
        "stack-create-complete",
        "--stack-name",
        stack_name,
    ]
    
    if region:
        wait_cmd += ["--region", region]
    if profile:
        wait_cmd += ["--profile", profile]
    
    try:
        subprocess.check_call(wait_cmd)
        print(f"✅ Stack '{stack_name}' created successfully!")
        
        # Get stack outputs
        outputs_cmd = [
            "aws",
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            stack_name,
            "--query",
            "Stacks[0].Outputs",
            "--output",
            "json",
        ]
        
        if region:
            outputs_cmd += ["--region", region]
        if profile:
            outputs_cmd += ["--profile", profile]
        
        outputs_raw = subprocess.check_output(outputs_cmd, text=True)
        outputs = json.loads(outputs_raw)
        
        # Find and print instance ID
        instance_id = None
        for output in outputs:
            if output.get("OutputKey") == "InstanceId":
                instance_id = output.get("OutputValue")
                break
        
        if instance_id:
            print(f"🖥️  Instance ID: {instance_id}")
        else:
            print("⚠️  Instance ID not found in stack outputs")
            
    except subprocess.CalledProcessError as e:
        print(f"❌ Stack creation failed or timed out: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
