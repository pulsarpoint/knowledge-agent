#!/bin/sh
set -eu
cp /fixtures/client_key.pub /home/operator/.ssh/authorized_keys
chown -R operator:operator /home/operator/.ssh
chmod 700 /home/operator/.ssh
chmod 600 /home/operator/.ssh/authorized_keys
exec /usr/sbin/sshd -D -e
