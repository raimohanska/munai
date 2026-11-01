FROM mcr.microsoft.com/devcontainers/python:1-3.10-bookworm

USER root
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg

COPY requirements.txt /tmp/munai-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/munai-requirements.txt \
    && chown -R vscode:vscode /opt/venv \
    && mkdir -p /home/vscode/.cache \
    && chown vscode:vscode /home/vscode/.cache

USER vscode

# Official V2 standalone installer: no Node.js dependency required.
ARG OPENCODE_VERSION=2.2.0
ENV PATH="/home/vscode/.opencode/bin:${PATH}"
RUN curl -fsSL https://opencode.ai/v2/install -o /tmp/install-opencode.sh \
    && bash /tmp/install-opencode.sh --version "${OPENCODE_VERSION}" --no-modify-path \
    && opencode --version \
    && rm /tmp/install-opencode.sh
