#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: list-vpcs.sh [--region REGION] [--profile PROFILE]
       list-vpcs.sh --vpc-id VPC_ID [--region REGION] [--profile PROFILE]

Lists VPCs in the current account using the AWS CLI.
If no region is provided, the AWS CLI default is used.
If --vpc-id is provided, lists subnets for that VPC instead.
USAGE
}

region=""
profile=""
vpc_id=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)
      region="$2"
      shift 2
      ;;
    --profile)
      profile="$2"
      shift 2
      ;;
    --vpc-id)
      vpc_id="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI not found in PATH" >&2
  exit 1
fi

args=(ec2 describe-vpcs --query 'Vpcs[].{VpcId:VpcId,CidrBlock:CidrBlock,IsDefault:IsDefault,Name:Tags[?Key==`Name`]|[0].Value}' --output table)

if [[ -n "$region" ]]; then
  args+=(--region "$region")
fi

if [[ -n "$profile" ]]; then
  args+=(--profile "$profile")
fi

if [[ -n "$vpc_id" ]]; then
  subnet_args=(ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc_id" --query 'Subnets[].{SubnetId:SubnetId,CidrBlock:CidrBlock,Az:AvailabilityZone,Name:Tags[?Key==`Name`]|[0].Value}' --output table)
  if [[ -n "$region" ]]; then
    subnet_args+=(--region "$region")
  fi
  if [[ -n "$profile" ]]; then
    subnet_args+=(--profile "$profile")
  fi
  aws "${subnet_args[@]}"
else
  aws "${args[@]}"
fi
