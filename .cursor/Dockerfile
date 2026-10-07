FROM ubuntu:24.04

# No apt Python: install.sh takes the interpreters it needs from uv.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        curl \
        llvm \
        clang \
        g++ \
        binutils \
    && rm -rf /var/lib/apt/lists/*

USER ubuntu
