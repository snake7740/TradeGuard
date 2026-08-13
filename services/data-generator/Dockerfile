FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY generate.py .
# 按需运行：docker compose run --rm data-generator python generate.py --scale small
CMD ["python", "generate.py", "--scale", "demo"]
