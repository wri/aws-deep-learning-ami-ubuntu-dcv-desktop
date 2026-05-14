# AWS Deep Learning Desktop with Amazon DCV

Launch an AWS desktop environment with [Amazon DCV](https://aws.amazon.com/hpc/dcv/).

## Overview

Two CloudFormation templates are provided:

| Template | Description |
|----------|-------------|
| [deep-learning-ubuntu-desktop.yaml](deep-learning-ubuntu-desktop.yaml) | Ubuntu Pro desktop with GPU/Trainium/Inferentia support |
| [WIndowsServer-NICE-DCV.yaml](WIndowsServer-NICE-DCV.yaml) | Windows Server desktop with DCV |

**Supported AMIs (Ubuntu template):**
* Ubuntu Server Pro 24.04 LTS (Default)
* Ubuntu Server Pro 22.04 LTS

**Supported EC2 Instance Types:**
* **Trainium/Inferentia:** [trn1](https://aws.amazon.com/ec2/instance-types/trn1/), [trn2](https://aws.amazon.com/ec2/instance-types/trn2/), [inf2](https://aws.amazon.com/ec2/instance-types/inf2/)
* **GPU:** [g4](https://aws.amazon.com/ec2/instance-types/g4/), [g5](https://aws.amazon.com/ec2/instance-types/g5/), [g6](https://aws.amazon.com/ec2/instance-types/g6/), [p3](https://aws.amazon.com/ec2/instance-types/p3/), [p4](https://aws.amazon.com/ec2/instance-types/p4/), [p5](https://aws.amazon.com/ec2/instance-types/p5/)
* **General Purpose:** Selected [m5](https://aws.amazon.com/ec2/instance-types/m5/), [c5](https://aws.amazon.com/ec2/instance-types/c5/), [r5](https://aws.amazon.com/ec2/instance-types/r5/)

(others may or may not work)

## Getting Started

### Prerequisites

* [AWS Account](https://aws.amazon.com/account/) with [Administrator job function](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_job-functions.html) access
* [uv](https://docs.astral.sh/uv/) installed locally (for running utility scripts)

**Supported AWS Regions:**
us-east-1, us-east-2, us-west-2, eu-west-1, eu-central-1, ap-southeast-1, ap-southeast-2, ap-northeast-1, ap-northeast-2, ap-south-1

**Note:** Not all EC2 instance types are available in all [Availability Zones](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-regions-availability-zones.html).

### Setup Steps

1. **Select your [AWS Region](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-regions-availability-zones.html)** from the supported regions above

2. **EC2 Key Pair:** [Create an EC2 key pair](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-key-pairs.html#prepare-key-pair) in your target region — the Ubuntu template requires one

3. **Clone this repository:**
   ```bash
   git clone <repository-url>
   cd aws-deep-learning-ami-ubuntu-dcv-desktop
   ```

### Launch the Desktop

Use the interactive `utils/launch-desktop.py` script (recommended):
```bash
uv run utils/launch-desktop.py
```

Or pass parameters directly (non-interactive):
```bash
uv run utils/launch-desktop.py \
  --region us-west-2 \
  --stack-name-suffix my-desktop \
  --vpc-id vpc-xxxxxxxx \
  --subnet-id subnet-xxxxxxxx \
  --desktop-access-cidr YOUR.IP.ADDRESS/32 \
  --instance-type g5.xlarge \
  --public-ip Yes \
  --assign-static-ip Yes \
  --ebs-size 200
```

> **Tip:** Find your public IP at [checkip.amazonaws.com](http://checkip.amazonaws.com/). Use `/32` to restrict access to a single IP.

For the Windows template:
```bash
uv run utils/launch-desktop.py \
  --template WIndowsServer-NICE-DCV.yaml \
  --region us-west-2 \
  --stack-name-suffix windows-desktop \
  --vpc-id vpc-xxxxxxxx \
  --subnet-id subnet-xxxxxxxx \
  --desktop-access-cidr YOUR.IP.ADDRESS/32 \
  --instance-type t3.medium
```

Alternatively, deploy directly with the AWS CLI:
```bash
aws cloudformation create-stack \
  --stack-name deep-learning-desktop \
  --template-body file://deep-learning-ubuntu-desktop.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=DesktopVpcId,ParameterValue=vpc-xxxxxxxx \
    ParameterKey=DesktopVpcSubnetId,ParameterValue=subnet-xxxxxxxx \
    ParameterKey=DesktopInstanceType,ParameterValue=g5.xlarge \
    ParameterKey=DesktopAccessCIDR,ParameterValue=YOUR.IP.ADDRESS/32
```

**Important:** The template creates [IAM](https://aws.amazon.com/iam/) resources. When deploying via Console, check *"I acknowledge that AWS CloudFormation might create IAM resources"*; via CLI, use `--capabilities CAPABILITY_NAMED_IAM`.

### Connect via SSH

1. Wait for stack status `CREATE_COMPLETE` in CloudFormation console
2. [Connect via SSH](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/AccessingInstancesLinux.html) as user `ubuntu` using your key pair

**First-time Setup:**
* If you see `"Cloud init in progress! Logs: /var/log/cloud-init-output.log"`, disconnect and wait ~15 minutes. The desktop installs Amazon DCV server and reboots automatically.
* When you see `Deep Learning Desktop is Ready!`, set a password:
  ```bash
  sudo passwd ubuntu
  ```

**Troubleshooting:** Check `/var/log/cloud-init-output.log` and `~/userdata.log`. Most transient failures can be fixed by rebooting the instance.

### Connect via Amazon DCV Client

1. Download and install the [Amazon DCV client](https://docs.aws.amazon.com/dcv/latest/userguide/client.html)
2. Login as user `ubuntu`
3. Do not upgrade the OS version when prompted on first login

**Session Manager tunnel (no public IP required):**
```bash
aws ssm start-session \
  --region us-west-2 \
  --target i-xxxxxxxxxxxxxxxxx \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8443"],"localPortNumber":["8443"]}'
```
Then connect your DCV client to `localhost:8443`.

## Using the Desktop

### Generative AI Inference Testing

The desktop provides inference testing frameworks for LLMs and embedding models. See [Inference Testing Guide](./gen-ai-inference-testing/README.md) for complete documentation.

**Supported Inference Servers:**
* [Triton Inference Server](https://github.com/triton-inference-server) - NVIDIA's production inference server
* [DJL Serving](https://docs.djl.ai/master/docs/serving/serving/docs/lmi/index.html) - Deep Java Library with LMI
* OpenAI-compatible Server - Standard OpenAI API interface

**Supported Backends:**
* [vLLM](https://github.com/vllm-project/vllm) - GPU and Neuron
* [TensorRT-LLM](https://github.com/NVIDIA/TensorRT-LLM) - Optimized for NVIDIA GPUs
* Custom Python backends for embeddings

### Generative AI Training Testing

Four frameworks for fine-tuning LLMs with PEFT (LoRA) or full fine-tuning. See [Training Testing Guide](./gen-ai-training-testing/README.md) for complete documentation.

| Framework | Key Features |
|-----------|--------------|
| [NeMo 2.0](./gen-ai-training-testing/nemo2/README.md) | Tensor/pipeline parallelism, Megatron-LM optimizations |
| [PyTorch Lightning](./gen-ai-training-testing/ptl/README.md) | Full control, flexible callbacks |
| [Accelerate](./gen-ai-training-testing/accelerate/README.md) | Simple API, minimal code |
| [Ray Train](./gen-ai-training-testing/ray_train/README.md) | Distributed orchestration, auto-recovery |

### Data Storage

* **[Amazon EBS](https://aws.amazon.com/ebs/):** Root volume (deleted on termination)
* **[Amazon EFS](https://aws.amazon.com/efs/):** Mounted at `/home/ubuntu/efs` (persists after termination)
* **[Amazon FSx for Lustre](https://aws.amazon.com/fsx/):** Optional, mounted at `/home/ubuntu/fsx` (enable via `FSxForLustre` parameter)

**Important:** EBS volumes are deleted on termination. EFS file-systems persist.

## Managing the Desktop

### Stopping and Restarting

You can safely reboot, stop, and restart the desktop instance. EFS (and FSx for Lustre, if enabled) automatically remount on restart.

### Distributed Training

For distributed training workloads requiring EFA, see the [EFA Cluster Guide](efa-cluster/README.md).

### Deleting Resources

Delete the CloudFormation stack when no longer needed. **EFS file-systems are NOT automatically deleted** — remove them manually if no longer needed.

## CloudFormation Parameters (Ubuntu Template)

| Parameter | Description |
|-----------|-------------|
| `AWSUbuntuAMIType` | Ubuntu Pro version: `UbuntuPro2404LTS` (default) or `UbuntuPro2204LTS` |
| `DesktopInstanceType` | EC2 instance type |
| `DesktopVpcId` | VPC ID |
| `DesktopVpcSubnetId` | Subnet ID (public with IGW, or private with NAT) |
| `DesktopAccessCIDR` | IP CIDR for DCV/SSH access (e.g. `YOUR.IP.ADDRESS/32`) |
| `DesktopHasPublicIpAddress` | Assign a public IP (`true`/`false`) |
| `DesktopSecurityGroupId` | *Optional.* Existing security group; leave blank to auto-create |
| `EbsVolumeSize` | Root EBS volume size in GB (default 200) |
| `EbsVolumeType` | EBS volume type (default `gp3`) |
| `EBSOptimized` | Enable EBS-optimized networking (default `true`) |
| `KeyName` | *Optional.* EC2 key pair name for SSH access |
| `UbuntuPassword` | Password for the `ubuntu` user (required for DCV login) |
| `User` | Username component for hostname generation |
| `StackNameSuffix` | Stack name suffix used for hostname generation |
| `ProjectTagValue` | Override value for `wri:project` resource tag |
| `InstanceRoleName` | *Optional.* Existing IAM role name; leave blank to auto-create |
| `InstallDesktop` | Install desktop and DCV components (default `true`) |
| `DesktopFlavor` | Desktop environment to install (default `xfce4`) |
| `EnableEFS` | Create or attach an EFS file-system (default `false`) |
| `EFSFileSystemId` | *Optional.* Existing EFS file-system ID |
| `EFSMountPath` | EFS mount path (default `/home/ubuntu/efs`) |
| `FSxForLustre` | Enable FSx for Lustre (default `false`) |
| `FSxCapacity` | FSx capacity in multiples of 1200 GB (default 1200) |
| `FSxMountPath` | FSx mount path (default `/home/ubuntu/fsx`) |
| `SlackWebhookUrl` | *Optional.* Slack webhook URL for setup notifications |
| `Debug` | Send all log messages to Slack (default `false`) |
| `UserdataScriptUrl` | *Optional.* Override URL for the cloud-init userdata script |
| `UbuntuAMIOverride` | *Optional.* Override the AMI ID |
| `CapacityReservationId` | *Optional.* EC2 capacity reservation ID |

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This project is licensed under the MIT-0 [License](./LICENSE).
