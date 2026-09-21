"""
BackgroundJobManager: Asynchronous and Concurrent Background Task Execution Runtime.
Enables agents to spawn long-running jobs (build watchers, test runners, servers, crawlers),
poll their status, await their results asynchronously, and cancel them cleanly.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import inspect
import threading
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union
import uuid


class BackgroundJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class BackgroundJobRecord:
    """Descriptor of a managed background job."""
    job_id: str
    name: str
    status: BackgroundJobStatus = BackgroundJobStatus.PENDING
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    task_handle: Optional[asyncio.Task] = field(default=None, repr=False)
    thread_handle: Optional[threading.Thread] = field(default=None, repr=False)

    @property
    def duration_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.completed_at or time.time()
        return round(end - self.started_at, 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "result": self.result,
            "error": self.error,
        }


class BackgroundJobManager:
    """
    Manages the lifecycle of background coroutines and thread jobs.
    """
    def __init__(self):
        self._jobs: Dict[str, BackgroundJobRecord] = {}
        self._lock = threading.RLock()

    def start_job(
        self,
        name: str,
        target: Union[Callable[..., Any], Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> BackgroundJobRecord:
        """
        Starts a target callable or coroutine in the background.
        Returns the created BackgroundJobRecord.
        """
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        record = BackgroundJobRecord(
            job_id=job_id,
            name=name,
            status=BackgroundJobStatus.RUNNING,
            started_at=time.time(),
        )

        with self._lock:
            self._jobs[job_id] = record

        if asyncio.iscoroutinefunction(target) or inspect.iscoroutine(target):
            # Async coroutine execution
            async def _run_async_job():
                try:
                    if inspect.iscoroutine(target):
                        res = await target
                    else:
                        res = await target(*args, **kwargs)
                    record.result = res
                    record.status = BackgroundJobStatus.COMPLETED
                except asyncio.CancelledError:
                    record.status = BackgroundJobStatus.CANCELLED
                    record.error = "Job cancelled"
                except Exception as e:
                    record.status = BackgroundJobStatus.FAILED
                    record.error = str(e)
                finally:
                    record.completed_at = time.time()

            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(_run_async_job())
                record.task_handle = task
            except RuntimeError:
                # If no running asyncio loop, run in dedicated thread with new event loop
                def _thread_async_runner():
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    try:
                        new_loop.run_until_complete(_run_async_job())
                    finally:
                        new_loop.close()

                th = threading.Thread(target=_thread_async_runner, daemon=True)
                record.thread_handle = th
                th.start()
        else:
            # Sync callable execution in thread
            def _thread_sync_runner():
                try:
                    res = target(*args, **kwargs)
                    record.result = res
                    record.status = BackgroundJobStatus.COMPLETED
                except Exception as e:
                    record.status = BackgroundJobStatus.FAILED
                    record.error = str(e)
                finally:
                    record.completed_at = time.time()

            th = threading.Thread(target=_thread_sync_runner, daemon=True)
            record.thread_handle = th
            th.start()

        return record

    def get_job(self, job_id: str) -> Optional[BackgroundJobRecord]:
        """Retrieves a background job record by ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, status: Optional[BackgroundJobStatus] = None) -> List[BackgroundJobRecord]:
        """Lists all managed background jobs, optionally filtered by status."""
        with self._lock:
            if status:
                return [j for j in self._jobs.values() if j.status == status]
            return list(self._jobs.values())

    def cancel_job(self, job_id: str) -> bool:
        """Cancels an active background job."""
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.status in (
                BackgroundJobStatus.COMPLETED,
                BackgroundJobStatus.FAILED,
                BackgroundJobStatus.CANCELLED,
            ):
                return False

            record.status = BackgroundJobStatus.CANCELLED
            record.completed_at = time.time()
            record.error = "Job cancelled by manager"

            if record.task_handle and not record.task_handle.done():
                record.task_handle.cancel()
                return True
            return True

    async def await_job(self, job_id: str, timeout: Optional[float] = None) -> Any:
        """
        Asynchronously waits for a background job to finish and returns its result.
        """
        record = self.get_job(job_id)
        if not record:
            raise KeyError(f"Background job '{job_id}' not found.")

        start_time = time.time()
        while record.status in (BackgroundJobStatus.PENDING, BackgroundJobStatus.RUNNING):
            if timeout is not None and (time.time() - start_time) > timeout:
                raise TimeoutError(f"Awaiting job '{job_id}' timed out after {timeout}s.")
            await asyncio.sleep(0.05)

        if record.status == BackgroundJobStatus.FAILED:
            raise RuntimeError(f"Background job '{job_id}' failed: {record.error}")
        if record.status == BackgroundJobStatus.CANCELLED:
            raise asyncio.CancelledError(f"Background job '{job_id}' was cancelled.")
        return record.result


# Global default background job manager
background_job_manager = BackgroundJobManager()
