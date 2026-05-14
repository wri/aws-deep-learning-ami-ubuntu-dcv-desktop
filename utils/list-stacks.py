#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.9"
# dependencies = ["click>=8.1.0", "questionary>=2.0.1"]
# ///
import json
import subprocess
import sys
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
@click.option("--region", help="AWS region (overrides AWS config).")
@click.option("--profile", help="AWS CLI profile to use.")
@click.option("--include-deleted", is_flag=True, help="Include DELETE_COMPLETE stacks.")
@click.option("--dry-run", is_flag=True, help="Print delete commands only.")
def main(region, profile, include_deleted, dry_run):
    """List CloudFormation stacks and optionally delete selected stacks."""
    stacks_raw = run_aws(
        [
            "cloudformation",
            "describe-stacks",
            "--query",
            "Stacks[].{Name:StackName,Status:StackStatus,Desc:Description}",
            "--output",
            "json",
        ],
        region=region,
        profile=profile,
    )
    stacks = json.loads(stacks_raw)
    if not include_deleted:
        stacks = [stack for stack in stacks if stack.get("Status") != "DELETE_COMPLETE"]

    if not stacks:
        print("No stacks found.")
        return 0

    choices = []
    for stack in stacks:
        name = stack.get("Name") or ""
        status = stack.get("Status") or ""
        desc = (stack.get("Desc") or "").strip()
        title = f"{name}  {status}"
        if desc:
            title = f"{title}  {desc}"
        choices.append(Choice(title=title, value=name))

    selection = questionary.select(
        "Select stack to delete",
        choices=choices,
    ).ask()
    if selection is None:
        print("Cancelled.")
        return 1
    if not selection:
        print("No stack selected.")
        return 0

    if not confirm(f"Delete selected stack: {selection}?"):
        print("Cancelled.")
        return 1

    cmd = ["aws", "cloudformation", "delete-stack", "--stack-name", selection]
    if region:
        cmd += ["--region", region]
    if profile:
        cmd += ["--profile", profile]
    if dry_run:
        print(" ".join(cmd))
    else:
        subprocess.check_call(cmd)

    if not dry_run:
        wait_cmd = ["aws", "cloudformation", "wait", "stack-delete-complete", "--stack-name", selection]
        if region:
            wait_cmd += ["--region", region]
        if profile:
            wait_cmd += ["--profile", profile]
        subprocess.check_call(wait_cmd)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
