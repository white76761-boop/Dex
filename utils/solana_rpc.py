import httpx
import itertools
from typing import Any, Dict, Optional

class SolanaRPC:
    def __init__(self, rpc_url: str, timeout: float = 20.0):
        self.rpc_url = rpc_url
        self._id = itertools.count(1)
        self.client = httpx.AsyncClient(timeout=timeout)

    async def call(self, method: str, params: list | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._id),
            "method": method,
            "params": params or [],
        }
        r = await self.client.post(self.rpc_url, json=payload)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data["result"]

    async def get_transaction(self, signature: str) -> Optional[Dict[str, Any]]:
        # Use jsonParsed for easier parsing
        res = await self.call("getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        return res

    async def get_signatures_for_address(self, address: str, limit: int = 20, before: str | None = None) -> list[dict]:
        params = [address, {"limit": limit}]
        if before:
            params[1]["before"] = before
        return await self.call("getSignaturesForAddress", params)

    async def close(self):
        await self.client.aclose()
