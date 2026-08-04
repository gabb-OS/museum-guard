FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY esp_sen_mock.py .
EXPOSE 5683/udp
CMD ["python", "esp_sen_mock.py"]