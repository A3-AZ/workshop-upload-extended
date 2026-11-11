# steamcmd with its 32-bit runtime, already installed and updated, plus Python for the action script.
FROM cm2network/steamcmd:root

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

COPY src/ /action/

ENTRYPOINT ["python3", "/action/upload.py"]
