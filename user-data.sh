#!/bin/bash
dnf update -y
dnf install -y git docker awscli
systemctl enable --now docker
usermod -aG docker ec2-user

cat <<'INFO'
Base instance packages installed.
Use deployment/aws/ec2_bootstrap.sh from the repository to start the hybrid ZipVoice-CA container.
INFO
