FROM python:3.12-slim

WORKDIR /app
COPY echo-dot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY voice-pe/bridge/bridge.py base_bridge.py
COPY echo-dot/dot_bridge.py .

CMD ["python", "-u", "dot_bridge.py"]
