FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir paho-mqtt==2.1.0 tzdata
COPY uv_model.py .
CMD ["python3", "-u", "uv_model.py"]
