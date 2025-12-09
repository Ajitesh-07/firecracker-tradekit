# STAGE 1: Builder
FROM python:3.11-slim as builder

# Install compilers
RUN apt-get update && apt-get install -y curl build-essential pkg-config libssl-dev

# Install Open-RC
RUN apt-get update && apt-get install -y openrc net-tools

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app
COPY . .

RUN pip install maturin && maturin build --release

# STAGE 2: RootFS
FROM python:3.11-slim

WORKDIR /code

ENV DATA_PATH="/code/historical_data"

COPY --from=builder /app/target/wheels/*.whl /tmp/
RUN pip install /tmp/*.whl && rm /tmp/*.whl
RUN pip install numpy pandas scikit-learn

COPY historical_data /code/historical_data

# 5. PREPARE THE EXECUTABLE
COPY agent.py /bin/agent.py

RUN echo "root:root" | chpasswd
RUN echo '#!/bin/sh' > /sbin/myinit && \
    echo 'mount -t proc proc /proc' >> /sbin/myinit && \
    echo 'mount -t sysfs sys /sys' >> /sbin/myinit && \
    echo 'python3 /bin/agent.py' >> /sbin/myinit && \
    chmod +x /sbin/myinit

ENTRYPOINT ["/sbin/myinit"]