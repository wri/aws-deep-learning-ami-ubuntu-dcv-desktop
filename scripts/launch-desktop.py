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


def get_stack_info(stack_name, region=None, profile=None):
    """Get stack information and extract instance ID and key name."""
    try:
        stack_raw = run_aws([
            "cloudformation", "describe-stacks", 
            "--stack-name", stack_name,
            "--output", "json"
        ], region, profile)
        
        stack_info = json.loads(stack_raw)
        stack_status = stack_info["Stacks"][0]["StackStatus"]
        outputs = stack_info["Stacks"][0].get("Outputs", [])
        
        # Extract instance ID and key name from outputs
        instance_id = None
        key_name = None
        for output in outputs:
            if output.get("OutputKey") == "InstanceId":
                instance_id = output.get("OutputValue")
            elif output.get("OutputKey") == "KeyPairName":
                key_name = output.get("OutputValue")
        
        # Fallback: get instance ID from resources if not in outputs
        if not instance_id:
            try:
                instance_id = run_aws([
                    "cloudformation", "describe-stack-resource",
                    "--stack-name", stack_name,
                    "--logical-resource-id", "DesktopInstance",
                    "--query", "StackResourceDetail.PhysicalResourceId",
                    "--output", "text"
                ], region, profile).strip()
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


def get_subnets_for_vpc(vpc_id, region=None, profile=None):
    """Get subnets for a specific VPC."""
    subnets_raw = run_aws([
        "ec2", "describe-subnets",
        "--filters", f"Name=vpc-id,Values={vpc_id}",
        "--query", "Subnets[].{Id:SubnetId,Cidr:CidrBlock,Az:AvailabilityZone,Public:MapPublicIpOnLaunch,Name:Tags[?Key=='Name']|[0].Value}",
        "--output", "json"
    ], region, profile)
    return json.loads(subnets_raw)


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


def display_instance_info(instance_id, key_name, region=None, profile=None, is_new_stack=False):
    """Display instance information including SSH and DCV connection details."""
    if not instance_id:
        print("❌ Could not find DesktopInstance resource")
        return
    
    print(f"🖥️  Instance ID: {instance_id}")
    
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
        
        print(f"📡 Instance State: {state}")
        print(f"🌐 Public IP: {public_ip or 'none'}")
        print(f"🏠 Private IP: {private_ip}")
        
        if public_ip:
            if key_name:
                print(f"🔑 SSH Connection:")
                print(f"   ssh -i ~/.ssh/{key_name}.pem ubuntu@{public_ip}")
            else:
                print(f"🔑 SSH Connection (replace KEY_NAME with your key):")
                print(f"   ssh -i ~/.ssh/KEY_NAME.pem ubuntu@{public_ip}")
            
            print(f"🖥️  DCV Connection:")
            print(f"   https://{public_ip}:8443")
            
            if is_new_stack:
                print(f"")
                print(f"⚠️  REMINDER: Change the default password on first login!")
                print(f"   Default username: ubuntu")
                print(f"   Run: sudo passwd ubuntu")
            
    except subprocess.CalledProcessError:
        print("⚠️  Could not retrieve instance network information")


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

    # Check if stack already exists
    stack_status, instance_id, key_name = get_stack_info(stack_name, region, profile)
    
    if stack_status:
        print(f"⚠️  Stack '{stack_name}' already exists with status: {stack_status}")
        
        if stack_status in ["CREATE_COMPLETE", "UPDATE_COMPLETE"]:
            display_instance_info(instance_id, key_name, region, profile, is_new_stack=False)
            return 0
        else:
            print(f"❌ Stack exists but is in state '{stack_status}'. Cannot proceed with creation.")
            return 1

    # Parse template and get AWS resources
    defaults, allowed = parse_template_options(template)
    vpcs, keypairs, buckets = get_aws_resources(region, profile)

    # Interactive prompts
    vpc = select_from_list(vpcs, "VPCs", 
        formatter=lambda v: f"{v.get('Id')}  {v.get('Cidr')}  {v.get('Name') or ''}".strip())
    vpc_id = vpc if isinstance(vpc, str) else vpc.get("Id")

    subnets = get_subnets_for_vpc(vpc_id, region, profile)
    subnet = select_from_list(subnets, "subnets",
        formatter=lambda s: f"{s.get('Id')}  {s.get('Cidr')}  {s.get('Az')}  public={s.get('Public')}  {s.get('Name') or ''}".strip())
    subnet_id = subnet if isinstance(subnet, str) else subnet.get("Id")

    key_name = select_from_list(keypairs, "EC2 key pairs")
    s3_bucket = select_from_list(buckets, "S3 buckets")

    # Get CIDR
    default_cidr = get_public_cidr()
    while True:
        cidr = questionary.text("Desktop access CIDR (e.g. 1.2.3.4/32)", default=default_cidr or "").ask()
        if cidr is None:
            print("Cancelled.")
            return 1
        if cidr.strip():
            cidr = cidr.strip()
            break

    # Optional parameters
    ami_type = prompt_optional_choice("AMI type (AWSUbuntuAMIType)", 
        allowed.get("AWSUbuntuAMIType"), defaults.get("AWSUbuntuAMIType"))
    instance_type = prompt_optional_choice("Instance type (DesktopInstanceType)", 
        allowed.get("DesktopInstanceType"), defaults.get("DesktopInstanceType"))
    public_ip = prompt_optional_choice("Desktop has public IP (DesktopHasPublicIpAddress)", 
        allowed.get("DesktopHasPublicIpAddress"), defaults.get("DesktopHasPublicIpAddress", "true"))
    enable_efs = prompt_optional_choice("Enable EFS (EnableEFS)", 
        allowed.get("EnableEFS"), defaults.get("EnableEFS", "false"))

    ebs_value = questionary.text("EBS volume size in GB (EbsVolumeSize)", 
        default=str(defaults.get("EbsVolumeSize", "64"))).ask()
    if ebs_value is None:
        print("Cancelled.")
        return 1

    ubuntu_override = questionary.text("Ubuntu AMI override (leave blank to use default)", default="").ask()
    if ubuntu_override is None:
        print("Cancelled.")
        return 1

    security_group_id = questionary.text("Desktop security group ID (DesktopSecurityGroupId, leave blank to auto-create)", default="").ask()
    if security_group_id is None:
        print("Cancelled.")
        return 1

    # Build parameters
    parameters = [
        f"ParameterKey=S3Bucket,ParameterValue={s3_bucket}",
        f"ParameterKey=DesktopVpcId,ParameterValue={vpc_id}",
        f"ParameterKey=DesktopVpcSubnetId,ParameterValue={subnet_id}",
        f"ParameterKey=DesktopAccessCIDR,ParameterValue={cidr}",
        f"ParameterKey=KeyName,ParameterValue={key_name}",
        f"ParameterKey=UbuntuAMIOverride,ParameterValue={ubuntu_override.strip()}",
        f"ParameterKey=EbsVolumeSize,ParameterValue={ebs_value.strip()}",
        f"ParameterKey=DesktopSecurityGroupId,ParameterValue={security_group_id.strip()}",
    ]

    # Add optional parameters
    for param_key, param_value in [
        ("AWSUbuntuAMIType", ami_type),
        ("DesktopInstanceType", instance_type), 
        ("DesktopHasPublicIpAddress", public_ip),
        ("EnableEFS", enable_efs)
    ]:
        if param_value:
            parameters.append(f"ParameterKey={param_key},ParameterValue={param_value}")

    # Build and display command
    cmd = [
        "aws", "cloudformation", "create-stack",
        "--stack-name", stack_name,
        "--template-body", f"file://{template}",
        "--capabilities", "CAPABILITY_NAMED_IAM",
        "--parameters"
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

    # Create stack and wait for completion
    subprocess.check_call(cmd)
    print(f"\nStack '{stack_name}' creation initiated. Waiting for completion...")
    
    wait_cmd = ["aws", "cloudformation", "wait", "stack-create-complete", "--stack-name", stack_name]
    if region:
        wait_cmd += ["--region", region]
    if profile:
        wait_cmd += ["--profile", profile]
    
    try:
        subprocess.check_call(wait_cmd)
        print(f"✅ Stack '{stack_name}' created successfully!")
        
        # Get final stack info and display
        _, instance_id, _ = get_stack_info(stack_name, region, profile)
        display_instance_info(instance_id, key_name, region, profile, is_new_stack=True)
            
    except subprocess.CalledProcessError as e:
        print(f"❌ Stack creation failed or timed out: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
