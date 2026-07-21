ARG BASE_IMAGE=skill-eval-agent:noc-v7-011-ver3.3.0
FROM ${BASE_IMAGE}

COPY --chmod=0555 codex_wrapper.py /opt/skill-eval/bin/codex

LABEL org.cpideas.skill-eval.reasoning-effort="xhigh"
LABEL org.cpideas.skill-eval.base-agent-image-id="sha256:d922d6dfe1e11d5a7570b1108abcf18bfe2fec59703b54440363ae8eac002169"
LABEL org.cpideas.skill-eval.codex-wrapper-sha256="938886b301bea9c5a2fd1f68500daad408dda598c1e055883a69e30f9fb0020f"
LABEL org.cpideas.skill-eval.release-run="icpc-light-v33-xhigh"
