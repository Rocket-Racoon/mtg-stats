"""``python -m app.workers`` starts the arq worker."""

from app.workers.runner import run_worker_process

if __name__ == "__main__":
    run_worker_process()
