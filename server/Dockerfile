FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY models.py tools.py telegram_client.py main.py ./

# The SQLite file lives on a mounted volume (see k8s/pvc.yaml) so task data
# survives a pod restart.
ENV PM_CHASER_DB_PATH=/data/pm_chaser.db
ENV PM_CHASER_HOST=0.0.0.0
ENV PM_CHASER_PORT=8000

EXPOSE 8000

CMD ["python", "main.py"]
