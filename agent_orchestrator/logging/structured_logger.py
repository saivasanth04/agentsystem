"""
Structured Logger implementation for Agent Orchestrator.
Wraps Python standard logging to provide structured, contextual logging
with both JSONL file output and formatted console output.
"""
import logging
import os
from pathlib import Path
import sys
import threading
from typing import Any, Dict, Optional, Union

from .formatters import ConsoleLogFormatter, JsonLogFormatter


_LOCK = threading.RLock()
_CONFIGURED = False
_LOGGERS: Dict[str, "StructuredLogger"] = {}


class StructuredLogger:
    """
    Structured logger that wraps Python's standard logging.Logger.
    Supports extra context injection (stage, payload, session_id, task_id, agent_name)
    compatible with JsonLogFormatter and ConsoleLogFormatter.
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self.name = logger.name

    @property
    def level(self) -> int:
        return self._logger.level

    def setLevel(self, level: Union[int, str]) -> None:
        self._logger.setLevel(level)

    def _build_extra(
        self,
        stage: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        result = dict(extra or {})
        if stage is not None:
            result["stage"] = stage
        if payload is not None:
            result["payload"] = payload
        if session_id is not None:
            result["session_id"] = session_id
        if task_id is not None:
            result["task_id"] = task_id
        if agent_name is not None:
            result["agent_name"] = agent_name
        return result

    def log(
        self,
        level: int,
        msg: str,
        *args: Any,
        stage: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        extra = self._build_extra(
            stage=stage,
            payload=payload,
            session_id=session_id,
            task_id=task_id,
            agent_name=agent_name,
            extra=kwargs.pop("extra", None),
        )
        self._logger.log(level, msg, *args, extra=extra, **kwargs)

    def debug(
        self,
        msg: str,
        *args: Any,
        stage: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.log(
            logging.DEBUG,
            msg,
            *args,
            stage=stage,
            payload=payload,
            session_id=session_id,
            task_id=task_id,
            agent_name=agent_name,
            **kwargs,
        )

    def info(
        self,
        msg: str,
        *args: Any,
        stage: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.log(
            logging.INFO,
            msg,
            *args,
            stage=stage,
            payload=payload,
            session_id=session_id,
            task_id=task_id,
            agent_name=agent_name,
            **kwargs,
        )

    def warning(
        self,
        msg: str,
        *args: Any,
        stage: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.log(
            logging.WARNING,
            msg,
            *args,
            stage=stage,
            payload=payload,
            session_id=session_id,
            task_id=task_id,
            agent_name=agent_name,
            **kwargs,
        )

    def error(
        self,
        msg: str,
        *args: Any,
        stage: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.log(
            logging.ERROR,
            msg,
            *args,
            stage=stage,
            payload=payload,
            session_id=session_id,
            task_id=task_id,
            agent_name=agent_name,
            **kwargs,
        )

    def exception(
        self,
        msg: str,
        *args: Any,
        stage: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        kwargs["exc_info"] = True
        self.log(
            logging.ERROR,
            msg,
            *args,
            stage=stage,
            payload=payload,
            session_id=session_id,
            task_id=task_id,
            agent_name=agent_name,
            **kwargs,
        )

    def log_event(
        self,
        stage: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
        level: int = logging.INFO,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> None:
        """
        Emits a structured lifecycle/orchestration event.
        Direct drop-in replacement for legacy _default_event_logger.
        """
        self.log(
            level=level,
            msg=message,
            stage=stage,
            payload=payload,
            session_id=session_id,
            task_id=task_id,
            agent_name=agent_name,
        )

    def add_handler(self, handler: logging.Handler) -> None:
        self._logger.addHandler(handler)

    def remove_handler(self, handler: logging.Handler) -> None:
        self._logger.removeHandler(handler)

    def flush(self) -> None:
        for handler in self._logger.handlers:
            try:
                handler.flush()
            except Exception:
                pass
        curr = self._logger.parent
        while curr:
            for handler in getattr(curr, "handlers", []):
                try:
                    handler.flush()
                except Exception:
                    pass
            curr = curr.parent


def configure_logging(
    level: Union[str, int] = "INFO",
    log_file: Optional[Union[str, Path]] = None,
    console: bool = True,
    console_format: str = "console",
    root_logger_name: str = "orchestrator",
) -> logging.Logger:
    """
    Configures the root agent orchestrator logger with standard console and file sinks.
    """
    global _CONFIGURED
    with _LOCK:
        logger = logging.getLogger(root_logger_name)
        if isinstance(level, str):
            level = getattr(logging, level.upper(), logging.INFO)
        logger.setLevel(level)

        # Clear existing handlers with same name to prevent duplicates
        for h in list(logger.handlers):
            if getattr(h, "_orchestrator_managed", False):
                logger.removeHandler(h)

        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler._orchestrator_managed = True  # type: ignore
            console_handler.setLevel(level)
            if console_format == "json":
                console_handler.setFormatter(JsonLogFormatter())
            else:
                console_handler.setFormatter(ConsoleLogFormatter())
            logger.addHandler(console_handler)

        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
            file_handler._orchestrator_managed = True  # type: ignore
            file_handler.setLevel(level)
            file_handler.setFormatter(JsonLogFormatter())
            logger.addHandler(file_handler)

        _CONFIGURED = True
        return logger


def add_session_file_sink(
    session_id: str,
    log_dir: Union[str, Path],
    logger_name: str = "orchestrator",
    level: int = logging.DEBUG,
) -> Optional[logging.FileHandler]:
    """
    Attaches a JSONL file handler for a specific orchestration session.
    """
    with _LOCK:
        dir_path = Path(log_dir)
        dir_path.mkdir(parents=True, exist_ok=True)
        log_path = dir_path / f"session_{session_id}.jsonl"

        logger = logging.getLogger(logger_name)
        if logger.level == logging.NOTSET or logger.level > level:
            logger.setLevel(level)

        # Avoid duplicate handlers for the exact same file
        for h in logger.handlers:
            if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == str(log_path.resolve()):
                return h

        handler = logging.FileHandler(str(log_path), encoding="utf-8")
        handler._session_sink = session_id  # type: ignore
        handler._orchestrator_managed = True  # type: ignore
        handler.setLevel(level)
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        return handler


def remove_session_file_sink(
    handler: Optional[logging.FileHandler],
    logger_name: str = "orchestrator",
) -> None:
    """
    Closes and removes a session file handler.
    """
    if not handler:
        return
    with _LOCK:
        logger = logging.getLogger(logger_name)
        try:
            handler.flush()
            handler.close()
            logger.removeHandler(handler)
        except Exception:
            pass


def get_logger(name: str = "orchestrator") -> StructuredLogger:
    """
    Returns a cached StructuredLogger instance wrapping logging.getLogger(name).
    """
    with _LOCK:
        if name not in _LOGGERS:
            std_logger = logging.getLogger(name)
            if std_logger.level == logging.NOTSET:
                std_logger.setLevel(logging.INFO)
            # If orchestrator root has no handlers yet, configure basic console output
            if not _CONFIGURED and not std_logger.handlers and not logging.getLogger().handlers:
                configure_logging()
            _LOGGERS[name] = StructuredLogger(std_logger)
        return _LOGGERS[name]
