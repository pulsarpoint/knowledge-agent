FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server sudo util-linux coreutils && rm -rf /var/lib/apt/lists/* \
    && useradd -m -g operator -s /bin/sh operator && passwd -d operator \
    && mkdir -p /run/sshd /home/operator/.ssh \
    && printf 'operator ALL=(root) NOPASSWD: ALL\n' > /etc/sudoers.d/operator
COPY deploy/ssh-test/sshd_config /etc/ssh/sshd_config
COPY deploy/ssh-test/start.sh /start.sh
CMD ["sh", "/start.sh"]
