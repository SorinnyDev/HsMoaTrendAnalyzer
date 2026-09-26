FROM python:3.11-slim

WORKDIR /app

# Set timezone to KST
ENV TZ=Asia/Seoul
RUN apt-get update && apt-get install -y tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install python dependencies and playwright browsers
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

COPY . .

# Ensure script is executable
RUN chmod +x entrypoint.sh

# Create directories for volumes
RUN mkdir -p /app/logs /app/outputs

CMD ["./entrypoint.sh"]
