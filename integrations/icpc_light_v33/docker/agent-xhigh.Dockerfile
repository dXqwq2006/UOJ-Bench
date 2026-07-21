ARG BASE_IMAGE=skill-eval-agent:noc-v7-011-ver3.3.0
FROM ${BASE_IMAGE}

RUN mv /usr/local/bin/codex /usr/local/bin/codex-real

COPY --chmod=0555 docker/codex_wrapper.py /usr/local/bin/codex
COPY --chmod=0555 docker/codex_wrapper.py /opt/skill-eval/bin/codex
COPY vendor/cpideas_plus_778c619/cpideas_plus /opt/cpideas/cpideas_plus

LABEL org.cpideas.skill-eval.reasoning-effort="xhigh"
LABEL org.cpideas.skill-eval.base-agent-image-id="sha256:d922d6dfe1e11d5a7570b1108abcf18bfe2fec59703b54440363ae8eac002169"
LABEL org.cpideas.skill-eval.codex-wrapper-sha256="7d517ae8652a3dd176dd6a597f37678f8da2c0faeddbec0fea113c1f28bbaf65"
LABEL org.cpideas.skill-eval.cpideas-plus-commit="778c619799affe3c52ecd23e2984ce7d9545fed5"
LABEL org.cpideas.skill-eval.release-run="icpc-light-v33-xhigh"
