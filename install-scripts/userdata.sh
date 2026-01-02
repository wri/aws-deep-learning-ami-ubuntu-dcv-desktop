#!/bin/bash -xe
echo "Cloud init in progress! Logs: /var/log/cloud-init-output.log" > /etc/motd


: "${OVERRIDE_AMI:=}"
: "${SLACK_WEBHOOK_URL:=}"
: "${STACK_NAME:=}"
: "${STACK_NAME_SUFFIX:=}"
: "${USER:=}"
: "${UBUNTU_PASSWORD:=}"
: "${DESKTOP_FLAVOR:=xfce4}"
: "${DEBUG:=false}"

LOG_FILE=/home/ubuntu/userdata.log
slack_notify() {
  local message=$1
  if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
    curl -s -X POST -H 'Content-type: application/json' \
      --data "{\"text\":\"${message}\"}" \
      "$SLACK_WEBHOOK_URL" >/dev/null || true
  fi
}
log() {
  local message=$1
  local notify_slack=${2:-false}
  printf "%s %s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$message" >> "$LOG_FILE"
  if [[ "${DEBUG}" == "true" || "$notify_slack" == "true" ]]; then
    slack_notify "$message"
  fi
}
run_with_timing() {
  local label=$1
  shift
  local start end rc
  start=$(date +%s)
  log "${STACK_NAME:-stack} ${label} start" "true"
  set +e
  "$@"
  rc=$?
  set -e
  end=$(date +%s)
  log "${STACK_NAME:-stack} ${label} end status=$rc duration=$((end-start))s" "true"
  return $rc
}

SCRIPT_START=$(date +%s)
log "${STACK_NAME:-stack} userdata.sh start" "true"

. /etc/os-release

distro=ubuntu${VERSION_ID//[.]/""}
arch="x86_64"
echo "Ubuntu  $distro/$arch"

# setup graphics desktop
export DEBIAN_FRONTEND=noninteractive
export DEBCONF_NONINTERACTIVE_SEEN=true

dpkg -l | grep linux | awk -F' ' '{print $2}' > /tmp/dpkg.out
for pkg in `cat /tmp/dpkg.out`; do apt-mark hold $pkg; done







if [[ -z "${OVERRIDE_AMI}" ]]; then
  [[ ! -z $(lspci -v | grep NVIDIA) ]] && \
  [[ ! -x "$(command -v nvidia-smi)" ]] && \
  wget https://developer.download.nvidia.com/compute/cuda/repos/$distro/$arch/cuda-keyring_1.1-1_all.deb && \
  dpkg -i cuda-keyring_1.1-1_all.deb && \
  apt-get update && apt-get -y purge cuda && apt-get -y purge nvidia-* && apt-get -y purge libnvidia-* && apt-get -y autoremove && \
  apt-get -y install linux-headers-$(uname -r) build-essential && \
  ( ( [[ "$VERSION_ID" == 22.04* ]] && apt-get -y install gcc g++ cpp-11 gcc-11 g++-11 gcc-11-base libgcc-11-dev libstdc++-11-dev \
          cpp-12 gcc-12 g++-12 libgcc-12-dev libstdc++-12-dev && \
    update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-11 50 --slave /usr/bin/g++ g++ /usr/bin/g++-11 && \
    update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-12 100 --slave /usr/bin/g++ g++ /usr/bin/g++-12) || : ) && \
  DRIVER_VERSION=$(apt-cache policy nvidia-fabricmanager-580 | awk '/Candidate:/ {print $2}') && \
  DRIVER_MAJOR_VERSION=$(echo "$DRIVER_VERSION" | cut -d'.' -f1) && \
  apt-get install -y libnvidia-cfg1-${DRIVER_MAJOR_VERSION}-server=$DRIVER_VERSION \
    libnvidia-compute-${DRIVER_MAJOR_VERSION}-server=$DRIVER_VERSION \
    nvidia-driver-${DRIVER_MAJOR_VERSION}-server=$DRIVER_VERSION  && \
  ( (apt-get install -y nvidia-fabricmanager-${DRIVER_MAJOR_VERSION}=$DRIVER_VERSION && systemctl enable nvidia-fabricmanager) || : ) && \
  sync && reboot

  dpkg -l | grep nvidia | awk -F' ' '{print $2}' > /tmp/dpkg.out
  for pkg in `cat /tmp/dpkg.out`; do apt-mark hold $pkg; done
  
  [[ -z $(lspci -v | grep NVIDIA) ]] && update-pciids
  if  ( [[ ! -z $(lspci -v | grep Trainium) ]] || [[ ! -z $(lspci -v | grep Inferentia2) ]] ) \
    && [[ ! -x "$(command -v /opt/aws/neuron/neuron-ls)" ]]
  then
    wget -qO - https://apt.repos.neuron.amazonaws.com/GPG-PUB-KEY-AMAZON-AWS-NEURON.PUB > ./GPG-PUB-KEY-AMAZON-AWS-NEURON.PUB
    gpg --no-default-keyring --keyring ./aws_neuron_keyring.gpg --import  ./GPG-PUB-KEY-AMAZON-AWS-NEURON.PUB
    gpg --no-default-keyring --keyring ./aws_neuron_keyring.gpg  --export >  ./aws_neuron.gpg
    mv ./aws_neuron.gpg /etc/apt/trusted.gpg.d/
    rm ./GPG-PUB-KEY-AMAZON-AWS-NEURON.PUB

    add-apt-repository -y  "deb https://apt.repos.neuron.amazonaws.com ${VERSION_CODENAME} main"
    apt-get -y update
    apt-get -y install linux-headers-$(uname -r) git
    apt-get -y install aws-neuronx-dkms aws-neuronx-oci-hook aws-neuronx-collectives aws-neuronx-runtime-lib aws-neuronx-tools
    echo "export PATH=/opt/aws/neuron/bin:$PATH" >> /home/ubuntu/.bashrc
  fi
fi

# setup software repo for docker
wget -qO - https://download.docker.com/linux/ubuntu/gpg > ./GPG_DOCKER.PUB
gpg --no-default-keyring --keyring ./docker_keyring.gpg --import  ./GPG_DOCKER.PUB
gpg --no-default-keyring --keyring ./docker_keyring.gpg  --export > ./docker.gpg
mv ./docker.gpg /etc/apt/trusted.gpg.d/
rm ./GPG_DOCKER.PUB

add-apt-repository -y  "deb [arch=amd64] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable"

# setup software repo for fsx-lustre client
wget -qO - https://fsx-lustre-client-repo-public-keys.s3.amazonaws.com/fsx-ubuntu-public-key.asc > ./fsx-ubuntu-public-key.asc
gpg --no-default-keyring --keyring ./fsx_keyring.gpg --import  ./fsx-ubuntu-public-key.asc
gpg --no-default-keyring --keyring ./fsx_keyring.gpg  --export > ./fsx.gpg
mv ./fsx.gpg /etc/apt/trusted.gpg.d/
rm ./fsx-ubuntu-public-key.asc

( [[ "$VERSION_ID" == 24.04* ]] && \
  add-apt-repository -y  "deb https://fsx-lustre-client-repo.s3.amazonaws.com/ubuntu noble main" ) || \
( [[ "$VERSION_ID" == 22.04* ]] && \
  apt-get -y install lsb-core && \
  add-apt-repository -y  "deb https://fsx-lustre-client-repo.s3.amazonaws.com/ubuntu jammy main"
)

# add key for NICE-DCV
wget -qO - https://d1uj6qtbmh3dt5.cloudfront.net/NICE-GPG-KEY > ./NICE-GPG-KEY
gpg --no-default-keyring --keyring ./nice_dcv_keyring.gpg --import  ./NICE-GPG-KEY
gpg --no-default-keyring --keyring ./nice_dcv_keyring.gpg  --export > ./nice_dcv.gpg
mv ./nice_dcv.gpg /etc/apt/trusted.gpg.d/
rm ./NICE-GPG-KEY

# add  visual code repository
wget -qO - https://packages.microsoft.com/keys/microsoft.asc > ./microsoft.asc
gpg --no-default-keyring --keyring ./microsoft_keyring.gpg --import  ./microsoft.asc
gpg --no-default-keyring --keyring ./microsoft_keyring.gpg --export >  ./microsoft.gpg
mv ./microsoft.gpg /etc/apt/trusted.gpg.d/
rm ./microsoft.asc

add-apt-repository -y  "deb [arch=amd64] https://packages.microsoft.com/repos/vscode stable main"

# update and install required packages
apt-get update

apt-get -y install git tar curl jq
apt-get -y install software-properties-common

# install docker if it is not installed
if [ ! -x "$(command -v docker)" ]; then
  apt-get -y install docker-ce docker-ce-cli containerd.io      
  usermod -aG docker ubuntu

  # install nvidia container toolkit if we have a nvidia GPU
  if [[ ! -z $(lspci -v | grep NVIDIA) ]]
  then
    wget -qO - https://nvidia.github.io/nvidia-container-runtime/gpgkey > ./gpg_nvidia_container_runtime.pub
    gpg --no-default-keyring --keyring ./nvidia_container_runtime_keyring.gpg --import  ./gpg_nvidia_container_runtime.pub
    gpg --no-default-keyring --keyring ./nvidia_container_runtime_keyring.gpg --export >  ./nvidia_container_runtime.gpg
    mv ./nvidia_container_runtime.gpg /etc/apt/trusted.gpg.d/
    rm ./gpg_nvidia_container_runtime.pub

    distribution=$ID$VERSION_ID
    curl -s -L https://nvidia.github.io/nvidia-container-runtime/$distribution/nvidia-container-runtime.list | \
      tee /etc/apt/sources.list.d/nvidia-container-runtime.list
    apt-get update
    apt-get -y install nvidia-container-toolkit
  fi
fi

apt-get -y install tzdata
apt-get -y install keyboard-configuration
apt-get -y install gnupg2
apt-get -y install openmpi-bin libopenmpi-dev 
apt-get -y install protobuf-compiler

cat >/usr/local/bin/install-desktop.sh <<'EOF'
#!/bin/bash
set -euo pipefail
case "${DESKTOP_FLAVOR}" in
  ubuntu-desktop|ubuntu-desktop-minimal|xfce4|kubuntu-desktop)
    apt-get -y install "${DESKTOP_FLAVOR}"
    ;;
  *)
    echo "Unknown desktop flavor: ${DESKTOP_FLAVOR}" >&2
    exit 1
    ;;
esac
EOF
chmod +x /usr/local/bin/install-desktop.sh

DESKTOP_START=$(date +%s)
log "desktop-install start flavor=${DESKTOP_FLAVOR}"
/usr/local/bin/install-desktop.sh
DESKTOP_END=$(date +%s)
log "desktop-install end duration=$((DESKTOP_END-DESKTOP_START))s"

if [[ ! -x "$(command -v dcv)" ]]
then
apt-get -y install gdm3
echo "/usr/sbin/gdm3" > /etc/X11/default-display-manager
dpkg-reconfigure gdm3
sed -i -e "s/#WaylandEnable=false/WaylandEnable=false/g" /etc/gdm3/custom.conf
(systemctl stop gdm3 && systemctl start gdm3 && apt-get -y install mesa-utils) || (sync &&  reboot)

cat >/usr/local/bin/gpu-setup.sh <<'EOF'
#!/bin/bash
set -euo pipefail
if lspci | grep -q NVIDIA && command -v nvidia-smi >/dev/null 2>&1; then
  DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)
  DRIVER_MAJOR=$(echo "$DRIVER_VER" | cut -d'.' -f1)
  if apt-cache show xserver-xorg-video-nvidia-${DRIVER_MAJOR}-server >/dev/null 2>&1; then
    apt-get -y install xserver-xorg-video-nvidia-${DRIVER_MAJOR}-server
  elif apt-cache show xserver-xorg-video-nvidia-${DRIVER_MAJOR} >/dev/null 2>&1; then
    apt-get -y install xserver-xorg-video-nvidia-${DRIVER_MAJOR}
  fi
  if apt-cache show libnvidia-gl-${DRIVER_MAJOR}-server >/dev/null 2>&1; then
    apt-get -y install libnvidia-gl-${DRIVER_MAJOR}-server
  elif apt-cache show libnvidia-gl-${DRIVER_MAJOR} >/dev/null 2>&1; then
    apt-get -y install libnvidia-gl-${DRIVER_MAJOR}
  fi

  BUS_ID=$(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader | head -n1)
  BUS_HEX=$(echo "$BUS_ID" | awk -F "[:.]" '{print $2}')
  DEV_HEX=$(echo "$BUS_ID" | awk -F "[:.]" '{print $3}')
  FUNC_DEC=$(echo "$BUS_ID" | awk -F "[:.]" '{print $4}')
  BUS_DEC=$(printf "%d" "0x${BUS_HEX}")
  DEV_DEC=$(printf "%d" "0x${DEV_HEX}")

  [[ -d /etc/X11/xorg.conf.d ]] || mkdir -p /etc/X11/xorg.conf.d
  rm -f /etc/X11/xorg.conf
  cat >/etc/X11/xorg.conf.d/10-nvidia.conf <<EOL
Section "Files"
  ModulePath "/usr/lib/x86_64-linux-gnu/nvidia/xorg"
  ModulePath "/usr/lib/xorg/modules"
EndSection
Section "ServerLayout"
  Identifier "Layout0"
  Screen 0 "Screen0"
EndSection
Section "Device"
  Identifier "NVIDIA"
  Driver "nvidia"
  BusID "PCI:${BUS_DEC}:${DEV_DEC}:${FUNC_DEC}"
  Option "AllowEmptyInitialConfiguration" "True"
EndSection
Section "Screen"
  Identifier "Screen0"
  Device "NVIDIA"
EndSection
EOL
  rm -f /etc/X11/xorg.conf.d/10-dummy.conf
else
  rm -f /etc/X11/xorg.conf.d/10-nvidia.conf
  apt-get -y install xserver-xorg-video-dummy
  cat >/etc/X11/xorg.conf <<EOL
Section "Device"
  Identifier "DummyDevice"
  Driver "dummy"
  Option "UseEDID" "false"
  VideoRam 512000
EndSection
Section "Monitor"
  Identifier "DummyMonitor"
  HorizSync 5.0 - 1000.0
  VertRefresh 5.0 - 200.0
  Option "ReducedBlanking"
EndSection
Section "Screen"
  Identifier "DummyScreen"
  Device "DummyDevice"
  Monitor "DummyMonitor"
  DefaultDepth 24
  SubSection "Display"
    Viewport 0 0
    Depth 24
    Virtual 4096 2160
  EndSubSection
EndSection
EOL
fi
EOF
chmod +x /usr/local/bin/gpu-setup.sh

/usr/local/bin/gpu-setup.sh

( [[ "$VERSION_ID" == 24.04* ]] && \
  wget https://d1uj6qtbmh3dt5.cloudfront.net/2024.0/Servers/nice-dcv-2024.0-19030-ubuntu2404-x86_64.tgz && \
  tar -xvzf nice-dcv-2024.0-19030-ubuntu2404-x86_64.tgz && \
  cd nice-dcv-2024.0-19030-ubuntu2404-x86_64 && \
  apt-get -y install ./nice-dcv-server_2024.0.19030-1_amd64.ubuntu2404.deb) || \
( [[ "$VERSION_ID" == 22.04* ]] && \
  wget https://d1uj6qtbmh3dt5.cloudfront.net/2024.0/Servers/nice-dcv-2024.0-18131-ubuntu2204-x86_64.tgz && \
  tar -xvzf nice-dcv-2024.0-18131-ubuntu2204-x86_64.tgz && \
  cd nice-dcv-2024.0-18131-ubuntu2204-x86_64 && \
  apt-get -y install ./nice-dcv-server_2024.0.18131-1_amd64.ubuntu2204.deb) || echo "Retrying DCV install..."
  
systemctl daemon-reload && sync && reboot
fi

#restart X server
systemctl set-default graphical.target
systemctl isolate graphical.target

# Create DCV server configuration file
[[ -d /opt/dcv-session-store ]] || mkdir /opt/dcv-session-store
cat >/etc/dcv/dcv.conf <<EOL
[license]
[log]
[session-management]
create-session=true
[session-management/defaults]
[session-management/automatic-console-session]
owner=ubuntu
storage-root="/opt/dcv-session-store/"
[display]
[connectivity]
[security]
authentication="system"
[clipboard]
primary-selection-copy=true
primary-selection-paste=true
EOL

# Create DCV session permissions files
rm -f /home/ubuntu/dcv.perms
cat >/home/ubuntu/dcv.perms <<EOL
[permissions]
%owner% allow builtin
EOL

# Apply desktop fixes from scripts/fix-desktop-status.py (inlined for cloud-init)
if [[ -f /etc/gdm3/custom.conf ]]; then
  if grep -q "^WaylandEnable=" /etc/gdm3/custom.conf; then
    sed -i -e "s/^WaylandEnable=.*/WaylandEnable=false/" /etc/gdm3/custom.conf
  else
    echo "WaylandEnable=false" >> /etc/gdm3/custom.conf
  fi
fi

# Enable DCV server
systemctl daemon-reload
systemctl enable dcvserver
systemctl restart dcvserver
systemctl enable gdm3
systemctl restart gdm3

echo "install DCV server complete"

# install nfs-common
apt-get -y install nfs-common

# Install EFA software, if Efa is enabled
if [[ "$EFA_ENABLED"  == "true" ]]
then
# Install EFA software
sysctl -w kernel.yama.ptrace_scope=0
curl -O https://efa-installer.amazonaws.com/aws-efa-installer-1.32.0.tar.gz
wget https://efa-installer.amazonaws.com/aws-efa-installer.key && gpg --import aws-efa-installer.key
cat aws-efa-installer.key | gpg --fingerprint
wget https://efa-installer.amazonaws.com/aws-efa-installer-1.32.0.tar.gz.sig && gpg --verify ./aws-efa-installer-1.32.0.tar.gz.sig
tar -xf aws-efa-installer-1.32.0.tar.gz
cd aws-efa-installer &&  ( ./efa_installer.sh --yes || echo "Verify EFA" )
cd ../ && rm -rf aws-efa-installer-1.32.0.tar.gz aws-efa-installer
fi

# Create EFS mount script
cat >/usr/local/bin/mount-efs.sh <<EOL
#!/bin/bash
if [[ "$EFS_ENABLED"  == "true" ]]
then
  mkdir -p $EFS_MOUNT_PATH
  mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,noresvport $EFS_FS_ID.efs.$AWS_REGION.amazonaws.com:/ $EFS_MOUNT_PATH && \
  mkdir -p $EFS_MOUNT_PATH/home && \
  chown ubuntu:ubuntu $EFS_MOUNT_PATH/home
fi
EOL
chmod u+x /usr/local/bin/mount-efs.sh
/usr/local/bin/mount-efs.sh   

# Create FSx  mount script
cat >/usr/local/bin/mount-fsx.sh <<EOL
#!/bin/bash
if [[ "$FSX_ENABLED"  == "true" ]]
then
  mkdir -p $FSX_MOUNT_PATH
  apt-get -y install lustre-client-modules-$(uname -r)
  mount -t lustre -o noatime,flock $FSX_FS_ID.fsx.$AWS_REGION.amazonaws.com@tcp:/$FSX_MOUNT_NAME $FSX_MOUNT_PATH
fi

EOL
chmod u+x /usr/local/bin/mount-fsx.sh
/usr/local/bin/mount-fsx.sh

# Create config file
mkdir -p /home/ubuntu/.aws
cat >/home/ubuntu/.aws/config <<EOL
[default]
region = ${AWS_REGION}

EOL
chown -R ubuntu:ubuntu /home/ubuntu/.aws

# update .bashrc
echo "export desktop_role_arn=${DESKTOP_ROLE_ARN}" >> /home/ubuntu/.bashrc
echo "export desktop_sg_id=${DESKTOP_SG_ID}" >> /home/ubuntu/.bashrc
echo "export desktop_subnet_id=${DESKTOP_SUBNET_ID}" >> /home/ubuntu/.bashrc
echo "export efs_fs_id=${EFS_FS_ID}" >> /home/ubuntu/.bashrc

if [[ "$FSX_ENABLED"  == "true" ]]
then
  echo "export fsx_fs_id=${FSX_FS_ID}" >> /home/ubuntu/.bashrc
  echo "export fsx_mount_name=${FSX_MOUNT_NAME}" >> /home/ubuntu/.bashrc
fi

# install miniconda3 if anaconda3, or miniconda3 are not installed
if [[ ! -d "/home/ubuntu/anaconda3" ]] && [[ ! -d "/home/ubuntu/miniconda3" ]]
then
  wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /home/ubuntu/miniconda3.sh
  HOME=/home/ubuntu bash /home/ubuntu/miniconda3.sh -b -p /home/ubuntu/miniconda3
  echo "source /home/ubuntu/miniconda3/etc/profile.d/conda.sh" >> /home/ubuntu/.bashrc
  rm /home/ubuntu/miniconda3.sh

  source /home/ubuntu/miniconda3/etc/profile.d/conda.sh
  conda tos accept --override-channels --channel  https://repo.anaconda.com/pkgs/main
  conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
  conda update -y --name base -c defaults conda

  # install jupyterlab and boto3 in base env
  conda activate && \
  conda install -y -c conda-forge jupyterlab && \
  conda install -y ipykernel && \
  conda install -y boto3 && \
  conda install -y nb_conda_kernels && \
  conda deactivate

  chown -R ubuntu:ubuntu /home/ubuntu/miniconda3
  chown -R ubuntu:ubuntu /home/ubuntu/.conda
fi

# set python to python3 system-wide
apt-get -y install python-is-python3

# install visual code 
apt-get -y install code

# install uv system-wide
curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

# install Kiro
URL=$(curl -s https://prod.download.desktop.kiro.dev/stable/metadata-linux-x64-stable.json | jq -r '.releases[] | select(.updateTo.url | endswith(".tar.gz")) | .updateTo.url')
mkdir -p /opt/kiro
curl -L "$URL" | tar -xz -C /opt/kiro --strip-components=1
chown -R root:root /opt/kiro
chmod 4755 /opt/kiro/chrome-sandbox
ln -sf /opt/kiro/bin/kiro /usr/local/bin/kiro
cat <<EOF > /usr/share/applications/kiro.desktop
[Desktop Entry]
Name=Kiro
Exec=/opt/kiro/bin/kiro %F
Icon=/opt/kiro/resources/app/resources/linux/code.png
Type=Application
Categories=Development;IDE;
EOF

# install aws cli
snap install aws-cli --classic

# Set hostname to dsi-[stack-name-suffix]-[user]
HOSTNAME="dsi"
if [[ ! -z "$STACK_NAME_SUFFIX" ]]; then
  HOSTNAME="${HOSTNAME}-${STACK_NAME_SUFFIX}"
fi
if [[ ! -z "$USER" ]]; then
  HOSTNAME="${HOSTNAME}-${USER}"
fi

# Set the hostname
hostnamectl set-hostname "$HOSTNAME"
echo "127.0.0.1 $HOSTNAME" >> /etc/hosts

# Set ubuntu user password if provided
if [[ ! -z "$UBUNTU_PASSWORD" ]]; then
  echo "ubuntu:$UBUNTU_PASSWORD" | chpasswd
  echo "Ubuntu user password has been set"
else
  echo "No password provided for ubuntu user"
fi

SCRIPT_END=$(date +%s)
log "${STACK_NAME:-stack} userdata.sh end status=0 duration=$((SCRIPT_END-SCRIPT_START))s" "true"

echo "Deep Learning Desktop is Ready!" > /etc/motd
