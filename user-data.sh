#!/bin/bash
dnf update -y
dnf install -y git docker awscli
systemctl enable --now docker
usermod -aG docker ec2-user
