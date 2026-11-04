from celery import Celery

from arsune.config import settings

app = Celery("arsune", broker=settings.redis_url, include=["arsune.worker"])
app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 60},
    task_ignore_result=True,
    task_soft_time_limit=settings.job_deadline_seconds + 10,
    task_time_limit=settings.job_deadline_seconds + 20,
    broker_connection_retry_on_startup=True,
)
