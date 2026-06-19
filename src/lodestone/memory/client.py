import json
from typing import Optional

import httpx


class MemoryGatewayClient:
    """Thin async adapter over TencentDB-Agent-Memory's HTTP Gateway."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        namespace: str = "lodestone",
        timeout: int = 10,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.namespace = namespace or "lodestone"
        self.timeout = int(timeout or 10)

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _session_id(self, scope: str, agent_id: str = "", project_name: str = "") -> str:
        parts = [self.namespace, scope]
        if agent_id:
            parts.append(agent_id)
        if project_name:
            parts.append(project_name)
        return ":".join(parts)

    async def _post(self, path: str, payload: dict):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            if not resp.content:
                return {}
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                return resp.json()
            return {"text": resp.text}

    async def _get(self, path: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}{path}", headers=self._headers())
            resp.raise_for_status()
            if not resp.content:
                return {}
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                return resp.json()
            return {"text": resp.text}

    def _stringify(self, data) -> str:
        if not data:
            return ""
        if isinstance(data, str):
            return data.strip()
        if isinstance(data, list):
            lines = []
            for item in data:
                if isinstance(item, dict):
                    line = item.get("text") or item.get("content") or item.get("summary")
                    if not line:
                        line = json.dumps(item, ensure_ascii=True, sort_keys=True)
                    lines.append(f"- {line}")
                else:
                    lines.append(f"- {item}")
            return "\n".join(lines).strip()
        if isinstance(data, dict):
            for key in ("text", "content", "memory_context", "context", "result"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for key in ("items", "results", "memories", "data"):
                value = data.get(key)
                if value:
                    return self._stringify(value)
            return json.dumps(data, ensure_ascii=True, sort_keys=True)
        return str(data)

    async def recall(self, query: str, scope: str = "orchestrator") -> str:
        return await self.recall_scoped(query, scope=scope)

    async def recall_scoped(
        self,
        query: str,
        scope: str = "orchestrator",
        agent_id: str = "",
        project_name: str = "",
    ) -> str:
        data = await self._post(
            "/recall",
            {
                "namespace": self.namespace,
                "session_id": self._session_id(scope, agent_id=agent_id, project_name=project_name),
                "query": query,
                "agent_id": agent_id or None,
                "project": project_name or None,
            },
        )
        return self._stringify(data)

    async def capture(
        self,
        scope: str,
        user_text: str,
        assistant_text: str,
        agent_id: str = "",
        project_name: str = "",
        metadata: Optional[dict] = None,
    ) -> None:
        await self._post(
            "/capture",
            {
                "namespace": self.namespace,
                "session_id": self._session_id(scope, agent_id=agent_id, project_name=project_name),
                "messages": [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": assistant_text},
                ],
                "metadata": metadata or {},
            },
        )

    async def capture_agent_turn(
        self,
        agent_id: str,
        user_text: str,
        assistant_text: str,
        project_name: str = "",
        run_kind: str = "dispatch",
        task_id: str = "",
    ) -> None:
        await self.capture(
            "agent",
            user_text,
            assistant_text,
            agent_id=agent_id,
            project_name=project_name,
            metadata={"agent_id": agent_id, "project": project_name, "run_kind": run_kind, "task_id": task_id},
        )

    async def capture_orchestrator_turn(self, user_text: str, assistant_text: str) -> None:
        await self.capture("orchestrator", user_text, assistant_text)

    async def search_agent_memory(
        self,
        agent_id: str,
        query: str,
        limit: int = 5,
        memory_type: str = "",
        project_name: str = "",
    ) -> str:
        data = await self._post(
            "/search/memory",
            {
                "namespace": self.namespace,
                "session_id": self._session_id("agent", agent_id=agent_id, project_name=project_name),
                "query": query,
                "limit": int(limit or 5),
                "type": memory_type or None,
                "agent_id": agent_id,
                "project": project_name or None,
            },
        )
        return self._stringify(data)

    async def search_agent_conversation(
        self, agent_id: str, query: str, limit: int = 5, project_name: str = ""
    ) -> str:
        data = await self._post(
            "/search/conversation",
            {
                "namespace": self.namespace,
                "session_id": self._session_id("agent", agent_id=agent_id, project_name=project_name),
                "query": query,
                "limit": int(limit or 5),
                "agent_id": agent_id,
                "project": project_name or None,
            },
        )
        return self._stringify(data)

    async def session_end(self, scope: str, agent_id: str = "", project_name: str = "") -> None:
        await self._post(
            "/session/end",
            {
                "namespace": self.namespace,
                "session_id": self._session_id(scope, agent_id=agent_id, project_name=project_name),
            },
        )

    async def health(self) -> dict:
        """Best-effort gateway health check."""
        try:
            data = await self._get("/health")
            return {
                "configured": True,
                "ok": True,
                "base_url": self.base_url,
                "namespace": self.namespace,
                "detail": self._stringify(data) or "ok",
            }
        except Exception as e:
            return {
                "configured": True,
                "ok": False,
                "base_url": self.base_url,
                "namespace": self.namespace,
                "detail": str(e),
            }

    async def smoke_test(self) -> dict:
        """Run a minimal end-to-end check against recall/session-end."""
        status = await self.health()
        if not status["ok"]:
            return status
        scope = "smoke"
        try:
            text = await self.recall("lodestone memory smoke test", scope=scope)
            await self.session_end(scope)
            return {
                **status,
                "smoke_ok": True,
                "detail": text or status["detail"],
            }
        except Exception as e:
            return {
                **status,
                "ok": False,
                "smoke_ok": False,
                "detail": str(e),
            }


def build_memory_client(config):
    if not getattr(config, "memory_enabled", False):
        return None
    cfg = config.memory
    base_url = (cfg or {}).get("base_url") or "http://127.0.0.1:8420"
    return MemoryGatewayClient(
        base_url=base_url,
        api_key=(cfg or {}).get("api_key", ""),
        namespace=(cfg or {}).get("namespace", "lodestone"),
        timeout=(cfg or {}).get("timeout", 10),
    )
