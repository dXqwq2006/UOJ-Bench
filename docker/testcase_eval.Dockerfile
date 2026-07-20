FROM docker.m.daocloud.io/library/ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG PYPY2_VERSION=7.3.17

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bzip2 \
        ca-certificates \
        g++-14 \
        libstdc++-14-dev \
        openjdk-21-jdk-headless \
        pypy3 \
        python3 \
        util-linux \
        wget \
    && wget -q \
        "https://downloads.python.org/pypy/pypy2.7-v${PYPY2_VERSION}-linux64.tar.bz2" \
        -O /tmp/pypy2.tar.bz2 \
    && mkdir -p /opt/pypy2 \
    && tar -xjf /tmp/pypy2.tar.bz2 -C /opt/pypy2 --strip-components=1 \
    && ln -s /opt/pypy2/bin/pypy /usr/local/bin/pypy2 \
    && ln -s /opt/pypy2/bin/pypy /usr/local/bin/python2 \
    && rm -rf /var/lib/apt/lists/* /tmp/pypy2.tar.bz2

USER 1000:1000
ENV HOME=/tmp
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
WORKDIR /workspace
