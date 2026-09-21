"""
Communication Tools for ReAct Agents to interact over the MessageBus.
Equips agents with tools to send structured messages, query peer agents synchronously,
publish technical findings to topic channels, and inspect inboxes.
"""
from typing import Any, Callable, Dict, List, Optional
from ..runtime.messaging import MessageBus, StructuredMessage, MessageType


class CommunicationToolRegistry:
    """
    Registers and dispatches inter-agent communication tools against a shared MessageBus.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        agent_registry: Any = None,
        llm: Any = None,
        workspace: Any = None,
        current_agent_name: str = "AGENT",
    ):
        self.message_bus = message_bus
        self.agent_registry = agent_registry
        self.llm = llm
        self.workspace = workspace
        self.current_agent_name = current_agent_name

    def send_agent_message(
        self,
        recipient: str,
        content: str,
        message_type: str = "TASK_RESULT",
        topic: str = "general",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Sends a structured message to a specific agent, task ID, or '*' for broadcast.
        """
        msg = StructuredMessage(
            sender=self.current_agent_name,
            recipient=recipient,
            message_type=message_type,
            content=content,
            topic=topic,
            payload=payload or {},
        )
        if recipient in ["*", "all", "broadcast"]:
            sent = self.message_bus.publish(topic, msg)
        else:
            sent = self.message_bus.send_direct(msg)
        return {"status": "SENT", "message": sent.to_dict()}

    def query_agent(
        self,
        target_agent: str,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Consults another specialized agent synchronously and waits for their expert answer.
        """
        resp = self.message_bus.query_agent_sync(
            sender=self.current_agent_name,
            target_agent=target_agent,
            query=query,
            context=context or {},
            agent_registry=self.agent_registry,
            llm=self.llm,
            workspace=self.workspace,
        )
        return {
            "status": "ANSWERED",
            "from_agent": resp.sender,
            "response": resp.content,
            "structured_data": resp.payload,
        }

    def publish_finding(
        self,
        topic: str,
        title: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Publishes an engineering finding, architectural constraint, or security observation to a topic.
        """
        msg = StructuredMessage(
            sender=self.current_agent_name,
            recipient="*",
            message_type=MessageType.FINDING,
            content=title,
            topic=topic,
            payload=details or {},
        )
        sent = self.message_bus.publish(topic, msg)
        return {"status": "PUBLISHED", "topic": topic, "message_id": sent.message_id}

    def read_inbox(
        self,
        recipient: Optional[str] = None,
        clear: bool = False,
    ) -> Dict[str, Any]:
        """
        Reads pending messages delivered to this agent or subtask.
        """
        target = recipient or self.current_agent_name
        msgs = self.message_bus.get_inbox(target, clear=clear)
        return {
            "recipient": target,
            "count": len(msgs),
            "messages": [m.to_dict() for m in msgs],
        }

    def get_message_history(
        self,
        topic: Optional[str] = None,
        message_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retrieves recent messages from the message bus audit trail.
        """
        msgs = self.message_bus.get_history(topic=topic, message_type=message_type)
        return {
            "count": len(msgs),
            "messages": [m.to_dict() for m in msgs[-10:]],
        }

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        Returns OpenAI-compatible function calling schemas for communication tools.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "send_agent_message",
                    "description": "Send a structured message, artifact, or task result to another agent or task ID.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "recipient": {
                                "type": "string",
                                "description": "Target agent name (e.g. 'CODER', 'SPECIFICATION', 'ARCHITECTURE') or task ID (e.g. 'T-01'), or '*' for broadcast.",
                            },
                            "content": {
                                "type": "string",
                                "description": "Human/agent-readable message content.",
                            },
                            "message_type": {
                                "type": "string",
                                "enum": ["TASK_RESULT", "ARTIFACT", "OBSERVATION", "FINDING", "REQUEST", "RESPONSE", "BROADCAST"],
                                "description": "Type of message being transmitted.",
                            },
                            "topic": {
                                "type": "string",
                                "description": "Channel or topic name (e.g. 'api_contracts', 'architecture', 'general').",
                            },
                            "payload": {
                                "type": "object",
                                "description": "Optional structured dictionary containing schemas, deliverables, or metadata.",
                            },
                        },
                        "required": ["recipient", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "query_agent",
                    "description": "Synchronously consult another specialized persona (e.g. asking ARCHITECTURE for schema clarification) mid-flight and receive an answer.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target_agent": {
                                "type": "string",
                                "description": "Specialized agent persona to consult (e.g. 'ARCHITECTURE', 'SPECIFICATION', 'security-auditor', 'python-debugger').",
                            },
                            "query": {
                                "type": "string",
                                "description": "Specific question or technical clarification needed.",
                            },
                            "context": {
                                "type": "object",
                                "description": "Relevant code snippets, schemas, or requirements context.",
                            },
                        },
                        "required": ["target_agent", "query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "publish_finding",
                    "description": "Publish a discovered constraint, security boundary, or technical decision to a topic channel for other agents.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "topic": {
                                "type": "string",
                                "description": "Topic name (e.g. 'security_boundaries', 'api_conventions', 'database_schema').",
                            },
                            "title": {
                                "type": "string",
                                "description": "Concise summary of the finding.",
                            },
                            "details": {
                                "type": "object",
                                "description": "Structured details, constraints, or code references.",
                            },
                        },
                        "required": ["topic", "title"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_inbox",
                    "description": "Read incoming messages and requests sent directly to this agent or subtask.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "recipient": {
                                "type": "string",
                                "description": "Recipient mailbox to check (defaults to current agent or task ID).",
                            },
                            "clear": {
                                "type": "boolean",
                                "description": "Whether to drain/clear the inbox after reading.",
                            },
                        },
                    },
                },
            },
        ]
