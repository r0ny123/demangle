FROM ubuntu:24.04

# No Python from apt: `requires-python` is `>=3.13` and Ubuntu 24.04 ships 3.12, so
# install.sh has uv provide the interpreter. `ca-certificates` is for the HTTPS fetches
# there (curl, git), which `--no-install-recommends` would otherwise leave without roots.
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
