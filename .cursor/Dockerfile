FROM ubuntu:24.04

# The system Python only installs uv, by hash; install.sh takes the interpreters the project
# runs on from uv.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        curl \
        python3-pip \
        llvm \
        clang \
        g++ \
        binutils \
    && rm -rf /var/lib/apt/lists/*

USER ubuntu
