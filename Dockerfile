FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# --proxy-headers so the app trusts the reverse proxy's X-Forwarded-* when
# building redirect URIs; forwarded-allow-ips=* because the proxy is the only
# thing that can reach the container on this private network.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
